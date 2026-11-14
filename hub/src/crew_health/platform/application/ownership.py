from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.ownership import OwnedResource, Transfer, TransferReceipt
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.platform.application.uow import codec
from crew_health.platform.domain.errors import fail


class OwnershipStore(Protocol):
    def head(
        self, view: ReadSnapshot | UnitOfWork, kind: str, resource_id: str
    ) -> tuple[OwnedResource, bool] | None: ...
    def transfer(
        self, view: ReadSnapshot | UnitOfWork, transfer_id: str
    ) -> tuple[Transfer, TransferReceipt] | None: ...


@dataclass(frozen=True)
class OwnershipHooks:
    validate_snapshot: Callable[[UnitOfWork, Transfer], None]
    install: Callable[[UnitOfWork, Transfer], None]
    fence: Callable[[UnitOfWork, Transfer], None]


class OwnershipService:
    def __init__(
        self,
        *,
        node_id: str,
        incarnation: str,
        ids: IdFactory,
        hooks: Mapping[str, OwnershipHooks],
        repository: OwnershipStore,
        sign: Callable[[bytes], bytes],
        verify: Callable[[bytes, bytes, str], bool],
    ) -> None:
        self.node_id, self.incarnation, self.ids = node_id, incarnation, ids
        self.hooks, self.sign, self.verify = dict(hooks), sign, verify
        self.repository = repository
        self.head_codec, self.transfer_codec = codec(OwnedResource), codec(Transfer)

    def require(self, uow: UnitOfWork, resource: OwnedResource) -> None:
        current = self.repository.head(uow, resource.kind, resource.resource_id)
        if (
            current is None
            or current[0] != resource
            or current[1]
            or (resource.owner_node_id, resource.owner_incarnation) != (self.node_id, self.incarnation)
        ):
            raise fail(ErrorCode.STALE_VERSION)

    def seed(self, uow: UnitOfWork, resource: OwnedResource) -> None:
        if resource.kind not in self.hooks or resource.epoch < 1 or resource.revision < 1:
            raise fail(ErrorCode.CONFIG_INVALID)
        if self.repository.head(uow, resource.kind, resource.resource_id) is not None:
            raise fail(ErrorCode.STALE_VERSION)
        uow.connection.execute(
            "INSERT INTO platform_ownership VALUES (?,?,?,0)",
            (resource.kind, resource.resource_id, self.head_codec.dump_json(resource)),
        )

    def _validate(self, transfer: Transfer, *, destination: bool) -> None:
        participant = (
            (transfer.destination_node_id, transfer.destination_incarnation)
            if destination
            else (transfer.source.owner_node_id, transfer.source.owner_incarnation)
        )
        if (
            participant != (self.node_id, self.incarnation)
            or transfer.source.kind not in self.hooks
            or transfer.new_epoch <= transfer.source.epoch
            or len(transfer.snapshot_sha256) != 64
        ):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if (transfer.source.owner_node_id, transfer.source.owner_incarnation) == (
            transfer.destination_node_id,
            transfer.destination_incarnation,
        ):
            raise fail(ErrorCode.INVALID_TRANSITION)

    def _certificate(self, transfer: Transfer, phase: str) -> bytes:
        payload = canonical_json(
            {
                "transfer": self.transfer_codec.dump_json(transfer).decode(),
                "phase": phase,
                "issuer": self.node_id,
            }
        )
        return canonical_json({"payload": payload.decode(), "signature": self.sign(payload).hex()})

    def _verify(self, transfer: Transfer, receipt: TransferReceipt, phase: str, issuer: str) -> None:
        if receipt.transfer_id != transfer.transfer_id or receipt.phase != phase:
            raise fail(ErrorCode.INVALID_TRANSITION)
        try:
            outer: object = json.loads(receipt.certificate_json)
            if (
                not isinstance(outer, dict)
                or set(outer) != {"payload", "signature"}
                or not isinstance(outer["payload"], str)
                or not isinstance(outer["signature"], str)
            ):
                raise ValueError("certificate")
            payload = outer["payload"].encode()
            expected = canonical_json(
                {
                    "transfer": self.transfer_codec.dump_json(transfer).decode(),
                    "phase": phase,
                    "issuer": issuer,
                }
            )
            if payload != expected or not self.verify(payload, bytes.fromhex(outer["signature"]), issuer):
                raise ValueError("signature")
        except ValueError, UnicodeError:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE) from None

    def _prior(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt | None:
        prior = self.repository.transfer(uow, transfer.transfer_id)
        if prior is not None and prior[0] != transfer:
            raise fail(ErrorCode.DUPLICATE_COMMAND)
        return prior[1] if prior else None

    def _store(
        self,
        uow: UnitOfWork,
        transfer: Transfer,
        phase: Literal["prepared", "fenced", "accepted", "completed", "cancelled"],
    ) -> TransferReceipt:
        competitors = uow.connection.rows(
            "SELECT transfer_id FROM platform_transfer WHERE resource_kind=? AND resource_id=? AND epoch=?",
            (transfer.source.kind, transfer.source.resource_id, transfer.new_epoch),
            limit=1,
        )
        if competitors and competitors[0][0] != transfer.transfer_id:
            raise fail(ErrorCode.STALE_VERSION)
        certificate = self._certificate(transfer, phase)
        uow.connection.execute(
            "INSERT INTO platform_transfer VALUES (?,?,?,?,?,?,?) ON CONFLICT(transfer_id) DO UPDATE SET phase=excluded.phase, certificate=excluded.certificate",
            (
                transfer.transfer_id,
                self.transfer_codec.dump_json(transfer),
                phase,
                certificate,
                transfer.source.kind,
                transfer.source.resource_id,
                transfer.new_epoch,
            ),
        )
        uow.replicate(
            ReplicationChange(
                None,
                "ownership",
                transfer.transfer_id,
                "version",
                canonical_json({"transfer_id": transfer.transfer_id, "phase": phase}),
                (),
                uow.release.release_id,
                Audience(
                    "ownership",
                    transfer.source.resource_id,
                    AudienceProjectionKey("platform", "ownership", 1),
                    "1",
                ),
            )
        )
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                "ownership." + phase,
                1,
                uow.actor,
                {"transfer": transfer.transfer_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        return TransferReceipt(transfer.transfer_id, phase, certificate)

    def prepare(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt:
        self._validate(transfer, destination=True)
        prior = self._prior(uow, transfer)
        if prior is not None:
            if prior.phase == "cancelled":
                raise fail(ErrorCode.INVALID_TRANSITION)
            return prior
        head = self.repository.head(uow, transfer.source.kind, transfer.source.resource_id)
        if head is not None and head[0].epoch >= transfer.new_epoch:
            raise fail(ErrorCode.STALE_VERSION)
        self.hooks[transfer.source.kind].validate_snapshot(uow, transfer)
        return self._store(uow, transfer, "prepared")

    def fence(self, uow: UnitOfWork, transfer: Transfer, prepared: TransferReceipt) -> TransferReceipt:
        self._validate(transfer, destination=False)
        self._verify(transfer, prepared, "prepared", transfer.destination_node_id)
        prior = self._prior(uow, transfer)
        if prior is not None and prior.phase in ("fenced", "completed"):
            return prior
        if prior is not None and prior.phase == "cancelled":
            raise fail(ErrorCode.INVALID_TRANSITION)
        self.require(uow, transfer.source)
        self.hooks[transfer.source.kind].validate_snapshot(uow, transfer)
        self.hooks[transfer.source.kind].fence(uow, transfer)
        uow.connection.execute(
            "UPDATE platform_ownership SET fenced=1 WHERE kind=? AND resource_id=?",
            (transfer.source.kind, transfer.source.resource_id),
        )
        return self._store(uow, transfer, "fenced")

    def accept(self, uow: UnitOfWork, transfer: Transfer, fenced: TransferReceipt) -> TransferReceipt:
        self._validate(transfer, destination=True)
        self._verify(transfer, fenced, "fenced", transfer.source.owner_node_id)
        prior = self._prior(uow, transfer)
        if prior is None or prior.phase not in ("prepared", "accepted"):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if prior.phase == "accepted":
            return prior
        head = self.repository.head(uow, transfer.source.kind, transfer.source.resource_id)
        if head is not None and head[0].epoch >= transfer.new_epoch:
            raise fail(ErrorCode.STALE_VERSION)
        self.hooks[transfer.source.kind].validate_snapshot(uow, transfer)
        self.hooks[transfer.source.kind].install(uow, transfer)
        owned = replace(
            transfer.source,
            owner_node_id=self.node_id,
            owner_incarnation=self.incarnation,
            epoch=transfer.new_epoch,
        )
        uow.connection.execute(
            "INSERT INTO platform_ownership VALUES (?,?,?,0) ON CONFLICT(kind,resource_id) DO UPDATE SET head=excluded.head,fenced=0",
            (owned.kind, owned.resource_id, self.head_codec.dump_json(owned)),
        )
        return self._store(uow, transfer, "accepted")

    def complete(self, uow: UnitOfWork, transfer: Transfer, accepted: TransferReceipt) -> TransferReceipt:
        self._validate(transfer, destination=False)
        self._verify(transfer, accepted, "accepted", transfer.destination_node_id)
        prior = self._prior(uow, transfer)
        if prior is None or prior.phase not in ("fenced", "completed"):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if prior.phase == "completed":
            return prior
        head = self.repository.head(uow, transfer.source.kind, transfer.source.resource_id)
        if head is None or head[0] != transfer.source or not head[1]:
            raise fail(ErrorCode.STALE_VERSION)
        owned = replace(
            transfer.source,
            owner_node_id=transfer.destination_node_id,
            owner_incarnation=transfer.destination_incarnation,
            epoch=transfer.new_epoch,
        )
        uow.connection.execute(
            "UPDATE platform_ownership SET head=?,fenced=1 WHERE kind=? AND resource_id=?",
            (self.head_codec.dump_json(owned), owned.kind, owned.resource_id),
        )
        return self._store(uow, transfer, "completed")

    def cancel(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt:
        prior = self._prior(uow, transfer)
        if prior is None and (transfer.source.owner_node_id, transfer.source.owner_incarnation) == (
            self.node_id,
            self.incarnation,
        ):
            self._validate(transfer, destination=False)
            self.require(uow, transfer.source)
            return self._store(uow, transfer, "cancelled")
        if prior is None or prior.phase not in ("prepared", "cancelled"):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if prior.phase == "cancelled":
            return prior
        return self._store(uow, transfer, "cancelled")

    def status(self, snapshot: ReadSnapshot, transfer_id: str) -> TransferReceipt:
        prior = self.repository.transfer(snapshot, transfer_id)
        if prior is None:
            raise fail(ErrorCode.NOT_FOUND)
        return prior[1]
