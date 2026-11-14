from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id

if TYPE_CHECKING:
    from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork


@dataclass(frozen=True, slots=True)
class CommandIdentity:
    key: str
    actor_ref: str
    operation: str
    target: str
    parameters_sha256: str
    body_fingerprint: str
    fingerprint_kind: Literal["sha256", "hmac_sha256"]  # internal enum


@dataclass(frozen=True, slots=True)
class StoredResult:
    receipt: AcceptedReceipt
    status: int
    resource_ref: str | None
    result_code: str
    safe_body: bytes | None


class IdempotencyStore(Protocol):
    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None: ...
    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None: ...


class ResultVisibility(Protocol):
    def authorize(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None: ...


def command_identity(
    *,
    key: str,
    actor_ref: str,
    operation: str,
    target: str,
    parameters_json: bytes,
    body_fingerprint: str,
    fingerprint_kind: Literal["sha256", "hmac_sha256"],
) -> CommandIdentity:
    require_id(key)
    require_id(actor_ref)
    if len(parameters_json) > 8388608:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        value: object = json.loads(parameters_json, object_pairs_hook=_pairs)
        digest = hashlib.sha256(canonical_json(value)).hexdigest()
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    return CommandIdentity(key, actor_ref, operation, target, digest, body_fingerprint, fingerprint_kind)


@dataclass(frozen=True, slots=True)
class AcceptedReceipt:
    command_key: str
    operation_id: str
    target: str
    receipt_id: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.accepted_at)


class CommandPolicy(Protocol):
    def requires_ledger(self, operation_id: str) -> bool: ...


class CommandReceipts(Protocol):
    def accepted(self, uow: UnitOfWork, identity: CommandIdentity) -> AcceptedReceipt: ...
    def read(
        self, view: ReadSnapshot | UnitOfWork, session: SessionContext, identity: CommandIdentity
    ) -> AcceptedReceipt: ...


@dataclass(frozen=True, slots=True)
class PendingCommand:
    intent_id: str
    identity: CommandIdentity
    resume_handler: str
    input_ref: str


class CommandIntents(Protocol):
    def reserve(
        self, uow: UnitOfWork, identity: CommandIdentity, *, resume_handler: str, input_ref: str
    ) -> PendingCommand: ...
    def pending(
        self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity
    ) -> PendingCommand | None: ...
    def complete(self, uow: UnitOfWork, intent: PendingCommand, result: StoredResult) -> None: ...


class CommandResumer(Protocol):
    def resume(self, session: SessionContext, intent: PendingCommand) -> StoredResult | None: ...


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def canonical_json(value: object) -> bytes:
    """RFC 8785 subset of I-JSON, iterative with E01 11 parser ceilings."""
    import json
    import math
    from decimal import Decimal

    stack: list[tuple[object, int]] = [(value, 0)]
    chunks: list[str] = []
    size = 0
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            raise ValueError("commands.max_depth ceiling exceeded")
        if isinstance(item, _Token):
            token = item.value
        elif item is None or isinstance(item, bool | str):
            token = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(item, int):
            if abs(item) > 9007199254740991:
                raise ValueError("integer outside I-JSON exact range")
            token = str(item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("nonfinite JSON number")
            token = _number(item, Decimal(repr(item)))
        elif isinstance(item, list | tuple):
            stack.append((_Token("]"), depth))
            for index in range(len(item) - 1, -1, -1):
                stack.append((item[index], depth + 1))
                if index:
                    stack.append((_Token(","), depth))
            token = "["
        elif isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise ValueError("JSON keys must be strings")
            keys = sorted(item, key=lambda k: str(k).encode("utf-16-be"))
            stack.append((_Token("}"), depth))
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                stack.extend([(item[key], depth + 1), (_Token(":"), depth), (key, depth + 1)])
                if index:
                    stack.append((_Token(","), depth))
            token = "{"
        else:
            raise ValueError("unsupported JSON type")
        size += len(token.encode("utf-8"))
        if size > 8388608:
            raise ValueError("commands.max_bytes ceiling exceeded")
        chunks.append(token)
    return "".join(chunks).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _Token:
    value: str


def _number(value: float, decimal: Decimal) -> str:
    if value == 0:
        return "0"
    if 1e-6 <= abs(value) < 1e21:
        return (
            format(decimal, "f").rstrip("0").rstrip(".")
            if isinstance(decimal.as_tuple().exponent, int) and int(decimal.as_tuple().exponent) < 0
            else format(decimal, "f")
        )
    mantissa, exponent = format(decimal, "e").split("e")
    exp = int(exponent)
    return mantissa + "e" + ("+" if exp >= 0 else "-") + str(abs(exp))
