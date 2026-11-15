"""The camera's worker calls as E01 tasks (SYSTEM 5.10.1, M6): `camera.start`, `camera.cancel` and `camera.report`.

Each handler reads its intent, makes one bounded call outside any unit of work (`execute`) and applies the answer in
the runner's unit of work (`apply`), guarded by the intent's current state so a newer state always wins. A worker or
E05 that does not answer raises `Unanswered`; the runner fails the lease as retryable and E01's queue retries it under
`tasks.max_attempts` and `tasks.retry_backoff_ms`. What the queue gives up on, the reconciliation reports at the
intent's deadline (dispatch.reconcile), so nothing waits forever.

`CameraTasks.drain` claims only these handlers through E01's TaskRunner. It is the runner for them until E01's
supervisor runs registered tasks for every module (fix/e01-composition); it never touches another module's task except
through `recover`, which is E01's own lease recovery.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace

from crew_health.camera.application.dispatch import (
    CANCEL_TASK,
    ENGINE,
    REPORT_TASK,
    START_TASK,
    DispatchLimits,
    DispatchStore,
    Intent,
    deadline_after_dispatch,
    submit,
)
from crew_health.camera.application.link import Probe, WorkerLink
from crew_health.camera.application.ports import AttemptReports
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.tasks import (
    TaskHandler,
    TaskLease,
    TaskQueue,
    TaskResult,
    TaskRunner,
)
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWork, UnitOfWorkFactory

WORKER_ID = "camera.link"
SKIPPED = "skipped"


class Unanswered(Exception):
    """The worker or E05 did not answer; E01's queue retries the task."""


