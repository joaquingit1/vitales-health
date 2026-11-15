"""ScheduleAssessment (E32 4.4): coalesced per mission and mode. A run that is still queued absorbs every later trigger
(it reads its inputs when it starts, so it sees them); a running run does not, so a trigger during a run queues one
more. The run is a platform task (`medicines.assessment`); with no task queue on the hub, scheduling is refused
CAPABILITY_UNAVAILABLE by the explicit route and skipped for inspections and assays, never accepted and dropped."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.commands import command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.tasks import TaskInput, TaskQueue
from crew_health.kernel.uow import Audience, UnitOfWork
from crew_health.medicines.application import events
from crew_health.medicines.application.ports import PotencyStore, RunRow

HANDLER, HANDLER_VERSION = "medicines.assessment", "1"
OPERATION = "medicines.assessment.run"


@dataclass(frozen=True, slots=True)
class Scheduled:
    run: RunRow
    coalesced: bool


def unavailable() -> ProblemError:
    return ProblemError(
        ErrorCode.CAPABILITY_UNAVAILABLE, "no task queue on this hub: assessments cannot run", retryable=False
    )


def schedule(
    store: PotencyStore,
    tasks: TaskQueue | None,
    ids: IdFactory,
    process_epoch: str,
    uow: UnitOfWork,
    *,
    mission_id: str,
    origin_mode: str,
    trigger: str,
    command_key: str | None = None,
) -> Scheduled:
    if tasks is None:
        raise unavailable()
    for run in store.runs(uow, mission_id, origin_mode, limit=2):  # newest first: a queued one is among them
        if run.start is None and store.run_status(uow, run.run_id)[0] == "queued":
            return Scheduled(run, True)
    run_id = ids.new("medr")
    identity = command_identity(
        key=run_id,
        actor_ref=uow.actor.id,
        operation=OPERATION,
        target=run_id,
        parameters_json=json.dumps(
            {"mission_id": mission_id, "origin_mode": origin_mode, "trigger": trigger}, sort_keys=True
        ).encode(),
        body_fingerprint=hashlib.sha256(run_id.encode()).hexdigest(),
        fingerprint_kind="sha256",
    )
    task = TaskInput(
        task_id=run_id,
        handler=HANDLER,
        handler_version=HANDLER_VERSION,
        input_ref=run_id,
        input_fingerprint=hashlib.sha256(
            f"{run_id}|{mission_id}|{origin_mode}|{trigger}".encode()
        ).hexdigest(),
        release_id=uow.release.release_id,
        model_release_id=None,
        actor=uow.actor,
        audience=Audience(
            events.CREW_RULE,
            run_id,
            events.projection(EventType.MEDICINE_ASSESSMENT_CHANGED),
            events.POLICY_VERSION,
        ),
        command=identity,
        public_kind=None,
    )
    run = RunRow(
        run_id=run_id,
        mission_id=mission_id,
        origin_mode=origin_mode,
        trigger=trigger,
        task_id=run_id,
        process_epoch=process_epoch,
        at=uow.now,
        by_id=uow.actor.id,
        command_key=command_key,
    )
    store.add_run(uow, run)
    store.add_run_event(uow, run_id, "queued", None)
    events.assessment_changed(uow, run_id, "queued", None)
    tasks.submit(uow, task)
    return Scheduled(run, False)
