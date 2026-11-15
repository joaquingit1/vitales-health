"""Assembly of the hub's background maintenance (SYSTEM 5.2, 5.7, 5.10, 5.10.1, 5.10.2): the clock wiring every
write transaction needs and the supervisor steps that keep scheduled jobs, durable subscribers, event retention,
task leases and the clock sweep moving after startup. Each step opens its own write transactions under the single
writer lock; the supervisor isolates a failing step to its own health component."""

from __future__ import annotations

from collections.abc import Callable

from crew_health.app.registry import Resolver
from crew_health.clock.application.clock import PersistentClocks
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.adapters.repository import EventRepository
from crew_health.events.application.retention import EventRetention
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.kernel.clock import ClockSegment
from crew_health.kernel.scheduler import HealthState, ScheduledJob
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.platform.application.supervisor import Supervisor, SupervisorStep
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.storage import SQLiteDatabase

BLOCKED_JOBS = "scheduler.blocked_jobs"
FAILED_JOBS = "scheduler.failed_jobs"


def guard_clock(db: SQLiteDatabase, clocks: PersistentClocks) -> None:
    """Right after resolution, before any write (SYSTEM 5.2 "Restart loads it before any write"): every write
    transaction checks the durable high water first (a regression freezes clearing) and every replication entry
    carries an HLC stamp from the same transaction."""
    db.set_transaction_guard("clock.regression", lambda uow: _check(clocks, uow))
    db.hlc = clocks


def wire_clock(db: SQLiteDatabase, clocks: PersistentClocks) -> ClockSegment:
    """CLOCK phase: this process ticks the HLC and opens its own hub capture segment."""
    guard_clock(db, clocks)
    with db.begin(actor=Actor("engine", "engine_clock"), correlation_id=db.ids.new("startup")) as uow:
        clocks.tick(uow)
        return clocks.open_hub_segment(uow, process_epoch=db.clock.process_epoch())


def _check(clocks: PersistentClocks, uow: UnitOfWork) -> None:
    clocks.check_regression(uow)


def _transaction(db: SQLiteDatabase, actor: str, work: Callable[[UnitOfWork], int]) -> int:
    with db.begin(actor=Actor("engine", actor), correlation_id=db.ids.new("supervisor")) as uow:
        return work(uow)


def _sweep(clocks: PersistentClocks) -> Callable[[UnitOfWork], int]:
    def run(uow: UnitOfWork) -> int:
        version = clocks.pending_sweep(uow)
        if version is None:
            return 0
        clocks.complete_sweep(uow, correction_version=version)
        return 1

    return run


def _dispatch(db: SQLiteDatabase, health: HealthState) -> Callable[[], int]:
    def run() -> int:
        limit = db.scheduler.settings.integer("scheduler.batch_size")
        done = _transaction(db, "engine_scheduler", lambda uow: db.scheduler.dispatch(uow, limit=limit))
        if db.scheduler.blocked:
            health.mark_degraded(BLOCKED_JOBS, ErrorCode.CAPABILITY_UNAVAILABLE.value, db.clock.now())
        else:
            health.mark_ok(BLOCKED_JOBS)
        if db.scheduler.failed:
            health.mark_degraded(FAILED_JOBS, ErrorCode.INTERNAL_ERROR.value, db.clock.now())
        else:
            health.mark_ok(FAILED_JOBS)
        return done

    return run


def _subscriber(subscriptions: DurableSubscriptions, name: str, pages: int) -> Callable[[], int]:
    def run() -> int:
        total = 0
        for _ in range(pages):
            delivered = subscriptions.process(name)
            total += delivered
            if delivered == 0:
                break
        return total

    return run


def _evaluation_retry(service: EvaluationService) -> Callable[[UnitOfWork, ScheduledJob], None]:
    def run(uow: UnitOfWork, job: ScheduledJob) -> None:
        service.retry(uow, completion_id=job.payload["completion_id"])

    return run


def build_supervisor(db: SQLiteDatabase, resolver: Resolver) -> Supervisor:
    clocks = resolver.require(PersistentClocks)
    health = resolver.require(HealthState)  # type: ignore[type-abstract]
    subscriptions = resolver.require(DurableSubscriptions)
    retention = EventRetention(
        repository=resolver.require(EventRepository),
        settings=db.settings,
        advance=db.retention_advance,
        registry=subscriptions,
    )
    pages = db.settings.integer("supervisor.subscriber_pages")
    steps = [
        SupervisorStep("clock.sweep", lambda: _transaction(db, "engine_clock", _sweep(clocks))),
        SupervisorStep("scheduler.dispatch", _dispatch(db, health)),
        *(
            SupervisorStep("events.subscriber." + name, _subscriber(subscriptions, name, pages))
            for name in subscriptions.subscribers()
        ),
        SupervisorStep("events.retention", lambda: _transaction(db, "engine_events", retention.prune)),
    ]
    # SYSTEM 5.10.2 and 5.10.1: evaluation retry is a scheduled job and task lease recovery a supervisor step;
    # assembly always composes both services, so neither depends on a module choosing to provide them.
    evaluation = resolver.require(EvaluationService)
    retry = _evaluation_retry(evaluation)
    if "platform.evaluation.retry" in db.scheduler.handlers:  # a second assembly over this database
        db.scheduler.handlers["platform.evaluation.retry"] = retry
    else:
        db.scheduler.register("platform.evaluation.retry", retry)
    tasks = resolver.require(TaskService)
    limit = db.settings.integer("tasks.claim_batch")
    steps.append(
        SupervisorStep(
            "tasks.recover",
            lambda: _transaction(db, "engine_tasks", lambda uow: tasks.recover(uow, limit=limit)),
        )
    )
    return Supervisor(steps=tuple(steps), settings=db.settings, health=health, now=db.clock.now)
