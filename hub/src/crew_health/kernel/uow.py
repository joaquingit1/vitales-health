from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from crew_health.contracts_gen.enums import EventType, Role
from crew_health.kernel.clock import require_utc

if TYPE_CHECKING:
    from crew_health.kernel.audit import AuditEntry
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.kernel.config import ReleaseHandle
    from crew_health.kernel.replication import ProjectionKey
    from crew_health.kernel.scheduler import ScheduledJob
    from crew_health.kernel.storage import StorageSession


@dataclass(frozen=True, slots=True)
class Actor:
    kind: Literal[
        "person", "station", "ground", "operator", "adapter", "engine", "assistant"
    ]  # internal enum
    id: str


class DomainEvent(Protocol):
    event_type: ClassVar[str]


@dataclass(frozen=True, slots=True)
class AudienceProjectionKey:
    owner: str
    name: str
    version: int


@dataclass(frozen=True, slots=True)
class Audience:
    rule: str
    object_ref: str
    projection: AudienceProjectionKey
    policy_version: str


@dataclass(frozen=True, slots=True)
class EventScope:
    kind: Literal["person", "habitat", "crew", "ground", "operator"]  # internal enum
    person_id: str | None
    environment_id: str | None


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    type: EventType
    occurred_at: datetime
    scope: EventScope
    config_release_id: str
    correlation_id: str | None
    data_json: bytes
    audience: Audience

    def __post_init__(self) -> None:
        require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class StoredEvent:
    seq: int
    event_id: str
    type: EventType
    occurred_at: datetime
    emitted_at: datetime
    scope: EventScope
    config_release_id: str
    correlation_id: str | None
    data_json: bytes
    audience: Audience

    def __post_init__(self) -> None:
        require_utc(self.occurred_at)
        require_utc(self.emitted_at)


class UnitOfWork(Protocol):
    @property
    def connection(self) -> StorageSession: ...
    @property
    def now(self) -> datetime: ...
    @property
    def actor(self) -> Actor: ...
    @property
    def release(self) -> ReleaseHandle: ...
    @property
    def correlation_id(self) -> str: ...
    def publish(self, event: DomainEvent) -> None: ...
    def emit(self, event: OutboxEvent) -> StoredEvent: ...
    def audit(self, entry: AuditEntry) -> None: ...
    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int: ...
    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool: ...
    def replicate(self, change: ReplicationChange) -> ReplicationRef: ...
    def savepoint(self, name: str) -> AbstractContextManager[None]: ...


class UnitOfWorkFactory(Protocol):
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> AbstractContextManager[UnitOfWork]: ...


class ReadSnapshot(Protocol):
    @property
    def connection(self) -> StorageSession: ...
    @property
    def release(self) -> ReleaseHandle: ...
    @property
    def now(self) -> datetime: ...


class ReadSnapshotFactory(Protocol):
    def snapshot(self) -> AbstractContextManager[ReadSnapshot]: ...


class DomainEventBus(Protocol):
    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None: ...
    def publish(self, uow: UnitOfWork, event: DomainEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: str
    kind: Literal["crew", "station", "ground", "operator", "editor", "adapter"]  # internal enum
    actor: Actor
    person_id: str | None
    roles: frozenset[Role]


class AudienceRule(Protocol):
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool: ...


@dataclass(frozen=True, slots=True)
class LocalProjectionRegistration:
    key: AudienceProjectionKey
    payload_schema_json: bytes
    required_field_pointers: tuple[str, ...]
    rule: AudienceRule


class OutboxSubscriptions(Protocol):
    def register(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ReplicationChange:
    export_projection: ProjectionKey | None
    record_type: str
    record_id: str
    operation: Literal["create", "version", "append", "retract"]  # internal enum
    payload_json: bytes
    causal_refs: tuple[str, ...]
    release_id: str
    audience: Audience


@dataclass(frozen=True, slots=True)
class ReplicationRef:
    origin_node_id: str
    origin_incarnation: str
    origin_seq: int
