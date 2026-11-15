# mypy: disable-error-code="type-abstract"
"""An explicit, deterministic station using the real SQLite, ledger and delivery services."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from crew_health.app.registry import DeclarationRegistry, StartupPhase
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.contracts_gen.enums import ConfigName, EventType, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.kernel.clock import (
    Clock,
    ClockCorrected,
    ClockCorrection,
    ClockCorrections,
    ClockSegment,
    TimeInput,
)
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.config import ConfigDocumentView, ReleaseHandle
from crew_health.kernel.evaluation import EvaluationInput
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock, MissionClockVersion
from crew_health.kernel.scheduler import AbsoluteDeadline, HealthState, ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemoryMissionClock, MemoryRelease
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    DomainEvent,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings as platform_settings
from crew_health.records.adapters.repository import RecordRepository
from crew_health.routine import module
from crew_health.routine.adapters.sqlite_repository import migration_sql
from crew_health.routine.application.audit import audit_event_types
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.ports import (
    HubClock,
    LocationPort,
    MissionFacts,
    MissionStatePort,
    RecordFacts,
    RosterMember,
    RosterPort,
    RoutineQueries,
)
from crew_health.routine.application.settings import specs

from .support import REPO, example


def instant(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def session(person: str | None = "person_mara", *, roles: frozenset[Role] | None = None) -> SessionContext:
    return SessionContext(
        "session_" + (person or "station"),
        "crew" if person else "station",
        Actor("person", person) if person else Actor("station", "station_1"),
        person,
        frozenset({Role.CREW}) if roles is None else roles,
    )


def configured_release(release_id: str = "rel_test") -> MemoryRelease:
    documents: dict[ConfigName, ConfigDocumentView] = {}
    for name in (
        ConfigName.ROUTINE_SCHEDULE,
        ConfigName.ROUTINE_RULES,
        ConfigName.UI_COPY,
        ConfigName.QUESTIONNAIRES,
    ):
        raw = (
            (REPO / "config" / f"{name.value}.json").read_bytes()
            if name in (ConfigName.ROUTINE_SCHEDULE, ConfigName.ROUTINE_RULES)
            else canonical_json(example(name.value))
        )
        documents[name] = ConfigDocumentView(name, sha256(raw).hexdigest(), raw)
    return MemoryRelease(release_id, documents)


class StationSettings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.platform = platform_settings()
        defaults: dict[str, object] = {
            "routine.page_limit": 100,
            "routine.max_open_per_person": 100,
            "routine.max_active_overrides_per_person": 100,
            "routine.max_assistant_rechecks_per_person_day": 3,
            "routine.max_overlay_intervals": 100,
            "routine.miss_grace_ms": 60000,
            "routine.release_cache_entries": 8,
            "routine.max_items": 100,
            "routine.max_bindings": 100,
            "routine.max_slots_per_binding": 32,
            "routine.max_roster": 64,
            "routine.history_days": 100,
        }
        defaults.update(values or {})
        self.routine = ValidatedSettings(specs(), defaults)

    def integer(self, key: str) -> int:
        return (self.routine if key.startswith("routine.") else self.platform).integer(key)

    def quantity(self, key: str) -> Quantity:
        return self.platform.quantity(key)

    def boolean(self, key: str) -> bool:
        return self.platform.boolean(key)

    def text(self, key: str) -> str:
        return self.platform.text(key)


@dataclass
class Mission:
    facts: MissionFacts | None

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return self.facts


class Roster:
    def __init__(self) -> None:
        self.roster = tuple(
            RosterMember("person_" + name, name.capitalize(), index, 0 if index == 0 else None)
            for index, name in enumerate(("mara", "tomas", "ilya", "sofia"))
        )

    def crew(self, at: datetime) -> Sequence[RosterMember]:
        return self.roster

    def members(self) -> Sequence[RosterMember]:
        return self.roster

    def person_ids(self) -> frozenset[str]:
        return frozenset(member.person_id for member in self.roster)


class Locations:
    def __init__(self) -> None:
        self.locations: dict[str, str | None] = {
            "person_mara": "cabin",
            "person_tomas": None,
            "person_ilya": "airlock",
            "person_sofia": "cabin",
        }

    def location_of(self, person_id: str, at: datetime) -> str | None:
        return self.locations.get(person_id)


class HarnessHubClock:
    """The hub clock capture E01 builds privately in its assembly (`_hub_time`), offered here as routine's HubClock."""

    def __init__(self, clocks: PersistentClocks) -> None:
        self.clocks = clocks

    def hub_time(self, uow: UnitOfWork, origin_mode: Literal["LIVE", "REPLAY", "SIM"]) -> TimeInput | None:
        source = self.clocks.hub_source_clock_id
        segment = None if source is None else self.clocks.repository.latest_segment(uow.connection, source)
        if segment is None:
            return None
        version = self.clocks.repository.correction_head(uow.connection)
        return TimeInput(
            uow.now, segment.source_clock_id, segment.segment_id, version, "hub_corrected", origin_mode
        )


