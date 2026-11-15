from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import (
    JobResultRef,
    TaskInput,
    TaskLease,
    TaskRegistration,
    TaskResult,
    TaskStatus,
)
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import (
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
)
from crew_health.platform.domain.errors import fail


@dataclass(frozen=True, slots=True)
class TaskRow:
    task: TaskInput
    status: TaskStatus
    generation: int
    attempts: int
    worker_id: str | None
    deadline: ElapsedDeadline | None

    def lease(self) -> TaskLease | None:
        if self.worker_id is None or self.deadline is None or self.status.state != "running":
            return None
        return TaskLease(self.task, self.generation, self.worker_id, self.deadline)


class TaskStore(Protocol):
    def get(self, view: ReadSnapshot | UnitOfWork, task_id: str) -> TaskRow | None: ...
    def pending_count(self, uow: UnitOfWork) -> int: ...
    def insert(self, uow: UnitOfWork, task: TaskInput) -> TaskRow: ...
    def update(self, uow: UnitOfWork, previous: TaskRow, row: TaskRow) -> None: ...
    def attempt(self, uow: UnitOfWork, lease: TaskLease) -> None: ...
    def page(
        self, view: ReadSnapshot | UnitOfWork, *, state: str, after_id: str, limit: int
    ) -> tuple[TaskRow, ...]: ...
    def task_size(self, task: TaskInput) -> int: ...
    def result_size(self, result: TaskResult) -> int: ...


