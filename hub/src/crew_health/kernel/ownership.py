from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


@dataclass(frozen=True, slots=True)
class OwnedResource:
    kind: str
    resource_id: str
    owner_node_id: str
    owner_incarnation: str
    epoch: int
    revision: int


@dataclass(frozen=True, slots=True)
class Transfer:
    transfer_id: str
    source: OwnedResource
    destination_node_id: str
    destination_incarnation: str
    new_epoch: int
    snapshot_ref: str
    snapshot_sha256: str
    command: CommandIdentity


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    transfer_id: str
    phase: Literal["prepared", "fenced", "accepted", "completed", "cancelled"]  # internal enum
    certificate_json: bytes


class Ownership(Protocol):
    def require(self, uow: UnitOfWork, resource: OwnedResource) -> None: ...
    def prepare(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt: ...
    def fence(self, uow: UnitOfWork, transfer: Transfer, prepared: TransferReceipt) -> TransferReceipt: ...
    def accept(self, uow: UnitOfWork, transfer: Transfer, fenced: TransferReceipt) -> TransferReceipt: ...
    def complete(self, uow: UnitOfWork, transfer: Transfer, accepted: TransferReceipt) -> TransferReceipt: ...
    def cancel(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt: ...
    def status(self, snapshot: ReadSnapshot, transfer_id: str) -> TransferReceipt: ...