class RoutineHarness:
    def __init__(
        self,
        directory: Path,
        *,
        at: datetime | None = None,
        mode: Literal["LIVE", "REPLAY", "SIM"] = "SIM",
        initialize: bool = True,
        values: dict[str, object] | None = None,
    ) -> None:
        epoch = datetime(2026, 11, 1, tzinfo=UTC)
        self.clock = MemoryClock(at or epoch, 0, "epoch_1")
        self.mission_clock = MemoryMissionClock(
            (MissionClockVersion("clock_1", 1, epoch, epoch, None, "Day {day}"),)
        )
        self.ids = MemoryIds(identifiers=tuple(str(i) for i in range(0 if initialize else 50000, 100000)))
        self.settings = StationSettings(values)
        self.health = ComponentHealthState(maximum=256)
        self.mission = Mission(MissionFacts("mission_1", mode, "station_leo", None, "node_1"))
        self.roster, self.locations = Roster(), Locations()
        self.release: ReleaseHandle = configured_release()
        self.db = SQLiteDatabase(
            directory / "hub.sqlite",
            clock=self.clock,
            ids=self.ids,
            release=self.release,
            settings=self.settings,
            origin_node_id="node_1",
            origin_incarnation="incarnation_1",
            initialize=initialize,
        )
        self._events()
        sql = (REPO / "hub/src/crew_health/records/adapters/migrations/0001_records.sql").read_text(
            encoding="utf-8"
        )
        self.db.migrate_tracked("records.0001", sha256(sql.encode()).hexdigest(), sql)
        self.records = RecordRepository()
        sql = migration_sql()
        self.db.migrate_tracked("routine.0001", sha256(sql.encode()).hexdigest(), sql)
        sql = (REPO / "hub/src/crew_health/routine/adapters/migrations/0002_routine.sql").read_text(
            encoding="utf-8"
        )
        self.db.migrate_tracked("routine.0002", sha256(sql.encode()).hexdigest(), sql)
        for event_type in audit_event_types():
            self.db.register_audit_policy(
                AuditPolicy(
                    event_type,
                    {
                        "code": frozenset({event_type.removeprefix("routine.")}),
                        "source_kind": frozenset(
                            {"warning", "mission_event", "overlay", "observation", "answer", "person"}
                        ),
                    },
                    frozenset(),
                    {},
                )
            )
        self.ledger = CommandLedger(self.ids, settings=self.settings)
        self._clock_history(initialize)
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=256)
        self._providers()
        module.register(self.registry)
        self._handlers()
        self.resolver = self.registry.resolve()
        for key, callback in sorted(
            self.registry.declarations.get("startup", {}).items(), key=lambda pair: str(pair[0])
        ):
            if cast(tuple[StartupPhase, str, str], key)[0] == StartupPhase.STATE_SWEEP:
                cast(Callable[[], None], callback)()
        self.runtime = cast(module.RoutineRuntime, self.resolver.require(RoutineQueries))
        self.engine = self.runtime.engine
        self.queries, self.commands = self.runtime.queries, self.runtime.commands

    def _events(self) -> None:
        sql = (REPO / "hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text(
            encoding="utf-8"
        )
        self.db.migrate_tracked("events.0001", sha256(sql.encode()).hexdigest(), sql)
        self.events = EventRepository(
            settings=self.settings,
            ids=self.ids,
            validator=EventValidator(schema_directory=REPO / "contracts/api/schemas"),
        )
        self.db.event_sink = self.events.append
        self.subscriptions = DurableSubscriptions(
            repository=self.events, settings=self.settings, factory=self.db
        )

    def _clock_history(self, initialize: bool) -> None:
        """The real E01 clock (SYSTEM 5.2): its migration, the hub's own source clock and segment, and on a new
        store the operator's first correction and sweep, so the shared clock starts trusted as after a hub's
        first time confirmation. `regress` and `confirm_time` drive it later."""
        sql = (REPO / "hub/src/crew_health/clock/adapters/migrations/0001_clock.sql").read_text(
            encoding="utf-8"
        )
        self.db.migrate_tracked("clock.0001", sha256(sql.encode()).hexdigest(), sql)
        self.clocks = PersistentClocks(
            repository=SqlClockRepository(
                settings=self.settings, node_id="node_1", incarnation_id="incarnation_1"
            ),
            settings=self.settings,
            ids=self.ids,
            hub_source_clock_id="hub_clock",
        )
        if initialize:
            with self.db.begin(actor=Actor("operator", "operator_1"), correlation_id="clock_setup") as uow:
                self.clocks.open(
                    uow, ClockSegment("hub_clock", "hub_oscillator", "hub_segment_1", None, 1, 1)
                )
            self.confirm_time()

    def confirm_time(self, offset_ms: int = 0) -> None:
        """An operator correction on the hub clock followed by the correction sweep (clock.py complete_sweep)."""
        with self.db.snapshot() as view:
            version = self.clocks.repository.correction_head(view.connection) + 1
        with self.db.begin(
            actor=Actor("operator", "operator_1"), correlation_id=f"clock_fix_{version}"
        ) as uow:
            self.clocks.apply(
                uow,
                ClockCorrection(
                    f"correction_{version}",
                    "hub_clock",
                    "hub_segment_1",
                    version,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    None,
                    offset_ms,
                    0,
                    uow.now,
                    "operator",
                ),
            )
        self.sweep(version)

    def correct(self, offset_ms: int = 0) -> int:
        """An operator correction without its sweep: the shared clock stays frozen."""
        with self.db.snapshot() as view:
            version = self.clocks.repository.correction_head(view.connection) + 1
        with self.db.begin(
            actor=Actor("operator", "operator_1"), correlation_id=f"clock_fix_{version}"
        ) as uow:
            self.clocks.apply(
                uow,
                ClockCorrection(
                    f"correction_{version}",
                    "hub_clock",
                    "hub_segment_1",
                    version,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    None,
                    offset_ms,
                    0,
                    uow.now,
                    "operator",
                ),
            )
        return version

    def sweep(self, version: int) -> None:
        with self.db.begin(
            actor=Actor("operator", "operator_1"), correlation_id=f"clock_sweep_{version}"
        ) as uow:
            self.clocks.check_regression(uow)
            self.clocks.complete_sweep(uow, correction_version=version)

    def regress(self, to: datetime) -> None:
        """Moves the wall clock back and runs the kernel's regression check (create_app does it at start and
        E01 in every write): the shared clock freezes and ClockRegressed is published."""
        with self.db.begin(actor=Actor("engine", "engine_clock"), correlation_id=self.ids.new("cor")) as uow:
            self.clocks.check_regression(uow)
        self.clock.at = to
        with self.db.begin(actor=Actor("engine", "engine_clock"), correlation_id=self.ids.new("cor")) as uow:
            self.clocks.check_regression(uow)

    def restart(self, deps: RoutineDeps) -> None:
        """A routine restart on `deps`: bind's assemble, then the STATE_SWEEP startup hook (SYSTEM 6)."""
        self.runtime.assemble(deps)
        self.runtime.startup()
        self.engine = self.runtime.engine
        self.queries, self.commands = self.runtime.queries, self.runtime.commands

    def grant_officer_care(self, *roles: Role) -> None:
        """E02's Visibility (SYSTEM 8.3) granting every routine object to sessions holding one of `roles` (the crew
        medical officer by default), as a current care relationship or ground assignment would; without it an
        officer or ground session reads and ends no one else's override."""
        from dataclasses import replace

        granted = frozenset(roles or (Role.CREW_MEDICAL_OFFICER,))

        class OfficerCare:
            def can_read(self, session: SessionContext, object_ref: str) -> bool:
                return bool(granted & session.roles)

        self.runtime.assemble(replace(self.engine.deps, visibility=OfficerCare()))
        self.engine = self.runtime.engine
        self.queries, self.commands = self.runtime.queries, self.runtime.commands

    def trusted(self) -> bool:
        with self.db.snapshot() as view:
            return self.clocks.clearing_allowed(view)

    def _providers(self) -> None:
        def provide[P](port: type[P], value: P) -> None:
            self.registry.provide(port, lambda _: value)

        provide(UnitOfWorkFactory, self.db)
        provide(ReadSnapshotFactory, self.db)
        provide(Clock, self.clock)
        provide(MissionClock, self.mission_clock)
        provide(IdFactory, self.ids)
        provide(HubSettings, self.settings)
        provide(IdempotencyStore, self.ledger)
        provide(CommandReceipts, self.ledger)
        provide(HealthState, self.health)
        provide(MissionStatePort, self.mission)
        provide(RosterPort, self.roster)
        provide(LocationPort, self.locations)
        provide(ClockCorrections, self.clocks)
        provide(HubClock, HarnessHubClock(self.clocks))
        self.registry.provide(RecordFacts, lambda _: self.records)

    def _handlers(self) -> None:
        clocks = self.clocks

        def deadline(uow: UnitOfWork, job: ScheduledJob) -> datetime | None:
            # The resolver create_app composes (app/assembly.py _wire_declarations), copied for this station.
            if isinstance(job.deadline, AbsoluteDeadline):
                return job.deadline.at
            normalized = clocks.normalize(uow, job.deadline.anchor)
            if (
                normalized.state != "known"
                or normalized.corrected_at is None
                or normalized.uncertainty_ms is None
            ):
                return None
            return (
                normalized.corrected_at
                + job.deadline.duration
                + timedelta(milliseconds=normalized.uncertainty_ms)
            )

        self.db.scheduler.deadline = deadline

        def corrected(uow: UnitOfWork, event: ClockCorrected) -> None:
            self.db.scheduler.recalculate_all(uow)

        self.db.bus.subscribe(ClockCorrected, corrected, name="platform.scheduler.clock_correction")
        for name, fn in self.registry.declarations.get("scheduler_handlers", {}).items():
            self.db.scheduler.register(str(name), cast(Callable[[UnitOfWork, ScheduledJob], None], fn))
        for name, declaration in self.registry.declarations.get("durable_subscribers", {}).items():
            types, handler = cast(
                tuple[frozenset[EventType], Callable[[UnitOfWork, StoredEvent], None]], declaration
            )
            self.subscriptions.register(str(name), types, handler)
        for name, declaration in self.registry.declarations.get("subscriptions", {}).items():
            event_type, domain_handler = cast(
                tuple[type[DomainEvent], Callable[[UnitOfWork, DomainEvent], None]], declaration
            )
            self.db.bus.subscribe(event_type, domain_handler, name=str(name))

    def advance(self, to: datetime) -> None:
        elapsed = to - self.clock.at
        self.clock.monotonic += max(0, elapsed // datetime.resolution) * 1000
        self.clock.at = to

    def dispatch(self) -> int:
        with self.db.begin(
            actor=Actor("engine", "engine_routine"), correlation_id=self.ids.new("cor")
        ) as uow:
            return self.db.scheduler.dispatch(uow, limit=self.settings.integer("scheduler.batch_size"))

    def deliver(self) -> int:
        total = 0
        page = self.settings.integer("events.page_size")
        for _ in range(self.settings.integer("storage.max_rows") // page + 1):
            count = self.subscriptions.process("routine.inputs")
            total += count
            if count < page:
                return total
        raise RuntimeError("routine test delivery exceeded its storage bound")

    def rows(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> tuple[tuple[SqlValue, ...], ...]:
        with self.db.snapshot() as view:
            return view.connection.rows(sql, parameters, limit=self.settings.integer("storage.max_rows"))

    def emit(self, kind: EventType, body: dict[str, object], *, at: datetime | None = None) -> StoredEvent:
        with self.db.begin(
            actor=Actor("engine", "engine_producer"), correlation_id=self.ids.new("cor")
        ) as uow:
            return uow.emit(
                OutboxEvent(
                    kind,
                    at or uow.now,
                    EventScope("crew", None, "station_leo"),
                    self.release.release_id,
                    uow.correlation_id,
                    canonical_json(body),
                    Audience(
                        "test.producer", "producer_1", AudienceProjectionKey("test", kind.value, 1), "1"
                    ),
                )
            )

    def answer(
        self, question_id: str, *, person_id: str = "person_mara", answer_id: str = "answer_1"
    ) -> None:
        from crew_health.records.domain.answers import AnswerRecord

        now = self.clock.now()
        mode = "SIM" if self.mission.facts is None else self.mission.facts.mission_mode
        answer = AnswerRecord(
            answer_id,
            question_id,
            self.release.version_of(ConfigName.QUESTIONNAIRES),
            "declined",
            None,
            None,
            None,
            Actor("person", person_id),
            now,
            mode,
            None,
            person_id,
            "mission_1",
            None,
            self.release.release_id,
            TimeInput(now, "clock_1", "segment_1", 1, "hub_corrected", mode),
            now,
            None,
        )
        with self.db.begin(actor=answer.actor, correlation_id=self.ids.new("cor")) as uow:
            self.records.add_answer(uow, answer)
            self.runtime.evaluate(
                uow,
                EvaluationInput(
                    "routine",
                    "1",
                    answer.answer_id,
                    answer.observed,
                    answer.release_id,
                    answer.patient_id,
                    1,
                    "evaluation_" + answer.answer_id,
                ),
            )

    def warning_body(self, warning_id: str, definition_id: str, **changes: object) -> dict[str, object]:
        name = "assistant-sleep.json" if changes.get("raised_by") == "assistant" else "co2-station.json"
        raw = (REPO / "contracts/api/examples/warnings" / name).read_text(encoding="utf-8")
        body = cast(dict[str, object], json.loads(raw)["value"])
        body.update(
            {
                "warning_id": warning_id,
                "definition_id": definition_id,
                "config_release_id": self.release.release_id,
                "origin_mode": self.mission.facts.mission_mode if self.mission.facts else "SIM",
                "raised_at": instant(self.clock.now()),
                "evidence_time": instant(self.clock.now()),
                "routine_override_ids": [],
            }
        )
        body.update(changes)
        return body

    def raise_warning(
        self, warning_id: str = "wrn_co2_1", definition_id: str = "co2_above_limit", **changes: object
    ) -> StoredEvent:
        return self.emit(EventType.WARNING_RAISED, self.warning_body(warning_id, definition_id, **changes))

    def clear_warning(
        self, warning_id: str = "wrn_co2_1", reason: str = "condition_false", **changes: object
    ) -> StoredEvent:
        body = self.warning_body(
            warning_id,
            "co2_above_limit",
            state="cleared",
            clear_reason=reason,
            cleared_at=instant(self.clock.now()),
            **changes,
        )
        return self.emit(EventType.WARNING_CLEARED, body)

    def overlay(
        self,
        overlay_id: str = "ovl_sleep_1",
        *,
        person_id: str = "person_mara",
        kind: str = "sleep",
        state: str = "active",
        revision: int = 1,
        planned_start: datetime | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        **changes: object,
    ) -> StoredEvent:
        body: dict[str, object] = {
            "overlay_id": overlay_id,
            "revision": revision,
            "person_id": person_id,
            "overlay": kind,
            "state": state,
            "planned_start": None if planned_start is None else instant(planned_start),
            "started_at": None if started_at is None else instant(started_at),
            "ended_at": None if ended_at is None else instant(ended_at),
            "source": {"kind": "person", "ref_id": person_id},
            "location": None,
        }
        body.update(changes)
        return self.emit(EventType.OVERLAY_CHANGED, body)

    def mission_event(
        self,
        event_type: str = "landing",
        *,
        event_id: str = "evt_landing_1",
        occurred_at: datetime | None = None,
        person_ids: tuple[str, ...] = (),
        **changes: object,
    ) -> StoredEvent:
        body: dict[str, object] = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": instant(occurred_at or self.clock.now()),
            "recorded_at": instant(self.clock.now()),
            "recorded_by": {"kind": "person", "id": "person_mara", "display_name": "Mara"},
            "origin_mode": self.mission.facts.mission_mode if self.mission.facts else "SIM",
            "environment_id": self.mission.facts.active_environment_id
            if self.mission.facts
            else "station_leo",
            "person_ids": list(person_ids),
            "location": None,
            "note": None,
        }
        body.update(changes)
        return self.emit(EventType.MISSION_EVENT_RECORDED, body)


def station(
    directory: Path,
    *,
    at: datetime | None = None,
    mode: Literal["LIVE", "REPLAY", "SIM"] = "SIM",
    initialize: bool = True,
    values: dict[str, object] | None = None,
) -> RoutineHarness:
    return RoutineHarness(directory, at=at, mode=mode, initialize=initialize, values=values)
