from __future__ import annotations

import json
import re
from datetime import timedelta

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.config_release_schema import ConfigRelease
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    PendingCommand,
    StoredResult,
    canonical_json,
)
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform.adapters import codec
from crew_health.platform.domain.commands import identity_bytes
from crew_health.platform.domain.errors import fail

_RESULT_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


def revision_reference(result: StoredResult) -> bool:
    """A replay pointer, not a response body: canonical `{"revision": n}` naming the revision of
    `resource_ref` the original response showed (SYSTEM 5.8: the owner rebuilds that revision from its own
    history). It carries no request or private text, so it is kept durably with the ledger row."""
    if (
        result.safe_body is None
        or result.resource_ref is None
        or not _RESULT_CODE.fullmatch(result.result_code)
    ):
        return False
    try:
        value = json.loads(result.safe_body)
    except ValueError:
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"revision"}
        and type(value["revision"]) is int
        and value["revision"] >= 1
        and canonical_json(value) == result.safe_body
    )


class CommandLedger:
    def __init__(self, ids: IdFactory, *, settings: HubSettings | None = None) -> None:
        self.ids, self.settings = ids, settings
        self.receipts = codec(AcceptedReceipt)

    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None:
        rows = uow.connection.rows(
            "SELECT identity, receipt, status, resource_ref, result_code, safe_body FROM platform_command_ledger WHERE actor_ref=? AND command_key=?",
            (identity.actor_ref, identity.key),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        if row[0] != identity_bytes(identity):
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        if not isinstance(row[1], bytes) or not isinstance(row[2], int) or not isinstance(row[4], str):
            raise fail(ErrorCode.INTERNAL_ERROR)
        resource, body = row[3], row[5]
        if body is None:
            cached = uow.connection.rows(
                "SELECT safe_body FROM platform_idempotency WHERE actor_ref=? AND command_key=? AND expires_at>=?",
                (identity.actor_ref, identity.key, uow.now.isoformat()),
                limit=1,
            )
            body = cached[0][0] if cached else None
        if (
            resource is not None
            and not isinstance(resource, str)
            or body is not None
            and not isinstance(body, bytes)
        ):
            raise fail(ErrorCode.INTERNAL_ERROR)
        return StoredResult(self.receipts.validate_json(row[1]), row[2], resource, row[4], body)

    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        existing = self.lookup(uow, identity)
        if existing is not None:
            if existing != result:
                raise fail(ErrorCode.DUPLICATE_COMMAND)
            return
        if result.resource_ref is not None:
            require_id(result.resource_ref)
        durable = result.safe_body if revision_reference(result) else None
        if result.safe_body is not None and durable is None:
            self._public_result(identity, result)
        elif durable is None and result.result_code not in {"accepted", "deleted", "cancelled"} | {
            code.value for code in ErrorCode
        }:
            raise fail(ErrorCode.INVALID_VALUE)
        receipt = result.receipt
        if (receipt.command_key, receipt.operation_id, receipt.target) != (
            identity.key,
            identity.operation,
            identity.target,
        ):
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        # Safe bodies require an explicitly authorized P0/P1 projection upstream.
        if identity.fingerprint_kind == "hmac_sha256" and result.safe_body is not None and durable is None:
            raise fail(ErrorCode.INVALID_VALUE)
        uow.connection.execute(
            "INSERT INTO platform_command_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity.actor_ref,
                identity.key,
                identity_bytes(identity),
                self.receipts.dump_json(receipt),
                result.status,
                result.resource_ref,
                result.result_code,
                durable,
            ),
        )
        if durable is None:
            self._cache(uow, identity, result)

    def _public_result(self, identity: CommandIdentity, result: StoredResult) -> None:
        if (
            identity.operation not in {"createConfigRelease", "activateConfigRelease"}
            or identity.fingerprint_kind != "sha256"
            or self.settings is None
            or result.safe_body is None
        ):
            raise fail(ErrorCode.INVALID_VALUE)
        body = ConfigRelease.model_validate_json(result.safe_body)
        if body.release_id.root != result.resource_ref:
            raise fail(ErrorCode.INVALID_VALUE)
        token = result.result_code
        pieces = token[1:-1].split(":") if token.startswith('"') and token.endswith('"') else []
        if (
            len(pieces) != 3
            or pieces[0] != result.resource_ref
            or not pieces[1].isdigit()
            or not pieces[2].isdigit()
            or int(pieces[1]) < 1
        ):
            raise fail(ErrorCode.INVALID_VALUE)

    def _cache(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        if result.safe_body is None:
            return
        if self.settings is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        maximum = self.settings.integer("commands.cache_entries")
        uow.connection.execute("DELETE FROM platform_idempotency WHERE expires_at<?", (uow.now.isoformat(),))
        count = uow.connection.rows("SELECT count(*) FROM platform_idempotency", (), limit=1)
        if int(str(count[0][0])) >= maximum:
            uow.connection.execute(
                "DELETE FROM platform_idempotency WHERE (actor_ref,command_key) IN (SELECT actor_ref,command_key FROM platform_idempotency ORDER BY expires_at,actor_ref,command_key LIMIT 1)"
            )
        uow.connection.execute(
            "INSERT INTO platform_idempotency VALUES (?,?,?,?)",
            (identity.actor_ref, identity.key, result.safe_body, (uow.now + timedelta(hours=24)).isoformat()),
        )

    def accepted(self, uow: UnitOfWork, identity: CommandIdentity) -> AcceptedReceipt:
        existing = self.lookup(uow, identity)
        if existing is not None:
            return existing.receipt
        return AcceptedReceipt(
            identity.key, identity.operation, identity.target, self.ids.new("receipt"), uow.now
        )

    def read(
        self, view: ReadSnapshot | UnitOfWork, session: SessionContext, identity: CommandIdentity
    ) -> AcceptedReceipt:
        if session.actor.id != identity.actor_ref:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        rows = view.connection.rows(
            "SELECT identity, receipt FROM platform_command_ledger WHERE actor_ref=? AND command_key=?",
            (identity.actor_ref, identity.key),
            limit=1,
        )
        if not rows:
            raise fail(ErrorCode.NOT_FOUND)
        if rows[0][0] != identity_bytes(identity):
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        raw = rows[0][1]
        if not isinstance(raw, bytes):
            raise fail(ErrorCode.INTERNAL_ERROR)
        return self.receipts.validate_json(raw)

    def pending(self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity) -> PendingCommand | None:
        from crew_health.platform.adapters.intents import IntentRepository

        return IntentRepository(self).pending(view, identity)