class TaskService:
    def __init__(
        self,
        *,
        settings: HubSettings,
        repository: TaskStore,
        ids: IdFactory,
        registrations: Sequence[TaskRegistration],
        authorize: Callable[[ReadSnapshot | UnitOfWork, SessionContext, TaskInput], bool],
        authorize_completion: Callable[[UnitOfWork, TaskInput], bool],
        capture: Callable[[datetime], TimeInput],
        elapsed: Callable[[ReadSnapshot, TimeInput, TimeInput], timedelta | None],
        public_payload: Callable[[TaskStatus, str], bytes] | None = None,
    ) -> None:
        self.settings, self.ids = settings, ids
        self.authorize, self.authorize_completion, self.capture, self.elapsed = (
            authorize,
            authorize_completion,
            capture,
            elapsed,
        )
        self.registrations = {(r.name, r.version): r for r in registrations}
        self.repository, self.public_payload = repository, public_payload
        if len(self.registrations) != len(registrations) or len(registrations) > settings.integer(
            "registry.max_hooks"
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        for registration in registrations:
            if (
                not registration.version
                or registration.public_kind
                not in (None, "assessment", "report_extraction", "participation_rebuild")
                or (
                    registration.public_kind is not None
                    and (registration.result_projector is None or public_payload is None)
                )
                or (registration.public_kind is None and registration.result_projector is not None)
            ):
                raise fail(ErrorCode.CONFIG_INVALID)

    def submit(self, uow: UnitOfWork, task: TaskInput) -> TaskStatus:
        require_id(task.task_id)
        require_id(task.input_ref)
        require_id(task.release_id)
        if task.actor != uow.actor or task.command.actor_ref != task.actor.id:
            raise fail(ErrorCode.IDENTITY_MISMATCH)
        if not self.authorize_completion(uow, task):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        row = self.repository.get(uow, task.task_id)
        if row is not None:
            if row.task != task:
                raise fail(ErrorCode.DUPLICATE_COMMAND)
            return row.status
        registration = self.registrations.get((task.handler, task.handler_version))
        if registration is None or registration.public_kind != task.public_kind:
            raise fail(ErrorCode.CONFIG_INVALID)
        if task.public_kind is not None and task.actor.kind not in ("person", "operator"):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if not re.fullmatch(r"[a-f0-9]{64}", task.input_fingerprint):
            raise fail(ErrorCode.INVALID_VALUE)
        if self.repository.task_size(task) > self.settings.integer("tasks.max_input_bytes"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        if self.repository.pending_count(uow) >= self.settings.integer("tasks.max_pending"):
            raise fail(ErrorCode.INTERNAL_ERROR)
        row = self.repository.insert(uow, task)
        self._effects(uow, row, "task.submitted")
        return row.status

    def _visible(self, view: ReadSnapshot | UnitOfWork, session: SessionContext, task_id: str) -> TaskRow:
        row = self.repository.get(view, task_id)
        if row is None:
            raise fail(ErrorCode.NOT_FOUND)
        if not self.authorize(view, session, row.task):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return row

    def cancel(self, uow: UnitOfWork, session: SessionContext, task_id: str) -> TaskStatus:
        row = self._visible(uow, session, task_id)
        if row.status.state in ("succeeded", "failed", "cancelled"):
            return row.status
        return self._cancel(uow, row)

    def _cancel(self, uow: UnitOfWork, row: TaskRow) -> TaskStatus:
        cancelled = replace(
            row,
            generation=row.generation + 1,
            worker_id=None,
            deadline=None,
            status=replace(row.status, state="cancelled", updated_at=uow.now),
        )
        self.repository.update(uow, row, cancelled)
        self._effects(uow, cancelled, "task.cancelled")
        return cancelled.status

    def claim(
        self, uow: UnitOfWork, *, worker_id: str, handlers: frozenset[str], limit: int
    ) -> tuple[TaskLease, ...]:
        self._bound(limit)
        require_id(worker_id)
        if len(handlers) > self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        leases: list[TaskLease] = []
        after = ""
        for _ in range(self.settings.integer("tasks.max_pending")):
            rows = self.repository.page(
                uow,
                state="queued",
                after_id=after,
                limit=min(limit, self.settings.integer("storage.max_rows")),
            )
            if not rows:
                break
            for row in rows:
                after = row.task.task_id
                if row.task.handler not in handlers or not self._ready(uow, row):
                    continue
                if row.attempts >= self.settings.integer("tasks.max_attempts"):
                    continue
                deadline = ElapsedDeadline(
                    self.capture(uow.now), timedelta(milliseconds=self.settings.integer("tasks.lease_ms"))
                )
                running = replace(
                    row,
                    generation=row.generation + 1,
                    attempts=row.attempts + 1,
                    worker_id=worker_id,
                    deadline=deadline,
                    status=replace(row.status, state="running", error_code=None, updated_at=uow.now),
                )
                lease = TaskLease(row.task, running.generation, worker_id, deadline)
                self.repository.update(uow, row, running)
                self.repository.attempt(uow, lease)
                self._effects(uow, running, "task.claimed")
                leases.append(lease)
                if len(leases) == limit:
                    return tuple(leases)
        return tuple(leases)

    def _ready(self, view: ReadSnapshot, row: TaskRow) -> bool:
        if row.deadline is None:
            return True
        duration = self.elapsed(view, row.deadline.anchor, self.capture(view.now))
        return duration is not None and duration >= row.deadline.duration

    def _live(self, view: ReadSnapshot, lease: TaskLease) -> TaskRow | None:
        row = self.repository.get(view, lease.task.task_id)
        if row is None or row.lease() != lease:
            return None
        interval = self.elapsed(view, lease.expires.anchor, self.capture(view.now))
        if interval is None or interval < timedelta(0) or interval >= lease.expires.duration:
            return None
        return row

    def progress(self, uow: UnitOfWork, lease: TaskLease, value: Quantity) -> bool:
        if value.unit != Unit("1") or not Decimal(0) <= value.value <= Decimal(1):
            raise fail(ErrorCode.INVALID_VALUE)
        row = self._live(uow, lease)
        if row is None:
            return False
        if not self.authorize_completion(uow, row.task):
            self._cancel(uow, row)
            return False
        updated = replace(row, status=replace(row.status, progress=value, updated_at=uow.now))
        self.repository.update(uow, row, updated)
        self._effects(uow, updated, "task.progressed")
        return True

    def complete(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> bool:
        row = self._live(uow, lease)
        if row is None or result.task_id != lease.task.task_id or result.generation != lease.generation:
            return False
        if not self.authorize_completion(uow, row.task):
            self._cancel(uow, row)
            return False
        require_id(result.result_ref)
        if not re.fullmatch(r"[a-f0-9]{64}", result.result_fingerprint):
            raise fail(ErrorCode.INVALID_VALUE)
        if self.repository.result_size(result) > self.settings.integer("tasks.max_result_bytes"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        registration = self._registration(row.task)
        registration.handler.apply(uow, lease, result)
        completed = replace(
            row,
            worker_id=None,
            deadline=None,
            status=replace(
                row.status,
                state="succeeded",
                progress=Quantity(Decimal(1), Unit("1")),
                result_ref=result.result_ref,
                error_code=None,
                updated_at=uow.now,
            ),
        )
        self.repository.update(uow, row, completed)
        self._effects(uow, completed, "task.succeeded")
        return True

    def fail_with_result(
        self, uow: UnitOfWork, lease: TaskLease, result: TaskResult, *, code: ErrorCode
    ) -> bool:
        row = self._live(uow, lease)
        if row is None or result.task_id != lease.task.task_id or result.generation != lease.generation:
            return False
        if not self.authorize_completion(uow, row.task):
            self._cancel(uow, row)
            return False
        require_id(result.result_ref)
        if not re.fullmatch(r"[a-f0-9]{64}", result.result_fingerprint):
            raise fail(ErrorCode.INVALID_VALUE)
        if self.repository.result_size(result) > self.settings.integer("tasks.max_result_bytes"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        self._registration(row.task).handler.apply(uow, lease, result)
        failed = replace(
            row,
            generation=row.generation + 1,
            worker_id=None,
            deadline=None,
            status=replace(
                row.status,
                state="failed",
                result_ref=result.result_ref,
                error_code=code,
                updated_at=uow.now,
            ),
        )
        self.repository.update(uow, row, failed)
        self._effects(uow, failed, "task.failed")
        return True

    def fail_pending(self, uow: UnitOfWork, task_id: str, *, code: ErrorCode) -> bool:
        row = self.repository.get(uow, require_id(task_id))
        if row is None or row.status.state != "queued":
            return False
        if not self.authorize_completion(uow, row.task):
            self._cancel(uow, row)
            return False
        self._retry(uow, row, code=code, retryable=False)
        return True

    def fail(self, uow: UnitOfWork, lease: TaskLease, *, code: ErrorCode, retryable: bool) -> bool:
        row = self._live(uow, lease)
        if row is None:
            return False
        self._retry(uow, row, code=code, retryable=retryable)
        return True

    def _retry(self, uow: UnitOfWork, row: TaskRow, *, code: ErrorCode, retryable: bool) -> None:
        retry = retryable and row.attempts < self.settings.integer("tasks.max_attempts")
        deadline = (
            ElapsedDeadline(
                self.capture(uow.now), timedelta(milliseconds=self.settings.integer("tasks.retry_backoff_ms"))
            )
            if retry
            else None
        )
        updated = replace(
            row,
            generation=row.generation + 1,
            worker_id=None,
            deadline=deadline,
            status=replace(
                row.status, state="queued" if retry else "failed", error_code=code, updated_at=uow.now
            ),
        )
        self.repository.update(uow, row, updated)
        self._effects(uow, updated, "task.retry_queued" if retry else "task.failed")

    def recover(self, uow: UnitOfWork, *, limit: int) -> int:
        self._bound(limit)
        after, count = "", 0
        for _ in range(self.settings.integer("tasks.max_pending")):
            rows = self.repository.page(
                uow,
                state="running",
                after_id=after,
                limit=min(limit, self.settings.integer("storage.max_rows")),
            )
            if not rows:
                break
            for row in rows:
                after = row.task.task_id
                if self._ready(uow, row):
                    self._retry(uow, row, code=ErrorCode.INTERNAL_ERROR, retryable=True)
                    count += 1
                    if count == limit:
                        return count
        return count

    def get(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> TaskStatus:
        row = self.repository.get(snapshot, job_id)
        if row is None or row.status.job_id is None:
            raise fail(ErrorCode.NOT_FOUND)
        return self._visible(snapshot, session, job_id).status

    def result(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> JobResultRef | None:
        status = self.get(snapshot, session, job_id)
        if status.result_ref is None:
            return None
        row = self._visible(snapshot, session, job_id)
        projector = self._registration(row.task).result_projector
        return None if projector is None else projector.project(snapshot, session, status.result_ref)

    def _registration(self, task: TaskInput) -> TaskRegistration:
        registration = self.registrations.get((task.handler, task.handler_version))
        if registration is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        return registration

    def _bound(self, limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= self.settings.integer("tasks.claim_batch"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)

    def _effects(self, uow: UnitOfWork, row: TaskRow, event_type: str) -> None:
        uow.replicate(
            ReplicationChange(
                None,
                "task",
                row.task.task_id,
                "append",
                canonical_json({"task_id": row.task.task_id}),
                (),
                row.task.release_id,
                row.task.audience,
            )
        )
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                event_type,
                1,
                uow.actor,
                {"task": row.task.task_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        if row.status.job_id is not None:
            if self.public_payload is None:
                raise fail(ErrorCode.CONFIG_INVALID)

            scope = (
                EventScope("person", row.task.actor.id, None)
                if row.task.actor.kind == "person"
                else EventScope("operator", None, None)
            )
            uow.emit(
                OutboxEvent(
                    EventType.JOB_PROGRESS,
                    uow.now,
                    scope,
                    row.task.release_id,
                    uow.correlation_id,
                    self.public_payload(row.status, uow.correlation_id),
                    row.task.audience,
                )
            )
