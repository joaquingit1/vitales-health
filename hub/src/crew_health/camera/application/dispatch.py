"""Measurement dispatch to the camera worker (A7; PLAN: E05 request accepted, pinned dispatch, capture on the device).

1. E05's `start` asks `AdapterAssignmentPort.assign` inside its own unit of work. The camera assignment records an
   intent keyed by that unit of work's correlation ID, so it commits or rolls back with the start.
2. The durable subscriber `camera.dispatch` receives `measurement_request.changed`. The event of the start's own
   commit (same correlation ID) binds the intent to the request, its subject, the next attempt sequence, the
   cameras the device configuration pairs with that subject (B1) and the origin the mission requires (M4), then
   submits a `camera.start` task to E01's task queue in the same unit of work (M6). A start that cannot be served
   (no fresh probe, no paired camera, another origin) is reported to E05 as "Not measured" with its reason code.
   Later events of the same request follow it: the attempt the worker recorded closes the intent; a cancelled or
   otherwise finished request drops what was never sent and cancels what was (V13). A failed attempt is final for
   that capture: measuring again is a new, explicit request (m9, PLAN line 56). Duplicate deliveries change nothing.
3. Every worker call runs in a task handler outside any unit of work (tasks.py); E01's queue retries it.
4. `reconcile` runs on the camera loop, outside every unit of work (M2): it asks the worker about each acquisition it
   accepted and reports to E05 what ended on the worker without an attempt (refused by the hub, expired, lost in a
   worker restart, unreachable past its deadline); a start never sent by its deadline is reported the same way. So no
   camera request stays in progress without a reason.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

from crew_health.camera.application.link import Answer, WorkerLink
from crew_health.camera.application.ports import MissionStatePort, OriginMode
from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import command_identity
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.tasks import TaskInput, TaskQueue
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)

type DispatchState = Literal[
    "assigned", "ready", "dispatched", "done", "reporting", "reported", "dropped", "cancelling", "cancelled"
]
ENGINE = Actor("engine", "camera.dispatch")
START_TASK, CANCEL_TASK, REPORT_TASK = "camera.start", "camera.cancel", "camera.report"
TASK_VERSION = "1"
_FINISHED = frozenset({"completed", "unavailable", "declined", "cancelled", "failed"})
_AUDIENCE = AudienceProjectionKey("camera", "dispatch", 1)


@dataclass(frozen=True, slots=True)
class Intent:
    correlation_id: str
    capability_code: str
    adapter_id: str
    state: DispatchState
    request_id: str | None
    patient_id: str | None
    sequence: int | None
    acquisition_id: str | None
    device_ids: tuple[str, ...]
    required_origin: OriginMode | None
    deadline: datetime | None
    code: str | None


class DispatchStore(Protocol):
    def record(
        self,
        session: StorageSession,
        *,
        correlation_id: str,
        capability_code: str,
        adapter_id: str,
        at: datetime,
    ) -> None: ...
    def get(self, session: StorageSession, correlation_id: str) -> Intent | None: ...
    def for_request(self, session: StorageSession, request_id: str, *, limit: int) -> tuple[Intent, ...]: ...
    def open(self, session: StorageSession, *, limit: int) -> tuple[Intent, ...]: ...
    def update(self, session: StorageSession, intent: Intent, *, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class DispatchLimits:
    batch: int
    timeout_ms: int
    acquisition_ttl_ms: int
    dispatch_deadline_ms: int
    max_per_request: int
    warmup_max_ms: int


@dataclass(frozen=True, slots=True)
class _Change:
    request_id: str
    state: str
    attempts: tuple[Mapping[str, object], ...]

    def next_sequence(self) -> int | None:
        sequences = [a.get("sequence") for a in self.attempts]
        if any(not isinstance(s, int) for s in sequences):
            return None
        return max((s for s in sequences if isinstance(s, int)), default=0) + 1

    def recorded(self, sequence: int | None) -> bool:
        return sequence is not None and any(a.get("sequence") == sequence for a in self.attempts)


def _change(event: StoredEvent) -> _Change | None:
    try:
        data = json.loads(event.data_json)
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    request_id, state, attempts = data.get("request_id"), data.get("state"), data.get("attempts")
    if not isinstance(request_id, str) or not isinstance(state, str) or not isinstance(attempts, list):
        return None
    if any(not isinstance(a, Mapping) for a in attempts):
        return None
    return _Change(request_id, state, tuple(attempts))


def submit(queue: TaskQueue, ids: IdFactory, uow: UnitOfWork, name: str, intent: Intent) -> None:
    """One E01 task per worker call kind and acquisition; a duplicate submission of the same task is a replay."""
    task_id = ids.derived("ctk", name, intent.correlation_id)
    fingerprint = hashlib.sha256(f"{name}/{intent.correlation_id}".encode()).hexdigest()
    command = command_identity(
        key=task_id,
        actor_ref=uow.actor.id,
        operation=name,
        target=intent.correlation_id,
        parameters_json=b"{}",
        body_fingerprint=fingerprint,
        fingerprint_kind="sha256",
    )
    queue.submit(
        uow,
        TaskInput(
            task_id, name, TASK_VERSION, intent.correlation_id, fingerprint, uow.release.release_id, None,
            uow.actor, Audience("camera.dispatch", intent.correlation_id, _AUDIENCE, "1"), command, None,
        ),
    )  # fmt: skip


class Dispatcher:
    def __init__(
        self,
        *,
        store: DispatchStore,
        link: WorkerLink,
        queue: TaskQueue,
        mission: MissionStatePort,
        uows: UnitOfWorkFactory,
        snapshots: ReadSnapshotFactory,
        ids: IdFactory,
        clock: Clock,
        limits: DispatchLimits,
    ) -> None:
        self._store, self._link, self._queue, self._mission = store, link, queue, mission
        self._uows, self._snapshots, self._ids, self._clock, self._limits = (
            uows,
            snapshots,
            ids,
            clock,
            limits,
        )

    # ------------------------------------------------------------------------------------- durable subscriber

    def on_changed(self, uow: UnitOfWork, event: StoredEvent) -> None:
        """Bind a start's intent, close what the worker recorded, or stop work for a finished request. Idempotent per
        event: every write is keyed by the event's correlation ID or guarded by state."""
        if event.type != EventType.MEASUREMENT_REQUEST_CHANGED or event.correlation_id is None:
            return
        change = _change(event)
        own = self._store.get(uow.connection, event.correlation_id)
        if own is not None and own.state == "assigned":
            self._bind(uow, own, change, event.scope.person_id)
            return
        if change is None or own is not None:
            return
        for intent in self._store.for_request(
            uow.connection, change.request_id, limit=self._limits.max_per_request
        ):
            if intent.state in ("ready", "dispatched") and change.recorded(intent.sequence):
                self._store.update(uow.connection, replace(intent, state="done"), at=uow.now)
            elif change.state in _FINISHED:
                self._stop(uow, intent)

    def _bind(self, uow: UnitOfWork, intent: Intent, change: _Change | None, patient: str | None) -> None:
        sequence = None if change is None else change.next_sequence()
        if change is None or change.state != "in_progress" or sequence is None or patient is None:
            self._store.update(uow.connection, replace(intent, state="dropped"), at=uow.now)
            return
        bound = replace(
            intent,
            request_id=change.request_id,
            patient_id=patient,
            sequence=sequence,
            acquisition_id=self._ids.derived("cad", change.request_id, str(sequence)),
        )
        mission = self._mission.current(uow)
        probe = self._link.fresh(uow.now)
        devices = () if probe is None else probe.paired_with(patient)
        if mission is None or probe is None:
            self._report(uow, bound, "camera_unavailable")
        elif probe.origin_mode != mission.mission_mode:
            self._report(uow, bound, "camera_origin_mismatch")
        elif not devices:
            self._report(uow, bound, "camera_not_paired")
        else:
            deadline = uow.now + timedelta(milliseconds=self._limits.dispatch_deadline_ms)
            ready = replace(
                bound,
                state="ready",
                device_ids=devices,
                required_origin=mission.mission_mode,
                deadline=deadline,
            )
            self._store.update(uow.connection, ready, at=uow.now)
            submit(self._queue, self._ids, uow, START_TASK, ready)

    def _stop(self, uow: UnitOfWork, intent: Intent) -> None:
        if intent.state == "ready":
            self._store.update(uow.connection, replace(intent, state="dropped"), at=uow.now)
        elif intent.state == "dispatched":
            cancelling = replace(intent, state="cancelling")
            self._store.update(uow.connection, cancelling, at=uow.now)
            submit(self._queue, self._ids, uow, CANCEL_TASK, cancelling)

    def _report(self, uow: UnitOfWork, intent: Intent, code: str) -> None:
        reporting = replace(intent, state="reporting", code=code)
        self._store.update(uow.connection, reporting, at=uow.now)
        submit(self._queue, self._ids, uow, REPORT_TASK, reporting)

    # ------------------------------------------------------------------------------------- reconciliation

    def reconcile(self) -> int:
        """Ask the worker about at most `workers.camera.dispatch_batch` open acquisitions, oldest first, and report
        those that ended without an attempt; returns how many were reported."""
        with self._snapshots.snapshot() as view:
            open_intents = self._store.open(view.connection, limit=self._limits.batch)
        reported = 0
        for intent in open_intents:
            code = self._verdict(intent)
            if code is None:
                continue
            with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
                if self._store.get(uow.connection, intent.correlation_id) != intent:
                    continue  # the request moved on meanwhile: the newer state wins
                if code == "done":
                    self._store.update(uow.connection, replace(intent, state="done"), at=uow.now)
                else:
                    self._report(uow, intent, code)
                    reported += 1
        return reported

    def _verdict(self, intent: Intent) -> str | None:
        now = self._clock.now()
        late = intent.deadline is not None and now >= intent.deadline
        if intent.state == "ready":
            return "camera_unavailable" if late else None
        answer = self._link.status(intent.acquisition_id or "", timeout_ms=self._limits.timeout_ms)
        return _status_verdict(answer, late)


_WORKER_ENDED: Mapping[str, str] = {
    "expired": "camera_expired",
    "cancelled": "camera_cancelled",
    "rejected": "camera_attempt_rejected",
}


def _status_verdict(answer: Answer, late: bool) -> str | None:
    if answer.kind == "not_found":
        return "camera_lost"  # the worker restarted without it or dropped it
    if answer.kind != "ok":
        return "camera_unreachable" if late else None
    state = answer.body.get("state")
    if state == "delivered":
        return "done"
    if isinstance(state, str) and state in _WORKER_ENDED:
        return _WORKER_ENDED[state]
    return None  # accepted, capturing or delivering: the worker still owns it and never gives up delivering


def deadline_after_dispatch(now: datetime, limits: DispatchLimits, ttl_ms: int) -> datetime:
    return now + timedelta(milliseconds=ttl_ms + limits.dispatch_deadline_ms)


__all__ = [
    "CANCEL_TASK",
    "ENGINE",
    "REPORT_TASK",
    "START_TASK",
    "DispatchLimits",
    "DispatchState",
    "DispatchStore",
    "Dispatcher",
    "Intent",
    "deadline_after_dispatch",
    "submit",
]
