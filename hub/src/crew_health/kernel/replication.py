from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.contracts_gen.enums import Role

if TYPE_CHECKING:
    from crew_health.kernel.uow import ReadSnapshot, ReplicationChange, UnitOfWork


@dataclass(frozen=True, slots=True)
class ProjectionKey:
    owner: str
    name: str
    version: int


@dataclass(frozen=True, slots=True)
class Recipient:
    recipient_id: str
    purpose: str
    policy_epoch: int
    roles: frozenset[Role]
    grant_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectionDependency:
    field_pointer: str
    record_ref: str | None
    disposition: Literal["included", "omitted", "required"]  # internal enum


@dataclass(frozen=True, slots=True)
class ProjectionBody:
    key: ProjectionKey
    object_ref: str
    selected_fields: tuple[str, ...]
    data_json: bytes
    dependencies: tuple[ProjectionDependency, ...]


@dataclass(frozen=True, slots=True)
class Delivery:
    stream_id: str
    policy_epoch: int
    delivery_seq: int
    recipient_id: str
    purpose: str
    source_node_id: str
    source_incarnation: str
    origin_token: str
    body: ProjectionBody
    envelope_sha256: str


class ProjectionProvider(Protocol):
    def export(
        self, snapshot: ReadSnapshot | UnitOfWork, change: ReplicationChange, recipient: Recipient
    ) -> ProjectionBody | None: ...


class ReplicaApplier(Protocol):
    def apply(self, uow: UnitOfWork, delivery: Delivery, recipient: Recipient) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectionRegistration:
    key: ProjectionKey
    schema_json: bytes
    schema_sha256: str
    provider: ProjectionProvider
    applier: ReplicaApplier


@dataclass(frozen=True, slots=True)
class RevocationControl:
    control_id: str
    recipient_id: str
    stream_id: str
    old_policy_epoch: int
    new_policy_epoch: int
    authority_ref: str
    signature: bytes


class ReplicaExchange(Protocol):
    def revoke(self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str) -> RevocationControl: ...
    def apply_revocation(self, uow: UnitOfWork, control: RevocationControl) -> str: ...
    def acknowledge_revocation(self, uow: UnitOfWork, *, control_id: str, recipient_id: str) -> None: ...
    def materialize(self, uow: UnitOfWork, recipient: Recipient, *, limit: int) -> int: ...
    def page(
        self, snapshot: ReadSnapshot, recipient: Recipient, *, stream_id: str, after: int, limit: int
    ) -> tuple[Delivery, ...]: ...
    def apply(self, uow: UnitOfWork, recipient: Recipient, delivery: Delivery) -> int: ...
    def acknowledge(
        self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str, through: int, envelope_sha256: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ReplicatedTable:
    """A module-owned table whose every write must declare replication in the same unit of work (SYSTEM 5.7),
    with the projection it is exported through and the leaf fields (JSON pointers) that projection may carry.
    A module declares it through ModuleRegistry.replicated_table; the projection allowlist comes only from here."""

    table: str
    record_type: str
    key_column: str
    projection: ProjectionKey | None = None
    allowed_fields: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ReplicationAllowlist:
    """Permitted leaf fields per projection, assembled from every module's ReplicatedTable declarations."""

    fields: Mapping[ProjectionKey, frozenset[str]]

    def allowed(self, key: ProjectionKey) -> frozenset[str]:
        return self.fields.get(key, frozenset())
