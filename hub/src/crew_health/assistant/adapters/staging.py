"""Readonly owner calls outside SQLite's writer; only bounded safe audits may be staged."""

from contextlib import AbstractContextManager
from datetime import datetime
from threading import Lock

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    ReplicationRef,
    StoredEvent,
    UnitOfWork,
)


class ReadStage:
    def __init__(
        self,
        view: ReadSnapshot,
        *,
        release: ReleaseHandle,
        actor: Actor,
        correlation_id: str,
        max_audits: int,
        max_entry_bytes: int,
    ) -> None:
        self.view, self.pin, self.by, self.correlation = view, release, actor, correlation_id
        if (
            type(max_audits) is not int
            or type(max_entry_bytes) is not int
            or min(max_audits, max_entry_bytes) < 1
        ):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "assistant staging bounds invalid")
        self.max_audits, self.max_entry_bytes = max_audits, max_entry_bytes
        self._entries: list[AuditEntry] = []
        self._closed = False
        self._lock = Lock()

    @property
    def connection(self) -> StorageSession:
        with self._lock:
            self._check()
            return self.view.connection

    @property
    def release(self) -> ReleaseHandle:
        return self.pin

    @property
    def actor(self) -> Actor:
        return self.by

    @property
    def now(self) -> datetime:
        return self.view.now

    @property
    def correlation_id(self) -> str:
        return self.correlation

    def audit(self, entry: AuditEntry) -> None:
        with self._lock:
            self._check()
            if entry.actor != self.actor or entry.correlation_id != self.correlation_id:
                raise ProblemError(ErrorCode.IDENTITY_MISMATCH, "assistant staging identity mismatch")
            size = _audit_size(entry, self.max_entry_bytes)
            if len(self._entries) >= self.max_audits or size > self.max_entry_bytes:
                raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant staging bound exceeded")
            self._entries.append(entry)

    def finish(self) -> tuple[AuditEntry, ...]:
        with self._lock:
            self._check()
            self._closed = True
            entries = tuple(self._entries)
            self._entries.clear()
            return entries

    def discard(self) -> None:
        with self._lock:
            self._closed = True
            self._entries.clear()

    def _check(self) -> None:
        if self._closed:
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight closed")

    def publish(self, event: DomainEvent) -> None:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")

    def emit(self, event: OutboxEvent) -> StoredEvent:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")

    def savepoint(self, name: str) -> AbstractContextManager[None]:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant preflight is readonly")


class PinnedWork:
    def __init__(self, work: UnitOfWork, release: ReleaseHandle) -> None:
        self.work, self.pin = work, release

    @property
    def release(self) -> ReleaseHandle:
        return self.pin

    @property
    def connection(self) -> StorageSession:
        return self.work.connection

    @property
    def actor(self) -> Actor:
        return self.work.actor

    @property
    def now(self) -> datetime:
        return self.work.now

    @property
    def correlation_id(self) -> str:
        return self.work.correlation_id

    def audit(self, entry: AuditEntry) -> None:
        self.work.audit(entry)

    def publish(self, event: DomainEvent) -> None:
        self.work.publish(event)

    def emit(self, event: OutboxEvent) -> StoredEvent:
        return self.work.emit(event)

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        return self.work.schedule(job, expected_generation=expected_generation)

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        return self.work.cancel_schedule(key, expected_generation=expected_generation)

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        return self.work.replicate(change)

    def savepoint(self, name: str) -> AbstractContextManager[None]:
        return self.work.savepoint(name)


def _audit_size(entry: AuditEntry, maximum: int) -> int:
    values: list[object] = [
        entry.event_id,
        entry.event_type,
        entry.actor.kind,
        entry.actor.id,
        entry.correlation_id,
    ]
    if entry.causation_id is not None:
        values.append(entry.causation_id)
    if len(entry.refs) + len(entry.safe_metadata) > maximum:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant staging bound exceeded")
    for mapping in (entry.refs, entry.safe_metadata):
        for key, value in mapping.items():
            values.extend((key, value))
    size = 0
    for item in values:
        if not isinstance(item, str | int | bool):
            raise ProblemError(ErrorCode.INVALID_VALUE, "assistant audit data invalid")
        text = str(item)
        if len(text) > maximum:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant staging bound exceeded")
        size += len(text.encode("utf-8"))
        if size > maximum:
            break
    if size > maximum:
        return size
    return len(
        canonical_json(
            {
                "event_id": entry.event_id,
                "event_type": entry.event_type,
                "event_version": entry.event_version,
                "actor": {"kind": entry.actor.kind, "id": entry.actor.id},
                "refs": dict(entry.refs),
                "correlation_id": entry.correlation_id,
                "causation_id": entry.causation_id,
                "safe_metadata": dict(entry.safe_metadata),
            }
        )
    )