def _result(lease: TaskLease, ref: str) -> TaskResult:
    return TaskResult(lease.task.task_id, lease.generation, ref, hashlib.sha256(ref.encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class _Deps:
    store: DispatchStore
    link: WorkerLink
    queue: TaskQueue
    snapshots: ReadSnapshotFactory
    ids: IdFactory
    clock: Clock
    limits: DispatchLimits

    def intent(self, lease: TaskLease) -> Intent | None:
        with self.snapshots.snapshot() as view:
            return self.store.get(view.connection, lease.task.input_ref)

    def current(self, uow: UnitOfWork, lease: TaskLease) -> Intent | None:
        return self.store.get(uow.connection, lease.task.input_ref)


def start_body(intent: Intent, probe: Probe, ttl_ms: int, warmup_max_ms: int) -> dict[str, object]:
    """Protocol v1 StartRequest. The clock probe fields travel with every try but are not part of the start's
    identity on the worker (M1), so a retry after a fresh probe is the same start; neither is the warm up bound."""
    return {
        "acquisition_id": intent.acquisition_id,
        "kind": "measurement",
        "capability_code": intent.capability_code,
        "request_id": intent.request_id,
        "patient_id": intent.patient_id,
        "sequence": intent.sequence,
        "device_ids": list(intent.device_ids),
        "required_origin": intent.required_origin,
        "ttl_ms": ttl_ms,
        "clock_offset_ms": probe.offset_ms,
        "clock_uncertainty_ms": probe.uncertainty_ms,
        "warmup_max_ms": warmup_max_ms,
    }


class StartHandler:
    def __init__(self, deps: _Deps) -> None:
        self._d = deps

    def _ttl(self, probe: Probe) -> int:
        """The hub's acquisition lifetime, never more than the worker accepts (m7: derived from its describe)."""
        return min(self._d.limits.acquisition_ttl_ms, probe.max_ttl_ms)

    def execute(self, lease: TaskLease) -> TaskResult:
        intent = self._d.intent(lease)
        if intent is None or intent.state != "ready":
            return _result(lease, SKIPPED)
        probe = self._d.link.fresh(self._d.clock.now())
        if probe is None:
            raise Unanswered("no fresh probe")
        answer = self._d.link.start(
            start_body(intent, probe, self._ttl(probe), self._d.limits.warmup_max_ms),
            timeout_ms=self._d.limits.timeout_ms,
        )
        if answer.kind == "ok":
            return _result(lease, f"dispatched.{self._ttl(probe)}")
        if answer.kind == "refused" and answer.code not in (None, "BUSY"):
            return _result(lease, "refused." + str(answer.code).lower())
        raise Unanswered(answer.code or answer.kind)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        current = self._d.current(uow, lease)
        if current is None or result.result_ref == SKIPPED:
            return
        kind, _, detail = result.result_ref.partition(".")
        if kind == "dispatched" and current.state == "ready":
            deadline = deadline_after_dispatch(uow.now, self._d.limits, int(detail))
            self._d.store.update(
                uow.connection, replace(current, state="dispatched", deadline=deadline), at=uow.now
            )
        elif kind == "dispatched":
            # the request finished or was reported while the start was in flight: stop what the worker accepted
            submit(self._d.queue, self._d.ids, uow, CANCEL_TASK, current)
        elif kind == "refused" and current.state == "ready":
            reporting = replace(current, state="reporting", code="camera_refused_" + detail)
            self._d.store.update(uow.connection, reporting, at=uow.now)
            submit(self._d.queue, self._d.ids, uow, REPORT_TASK, reporting)

    def discard(self, result: TaskResult) -> None:
        return


class CancelHandler:
    def __init__(self, deps: _Deps) -> None:
        self._d = deps

    def execute(self, lease: TaskLease) -> TaskResult:
        intent = self._d.intent(lease)
        if intent is None or intent.acquisition_id is None:
            return _result(lease, SKIPPED)
        answer = self._d.link.cancel(intent.acquisition_id, timeout_ms=self._d.limits.timeout_ms)
        if answer.kind == "not_found":
            return _result(lease, "unknown")
        if answer.kind == "ok":
            outcome = answer.body.get("result")
            return _result(
                lease, outcome if isinstance(outcome, str) and outcome.isidentifier() else "unknown"
            )
        raise Unanswered(answer.code or answer.kind)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        current = self._d.current(uow, lease)
        if current is not None and current.state == "cancelling":
            updated = replace(current, state="cancelled", code=result.result_ref)
            self._d.store.update(uow.connection, updated, at=uow.now)

    def discard(self, result: TaskResult) -> None:
        return


class ReportHandler:
    def __init__(self, deps: _Deps, reports: AttemptReports) -> None:
        self._d, self._reports = deps, reports

    def execute(self, lease: TaskLease) -> TaskResult:
        intent = self._d.intent(lease)
        if (
            intent is None
            or intent.state != "reporting"
            or intent.request_id is None
            or intent.sequence is None
            or intent.acquisition_id is None
        ):
            return _result(lease, SKIPPED)
        answer = self._reports.failed(
            intent.request_id,
            sequence=intent.sequence,
            at=self._d.clock.now(),
            reason=intent.code or "camera_unavailable",
            idempotency_key=intent.acquisition_id + ".hub",
        )
        if answer == "unreachable":
            raise Unanswered("recordAttempt")
        return _result(lease, answer)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        current = self._d.current(uow, lease)
        if current is not None and current.state == "reporting":
            self._d.store.update(uow.connection, replace(current, state="reported"), at=uow.now)

    def discard(self, result: TaskResult) -> None:
        return


class CameraTasks:
    """The three registrations and their runner pass."""

    def __init__(
        self,
        *,
        store: DispatchStore,
        link: WorkerLink,
        queue: TaskQueue,
        runner: TaskRunner,
        reports: AttemptReports,
        uows: UnitOfWorkFactory,
        snapshots: ReadSnapshotFactory,
        ids: IdFactory,
        clock: Clock,
        limits: DispatchLimits,
    ) -> None:
        deps = _Deps(store, link, queue, snapshots, ids, clock, limits)
        self.handlers: Mapping[str, TaskHandler] = {
            START_TASK: StartHandler(deps),
            CANCEL_TASK: CancelHandler(deps),
            REPORT_TASK: ReportHandler(deps, reports),
        }
        self._runner, self._uows, self._ids, self._batch = runner, uows, ids, limits.batch

    def drain(self) -> int:
        """Claim at most `workers.camera.dispatch_batch` camera tasks, run each outside any unit of work, and
        complete or fail it in its own; returns how many ran."""
        with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
            self._runner.recover(uow, limit=self._batch)
            leases = self._runner.claim(
                uow, worker_id=WORKER_ID, handlers=frozenset(self.handlers), limit=self._batch
            )
        for lease in leases:
            handler = self.handlers[lease.task.handler]
            try:
                result = handler.execute(lease)
            except Unanswered:
                with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
                    self._runner.fail(uow, lease, code=ErrorCode.CAPABILITY_UNAVAILABLE, retryable=True)
                continue
            with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
                completed = self._runner.complete(uow, lease, result)
            if not completed:
                handler.discard(result)
        return len(leases)
