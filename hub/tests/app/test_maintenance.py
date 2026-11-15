"""Regression tests for the E01 review findings on the clock wiring and the maintenance supervisor (SYSTEM 5.2,
5.7, 5.10, 5.10.1, 5.10.2). Each test drives the production wiring in app/maintenance.py, not a hand call of the
service method the wiring is supposed to make."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crew_health.app.maintenance import build_supervisor, wire_clock
from crew_health.app.registry import DeclarationRegistry
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import ClockIdentity, PersistentClocks
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.retention import EventRetention
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.kernel.clock import ClockCorrection, TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import AbsoluteDeadline, HealthState, ScheduledJob
from crew_health.kernel.testing import MemoryClock, MemoryIds
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReplicationChange,
    StoredEvent,
)
from crew_health.platform.adapters.evaluation import EvaluationRepository
from crew_health.platform.adapters.tasks import TaskRepository
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.application.supervisor import Supervisor, SupervisorStep
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-12", "SYSTEM.5.2", "SYSTEM.5.10")]
ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "hub/src/crew_health"
ENGINE = Actor("engine", "engine_test")
OPERATOR = Actor("operator", "operator_test")


def at(hour: int) -> datetime:
    return datetime(2026, 1, 2, hour, tzinfo=UTC)


def migrated(tmp_path: Path, clock: MemoryClock) -> SQLiteDatabase:
    db = database(tmp_path, clock=clock)
    for name in (
        "platform/adapters/migrations/0002_platform.sql",
        "clock/adapters/migrations/0001_clock.sql",
        "events/adapters/migrations/0001_events.sql",
    ):
        db.migrate((SOURCE / name).read_text(encoding="utf-8"))
    return db


def clocks_for(db: SQLiteDatabase) -> PersistentClocks:
    identity = ClockIdentity(db.origin_node_id, db.origin_incarnation, "hubclock_test")
    return PersistentClocks(
        repository=SqlClockRepository(
            settings=db.settings, node_id=identity.node_id, incarnation_id=identity.incarnation_id
        ),
        hub_source_clock_id=identity.hub_source_clock_id,
        settings=db.settings,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(1000))),
    )


def events(db: SQLiteDatabase) -> tuple[EventRepository, DurableSubscriptions]:
    repository = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"),
    )
    db.event_sink = repository.append
    return repository, DurableSubscriptions(repository=repository, settings=db.settings, factory=db)


def supervisor(
    db: SQLiteDatabase,
    clocks: PersistentClocks,
    subscriptions: DurableSubscriptions,
    repository: EventRepository,
) -> tuple[Supervisor, ComponentHealthState]:
    health = ComponentHealthState(maximum=db.settings.integer("registry.max_hooks"))
    registry = DeclarationRegistry(max_ports=64, max_hooks=64)
    registry.provide(PersistentClocks, lambda resolver: clocks)
    registry.provide(HealthState, lambda resolver: health)  # type: ignore[type-abstract]
    registry.provide(DurableSubscriptions, lambda resolver: subscriptions)
    registry.provide(EventRepository, lambda resolver: repository)

    def capture(at: datetime) -> TimeInput:
        return TimeInput(at, "hubclock_test", "segment_test", 0, "hub_corrected", "SIM")

    registry.provide(
        EvaluationService,
        lambda resolver: EvaluationService(
            (), db.settings, db.ids, repository=EvaluationRepository(), capture=lambda uow: capture(uow.now)
        ),
    )
    registry.provide(
        TaskService,
        lambda resolver: TaskService(
            settings=db.settings,
            repository=TaskRepository(),
            ids=db.ids,
            registrations=(),
            authorize=lambda view, session, task: False,
            authorize_completion=lambda uow, task: False,
            capture=capture,
            elapsed=clocks.elapsed,
        ),
    )
    return build_supervisor(db, registry.resolve()), health


def correct_hub_clock(db: SQLiteDatabase, clocks: PersistentClocks, version: int, offset_ms: int = 0) -> None:
    with db.begin(actor=OPERATOR, correlation_id="correction") as uow:
        segment = clocks.repository.latest_segment(uow.connection, "hubclock_test")
        assert segment is not None
        clocks.apply(
            uow,
            ClockCorrection(
                f"correction_{version}",
                segment.source_clock_id,
                segment.segment_id,
                version,
                uow.now,
                None,
                offset_ms,
                0,
                uow.now,
                "operator",
            ),
        )


def clearing(db: SQLiteDatabase, clocks: PersistentClocks) -> bool:
    with db.snapshot() as view:
        return clocks.clearing_allowed(view)


def event(db: SQLiteDatabase) -> OutboxEvent:
    stamp = db.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    return OutboxEvent(
        EventType.GUIDED_CHECK_BEAT,
        db.clock.now(),
        EventScope("person", "person_A", None),
        "release_1",
        None,
        canonical_json({"guided_check_id": "guided_1", "beat_count": 1, "at": stamp, "seconds_left": None}),
        Audience("guided", "guided_1", AudienceProjectionKey("guided", "beat", 1), "1"),
    )


def test_fresh_hub_stays_frozen_until_its_own_clock_is_corrected_and_the_supervisor_sweeps(
    tmp_path: Path,
) -> None:
    clock = MemoryClock(at(12), 0, "epoch_1")
    db = migrated(tmp_path, clock)
    clocks = clocks_for(db)
    repository, subscriptions = events(db)
    wire_clock(db, clocks)
    loop, _ = supervisor(db, clocks, subscriptions, repository)
    assert not clearing(db, clocks)
    assert loop.run_once()["clock.sweep"] == 0
    correct_hub_clock(db, clocks, 1)
    assert loop.run_once()["clock.sweep"] == 1
    assert clearing(db, clocks)
    assert loop.run_once()["clock.sweep"] == 0


def test_wall_clock_set_back_after_uptime_freezes_clearing_on_the_next_ordinary_write(tmp_path: Path) -> None:
    clock = MemoryClock(at(12), 0, "epoch_1")
    db = migrated(tmp_path, clock)
    db.migrate("CREATE TABLE probe_fact (id TEXT PRIMARY KEY) STRICT;")
    clocks = clocks_for(db)
    repository, subscriptions = events(db)
    wire_clock(db, clocks)
    loop, _ = supervisor(db, clocks, subscriptions, repository)
    correct_hub_clock(db, clocks, 1)
    loop.run_once()
    assert clearing(db, clocks)
    clock.at = at(20)
    with db.begin(actor=ENGINE, correlation_id="uptime") as uow:
        uow.connection.execute("INSERT INTO probe_fact VALUES (?)", ("at_twenty",))
    clock.at = at(14)  # the wall clock is set back six hours while the hub keeps running
    with db.begin(actor=ENGINE, correlation_id="after_jump") as uow:
        uow.connection.execute("INSERT INTO probe_fact VALUES (?)", ("at_fourteen",))
    assert not clearing(db, clocks)
    # A new hub correction plus a sweep is the only way back.
    correct_hub_clock(db, clocks, 2, offset_ms=6 * 3600 * 1000)
    assert loop.run_once()["clock.sweep"] == 1
    assert clearing(db, clocks)


def test_replication_entries_carry_the_hlc_of_their_transaction(tmp_path: Path) -> None:
    db = migrated(tmp_path, MemoryClock(at(12), 0, "epoch_1"))
    clocks = clocks_for(db)
    wire_clock(db, clocks)
    with db.begin(actor=ENGINE, correlation_id="replicated") as uow:
        before = clocks.repository.hlc(uow.connection)
        uow.replicate(
            ReplicationChange(
                None,
                "test_record",
                "record_1",
                "append",
                b"{}",
                (),
                uow.release.release_id,
                Audience("operator", "record_1", AudienceProjectionKey("tests", "record", 1), "1"),
            )
        )
    with db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT hlc_physical_ms, hlc_logical FROM repl_log ORDER BY seq", (), limit=8
        )
        stamped = clocks.repository.hlc(view.connection)
    assert stamped is not None and (
        before is None or (stamped.physical_ms, stamped.logical) > (before.physical_ms, before.logical)
    )
    # Every entry is stamped, including the hub segment opened at startup, and the last one carries the high water
    # its own transaction advanced.
    assert all(None not in row for row in rows)
    assert rows[-1] == (stamped.physical_ms, stamped.logical)


def test_a_job_the_hub_cannot_run_never_starves_later_due_jobs(tmp_path: Path) -> None:
    db = database(tmp_path)
    ran: list[str] = []
    db.scheduler.register("known", lambda uow, job: ran.append(job.key))
    base = datetime(2026, 11, 13, tzinfo=UTC)
    with db.begin(actor=ENGINE, correlation_id="schedule") as uow:
        uow.schedule(
            ScheduledJob("job_a", "uninstalled", AbsoluteDeadline(base), {}, None), expected_generation=None
        )
        uow.schedule(
            ScheduledJob("job_b", "known", AbsoluteDeadline(base + timedelta(hours=1)), {}, None),
            expected_generation=None,
        )
    with db.begin(actor=ENGINE, correlation_id="dispatch") as uow:
        assert db.scheduler.dispatch(uow, limit=1) == 1
    assert ran == ["job_b"]
    assert db.scheduler.blocked == ("job_a",)


def test_subscriber_registered_after_retention_starts_at_the_watermark_and_receives_new_events(
    tmp_path: Path,
) -> None:
    clock = MemoryClock(at(12), 0, "epoch_1")
    db = migrated(tmp_path, clock)
    repository, subscriptions = events(db)
    with db.begin(actor=ENGINE, correlation_id="old_event") as uow:
        uow.emit(event(db))
    clock.at += timedelta(hours=db.settings.integer("events.retention_hours") + 1)
    retention = EventRetention(
        repository=repository, settings=db.settings, advance=db.retention_advance, registry=subscriptions
    )
    with db.begin(actor=ENGINE, correlation_id="prune") as uow:
        assert retention.prune(uow) == 1
    seen: list[StoredEvent] = []
    subscriptions.register(
        "late_module", frozenset({EventType.GUIDED_CHECK_BEAT}), lambda uow, value: seen.append(value)
    )
    with db.begin(actor=ENGINE, correlation_id="startup") as uow:
        assert subscriptions.initialize(uow) == 1
    assert subscriptions.process("late_module") == 0
    with db.begin(actor=ENGINE, correlation_id="new_event") as uow:
        uow.emit(event(db))
    assert subscriptions.process("late_module") == 1
    assert len(seen) == 1
    # Retention now waits for the registered subscriber's cursor instead of an injected list.
    clock.at += timedelta(hours=db.settings.integer("events.retention_hours") + 1)
    with db.begin(actor=ENGINE, correlation_id="newer_event") as uow:
        uow.emit(event(db))
    with db.begin(actor=ENGINE, correlation_id="prune_again") as uow:
        assert retention.prune(uow) == 1
    assert subscriptions.process("late_module") == 1


def test_a_failing_supervisor_step_degrades_only_itself_and_the_loop_stops_cleanly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = database(tmp_path)
    health = ComponentHealthState(maximum=db.settings.integer("registry.max_hooks"))
    passes, failing = threading.Event(), [True]
    counted: list[int] = []

    def broken() -> int:
        if failing[0]:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "secret note body")
        return 0

    def counting() -> int:
        counted.append(1)
        if len(counted) >= 3:
            passes.set()
        return 1

    loop = Supervisor(
        steps=(SupervisorStep("broken", broken), SupervisorStep("counting", counting)),
        settings=db.settings,
        health=health,
        now=db.clock.now,
    )
    with caplog.at_level(logging.ERROR):
        assert loop.run_once() == {"broken": None, "counting": 1}
    snapshot = health.snapshot()
    assert snapshot.status == "degraded"
    assert {c.component: c.code for c in snapshot.components} == {
        "supervisor.broken": ErrorCode.CAPABILITY_UNAVAILABLE.value,
        "supervisor.counting": None,
    }
    assert "step=broken" in caplog.text and "secret note body" not in caplog.text
    failing[0] = False
    loop.start()
    with pytest.raises(ProblemError):
        loop.start()
    assert passes.wait(timeout=5)
    loop.stop()
    assert not loop.running
    assert health.snapshot().status == "ok"
