"""Test-only composition for E32 medicines: E01's real SQLiteDatabase, command ledger and audit on a tmp file, the
real medicines module registered into E01's DeclarationRegistry and the repository's own configuration (config/
treatments.json, medicines.json, pharmacy formulary and lots, the kit map). Fakes stand in for providers not on main
(E02 sessions and consent, E23 mission, E28 plan medicines; E31 authority and the clock corrections are fakes of
ports that are on main, so a test can set them). Production never binds anything here."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.routing import APIRouter

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.kernel.clock import (
    Clock,
    ClockCorrected,
    ClockCorrection,
    ClockCorrections,
    NormalizedTime,
    TimeInput,
)
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import TaskQueue, TaskRegistration
from crew_health.kernel.testing import MemoryIds, MemoryRelease
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.medicines import module as medicines_module
from crew_health.medicines.adapters import engine_result
from crew_health.medicines.adapters.potency import BUNDLE_FILES
from crew_health.medicines.adapters.weights import RecordsObservations, RecordsWeightReader
from crew_health.medicines.application import settings as medicines_settings
from crew_health.medicines.application.ports import (
    ConsentQueries,
    MedicationRecordReader,
    PlanMedicines,
    PotencyEngine,
    RunOutcome,
    WeightReader,
)
from crew_health.medicines.application.use_cases import Deps
from crew_health.medicines.domain.model import LotSummary
from crew_health.platform import module as platform_module
from crew_health.platform.adapters.tasks import TaskRepository
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings
from crew_health.records.application.seams import RecordsStore
from crew_health.roles.application.ports import (
    AuthorityDecision,
    ClinicalAuthorityQuery,
    DoseAuthorityContext,
)
from crew_health.supplies.application.ports import MissionFacts, MissionStatePort

with warnings.catch_warnings():  # starlette's own deprecation notice on importing its test client, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config"
SCHEMAS = REPO / "contracts/api/schemas"
MIGRATION = REPO / "hub/src/crew_health/medicines/adapters/migrations/0001_medicines.sql"
EVENTS_MIGRATION = REPO / "hub/src/crew_health/events/adapters/migrations/0001_events.sql"
FIXTURE = Path(__file__).parent / "fixtures/engine-run.json"
SCHEMA = engine_result.validator()
ENGINE_PYTHON = REPO / ".venv/bin/python"  # the root environment carries pharmacy, numpy and scipy
NOW = datetime(
    2028, 3, 1, 8, 0, tzinfo=UTC
)  # every lot sits in moon_base_pharmacy, before any labeled expiry
CMO, CREW_B, SUBJECT, OPERATOR = "person_cmo", "person_b", "person_a", "person_op"


def medicines_values(config_dir: Path, engine_python: str = "") -> dict[str, object]:
    return {
        "medicines.max_window_rows": 1000,
        "medicines.future_tolerance_seconds": 300,
        "medicines.clock_backward_tolerance_seconds": 60,
        "medicines.clock_forward_tolerance_seconds": 60,
        "medicines.config_dir": str(config_dir),
        "medicines.engine_python": engine_python,
        "medicines.assessment_timeout_seconds": 120,
        "medicines.worker_memory_limit_bytes": 4 * 1024**3,
        "medicines.memory_sample_ms": 50,
        "medicines.retained_unreferenced_runs": 2,
        "medicines.max_inputs_bytes": 1024 * 1024,
    }


class Settings:
    """E01's hermetic test settings plus the medicines.* settings validated against this module's own specs."""

    def __init__(self, config_dir: Path, engine_python: str = "", **own: object) -> None:
        self.base = test_settings()
        values = {**medicines_values(config_dir, engine_python), **own}
        self.own = ValidatedSettings(medicines_settings.specs(), values)

    def _pick(self, key: str) -> HubSettings:
        return self.own if key.startswith("medicines.") else self.base

    def integer(self, key: str) -> int:
        return self._pick(key).integer(key)

    def quantity(self, key: str) -> Quantity:
        return self._pick(key).quantity(key)

    def boolean(self, key: str) -> bool:
        return self._pick(key).boolean(key)

    def text(self, key: str) -> str:
        return self._pick(key).text(key)


@dataclass
class StepClock:
    """Wall time `at`; monotonic time is the real elapsed time, so a wall clock jump (`jump`) moves `at` alone."""

    at: datetime = NOW
    epoch: str = "epoch_1"
    jumped: timedelta = timedelta(0)

    def now(self) -> datetime:
        return self.at

    def monotonic_ns(self) -> int:
        return int((self.at - NOW - self.jumped).total_seconds() * 1e9)

    def process_epoch(self) -> str:
        return self.epoch

    def jump(self, by: timedelta) -> None:
        self.at, self.jumped = self.at + by, self.jumped + by


@dataclass
class Mission:
    facts: MissionFacts | None

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return self.facts

    def day_end(self, instant: datetime) -> datetime:
        return instant


@dataclass
class Authority:
    """E31 ClinicalAuthorityQuery fake: `officers` are crew medical officers (never for their own case); `acting`
    maps (person, subject) to the accepted medical_officer assignment that makes the person acting for that subject."""

    officers: set[str] = field(default_factory=lambda: {CMO})
    acting: dict[tuple[str, str], str] = field(default_factory=dict)

    def authority(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        subject_person_id: str,
        action: str,
        dose: DoseAuthorityContext | None,
        at: datetime,
    ) -> AuthorityDecision:
        if person_id == subject_person_id:
            return AuthorityDecision("none", None, "subject")
        if person_id in self.officers:
            return AuthorityDecision("crew_medical_officer", None, "rank")
        assignment = self.acting.get((person_id, subject_person_id))
        if assignment is not None:
            return AuthorityDecision("acting_medical_officer", assignment, "acting")
        return AuthorityDecision("none", None, "no_role")


@dataclass
class Consent:
    """E02 ConsentQueries fake: (subject, recipient, field) grants, `*` for any. By default every subject shared
    every medicines field with the crew medical officer, so tests that are not about privacy read as before."""

    grants: set[tuple[str, str, str]] = field(default_factory=lambda: {("*", CMO, "*")})

    def allows(
        self,
        view: ReadSnapshot | UnitOfWork,
        *,
        subject_person_id: str,
        recipient: str,
        scope: tuple[str, str],
    ) -> bool:
        return any(
            s in ("*", subject_person_id)
            and r == recipient
            and f in ("*", scope[1])
            and scope[0] == "medicines"
            for s, r, f in self.grants
        )


@dataclass
class Plans:
    """E28 fake: (subject, condition, product) triples an active plan step names."""

    named: set[tuple[str, str, str]] = field(default_factory=set)

    def names_medicine(
        self, view: ReadSnapshot | UnitOfWork, *, subject_person_id: str, condition_id: str, product_id: str
    ) -> bool:
        return (subject_person_id, condition_id, product_id) in self.named


@dataclass
class Corrections:
    """The clock module's correction journal (SYSTEM 5.2): `record` appends a correction at the next version."""

    journal: list[ClockCorrection] = field(default_factory=list)

    def record(self, at: datetime) -> None:
        version = len(self.journal) + 1
        self.journal.append(
            ClockCorrection(f"corr_{version}", "hub", "segment_1", version, at, None, 0, 0, at, "operator")
        )

    def history(
        self, snapshot: ReadSnapshot | UnitOfWork, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]:
        return tuple(c for c in self.journal if c.version > after_version)[:limit]

    def apply(self, uow: UnitOfWork, correction: ClockCorrection) -> ClockCorrected:
        raise NotImplementedError("medicines reads the correction journal only")

    def normalize(self, snapshot: ReadSnapshot, value: TimeInput) -> NormalizedTime:
        raise NotImplementedError("medicines reads the correction journal only")

    def elapsed(self, snapshot: ReadSnapshot, start: TimeInput, end: TimeInput) -> timedelta | None:
        raise NotImplementedError("medicines reads the correction journal only")


def session(person: str, roles: frozenset[Role]) -> SessionContext:
    return SessionContext("session_" + person, "crew", Actor("person", person), person, roles)


SESSIONS: Mapping[str, SessionContext] = {
    "cmo": session(CMO, frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW})),
    "crew_b": session(CREW_B, frozenset({Role.CREW})),
    "subject": session(SUBJECT, frozenset({Role.CREW})),
    "subject_cmo": session(SUBJECT, frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW})),
    "operator": session(OPERATOR, frozenset({Role.OPERATOR})),
}


@dataclass
class Sessions:
    def session(self, request: Request, operation_id: str) -> SessionContext:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        found = SESSIONS.get(token)
        if found is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return found


def kit_map_release() -> MemoryRelease:
    raw = (CONFIG / "kit-map.json").read_bytes()
    document = ConfigDocumentView(ConfigName.KIT_MAP, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release_1", {ConfigName.KIT_MAP: document})


CONFIRMED_AS: frozenset[str | int | bool] = frozenset({"crew_medical_officer", "acting_medical_officer"})


def audit_policies() -> tuple[AuditPolicy, ...]:
    """The audit entries this module writes (m11: registered by the harness until E01's registry hooks merge)."""
    return (
        AuditPolicy("medicines.administered", {"confirmed_as": CONFIRMED_AS}, frozenset(), {}),
        AuditPolicy(
            "medicines.administered_acting",
            {"confirmed_as": CONFIRMED_AS},
            frozenset({"role_assignment"}),
            {"role_assignment": frozenset({"role_assignment"})},
        ),
        AuditPolicy("medicines.corrected", {}, frozenset(), {}),
    )


def config_copy(directory: Path, edit: Callable[[dict[str, object], dict[str, object]], None]) -> Path:
    """A copy of config/ in which `edit(treatments, medicines)` changed the two medicines documents."""
    target = directory / "config"
    shutil.copytree(CONFIG, target)
    treatments = json.loads((target / "treatments.json").read_text(encoding="utf-8"))
    policy = json.loads((target / "medicines.json").read_text(encoding="utf-8"))
    edit(treatments, policy)
    (target / "treatments.json").write_text(json.dumps(treatments), encoding="utf-8")
    (target / "medicines.json").write_text(json.dumps(policy), encoding="utf-8")
    return target


def fixture_config(target: Path) -> Path:
    """config/ with the pharmacy inventory narrowed to the recorded run's lots, so the default FakeEngine run is a
    complete, valid assessment of the bundle (E32 5.3: every lot of the bundle is assessed)."""
    if not target.exists():
        shutil.copytree(CONFIG, target)
        (target / "pharmacy" / "lots.json").write_bytes(fixture_bundle()["lots"])
    return target


def lunar(mode: str = "SIM") -> MissionFacts:
    return MissionFacts("mission_1", mode, "lunar_surface", 180, "node_1")  # type: ignore[arg-type]


@dataclass
class FakeEngine:
    """PotencyEngine stand-in: answers every run with `outcome` (default: the recorded run of the fixture, whose
    inputs had no assays, inspections or readings) and counts the runs."""

    outcome: RunOutcome | None = None
    runs: int = 0
    # Multiplies every potency estimate of the recorded run: 0.98 puts the upper estimates (about 100.6 % of label)
    # at or under label, so a lot counts at its labeled amount (E32 3.6 max(1, u)) and proposals exist at all.
    scale: float = 1.0

    def assess(self, bundle: Mapping[str, bytes], inputs: bytes) -> RunOutcome:
        self.runs += 1
        if self.outcome is not None:
            return self.outcome
        result = json.loads(FIXTURE.read_text(encoding="utf-8"))["result"]
        as_of = json.loads(inputs)["as_of_utc"]
        result["as_of_utc"] = as_of
        for assessment in result["assessments"]:  # as a run at this hub clock would state it
            assessment["decision"]["as_of_utc"] = as_of
            for estimate in assessment["estimates"]:
                for quantile in estimate["quantiles"]:
                    quantile["value"] *= self.scale
                interval = estimate["coverage_interval"]
                interval["low"], interval["high"] = (
                    interval["low"] * self.scale,
                    interval["high"] * self.scale,
                )
        return RunOutcome("completed", json.dumps(result, sort_keys=True).encode(), None, "")

    def replay(self, bundle: Mapping[str, bytes], inputs: bytes, recorded: bytes) -> RunOutcome:
        return RunOutcome("completed", b'{"match":true}', None, "")

    def check(self, bundle: Mapping[str, bytes]) -> RunOutcome:
        return RunOutcome("completed", b'{"ok":true}', None, "")

    def summaries(
        self, run_id: str, result: bytes, bundle: Mapping[str, bytes], as_of: datetime
    ) -> Mapping[str, LotSummary]:
        return engine_result.summaries(SCHEMA, run_id, result, bundle, as_of)


FIXTURE_LOTS = ("lot-apap-001", "lot-pmz-tab-001")  # the lots the recorded engine run assessed


def fixture_bundle(config_dir: Path = REPO / "config") -> dict[str, bytes]:
    """The pharmacy bundle with lots.json narrowed to the recorded run's two lots."""
    files = {member: (config_dir / "pharmacy" / name).read_bytes() for member, name in BUNDLE_FILES.items()}
    lots = json.loads(files["lots"])
    lots["lots"] = [lot for lot in lots["lots"] if lot["lot_id"] in FIXTURE_LOTS]
    files["lots"] = json.dumps(lots).encode()
    return files


def fixture_summaries(run_id: str) -> Mapping[str, LotSummary]:
    result = json.loads(FIXTURE.read_text(encoding="utf-8"))["result"]
    as_of = datetime.fromisoformat(result["as_of_utc"].replace("Z", "+00:00"))
    return engine_result.summaries(SCHEMA, run_id, json.dumps(result).encode(), fixture_bundle(), as_of)


@dataclass
class Station:
    db: SQLiteDatabase
    clock: StepClock
    mission: Mission
    authority: Authority
    resolver: Resolver
    client: TestClient
    runtime_deps: Callable[[], Deps]
    tasks: TaskService
    consent: Consent
    plans: Plans
    corrections: Corrections
    registry: DeclarationRegistry = field(
        default_factory=lambda: DeclarationRegistry(max_ports=1, max_hooks=1)
    )

    @property
    def deps(self) -> Deps:
        return self.runtime_deps()

    def assign_acting(self, person: str, subject: str, assignment: str = "rol_acting_1") -> None:
        """E31 made `person` the accepted acting medical officer for `subject`: the assignment record exists (roles
        replicated it, so the audit can reference it) and the authority query answers acting."""
        with sqlite3.connect(self.db.path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO platform_reference VALUES ('role_assignment', ?)", (assignment,)
            )
        self.authority.acting[(person, subject)] = assignment

    def use_engine(self, engine: PotencyEngine) -> None:
        """Swap the bound worker (white box: tests that do not spawn the pharmacy child)."""
        self.rebind(engine=engine)

    def rebind(self, **changes: object) -> None:
        runtime = self.resolver.require(MedicationRecordReader)._runtime  # type: ignore[type-abstract,attr-defined]
        runtime.deps = replace(runtime.deps, **changes)

    def drain(self, *, limit: int = 8) -> int:
        """The production supervisor's loop (E01 adds it): claim, execute outside a transaction, complete."""
        done = 0
        for _ in range(limit):
            with self.db.begin(actor=Actor("engine", "supervisor"), correlation_id="claim") as uow:
                leases = self.tasks.claim(
                    uow, worker_id="worker_1", handlers=frozenset({"medicines.assessment"}), limit=1
                )
            if not leases:
                return done
            (lease,) = leases
            registration = self.registrations[lease.task.handler]
            result = registration.handler.execute(lease)
            with self.db.begin(actor=Actor("engine", "supervisor"), correlation_id="complete") as uow:
                self.tasks.complete(uow, lease, result)
            done += 1
        return done

    @property
    def registrations(self) -> Mapping[str, TaskRegistration]:
        found = self.registry.declarations["tasks"]
        return {str(k): v for k, v in found.items()}  # type: ignore[misc]

    def events(self, prefix: str = "medic") -> list[dict[str, Any]]:
        """The stored envelopes of every medicines event, in order."""
        rows = self.rows("SELECT envelope FROM events_outbox ORDER BY seq")
        found = [json.loads(cast(bytes, r[0])) for r in rows]
        for event in found:
            event["data"] = json.loads(event["data_json"])
        return [e for e in found if str(e["type"]).startswith(prefix)]

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.db.path) as connection:
            return list(connection.execute(sql).fetchall())


def build_station(
    directory: Path,
    *,
    config_dir: Path | None = None,
    mission: MissionFacts | None = None,
    clock: StepClock | None = None,
    engine: PotencyEngine | None = None,
    engine_python: str = "",
    queue: bool = True,
    records: object | None = None,
    own: Mapping[str, object] | None = None,
) -> Station:
    clock = clock or StepClock()
    directory.mkdir(parents=True, exist_ok=True)
    config_dir = config_dir or CONFIG
    settings = Settings(config_dir, engine_python, **(own or {}))
    starts = len(list(directory.glob("start-*")))  # a restart continues the ID sequence, as UUID7 IDs would
    (directory / f"start-{starts}").touch()
    db = SQLiteDatabase(
        directory / "hub.sqlite",
        clock=clock,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(starts * 100_000, (starts + 1) * 100_000))),
        release=kit_map_release(),
        settings=settings,
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=not (directory / "hub.sqlite").exists(),
    )
    raw = MIGRATION.read_text(encoding="utf-8")
    db.migrate_tracked("medicines.0001", hashlib.sha256(raw.encode()).hexdigest(), raw)
    events = EVENTS_MIGRATION.read_text(encoding="utf-8")
    db.migrate_tracked("events.0001", hashlib.sha256(events.encode()).hexdigest(), events)
    db.event_sink = EventRepository(
        settings=settings, ids=db.ids, validator=EventValidator(schema_directory=SCHEMAS)
    ).append
    for policy in audit_policies():
        db.register_audit_policy(policy)
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    fake_mission, authority = Mission(mission if mission is not None else lunar()), Authority()
    consent, plans, corrections = Consent(), Plans(), Corrections()
    registry.provide(SQLiteDatabase, lambda _: db)
    registry.provide(HubSettings, lambda _: db.settings)  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda _: db.ids)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda _: clock)  # type: ignore[type-abstract]
    registry.provide(MissionStatePort, lambda _: fake_mission)  # type: ignore[type-abstract]
    registry.provide(ClinicalAuthorityQuery, lambda _: authority)  # type: ignore[type-abstract]
    registry.provide(ConsentQueries, lambda _: consent)  # type: ignore[type-abstract]
    registry.provide(PlanMedicines, lambda _: plans)  # type: ignore[type-abstract]
    registry.provide(ClockCorrections, lambda _: corrections)  # type: ignore[type-abstract]
    registry.provide(medicines_module.RequestAuthenticator, lambda _: Sessions())  # type: ignore[type-abstract]
    platform_module.register(registry)
    medicines_module.register(registry)
    service = TaskService(
        settings=db.settings,
        repository=TaskRepository(),
        ids=db.ids,
        registrations=tuple(registry.declarations["tasks"].values()),  # type: ignore[arg-type]
        authorize=lambda view, session, task: session.actor == task.actor,
        authorize_completion=lambda uow, task: True,
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=lambda view, a, b: b.original_at - a.original_at,
    )
    if queue:
        registry.provide(TaskQueue, lambda _: service)  # type: ignore[type-abstract]
    if records is not None:
        registry.provide(cast(type[object], RecordsStore), lambda _: records)
        registry.provide(  # the assembly's binding of records' store to the medicines weight port
            cast(type[object], WeightReader),
            lambda r: RecordsWeightReader(
                cast(RecordsObservations, r.require(cast(type[object], RecordsStore)))
            ),
            requires=(cast(type[object], RecordsStore),),
        )
    resolver = registry.resolve()
    for _, startup in sorted(registry.declarations.get("startup", {}).items(), key=lambda item: str(item[0])):
        cast(
            Callable[[], None], startup
        )()  # the assembly runs these per phase; the release is active already
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        if isinstance(router, APIRouter):
            app.include_router(router)
    station = Station(
        db,
        clock,
        fake_mission,
        authority,
        resolver,
        TestClient(app),
        _runtime_deps(resolver),
        service,
        consent,
        plans,
        corrections,
        registry,
    )
    if engine is not None or not engine_python:  # with an interpreter the real worker stays bound
        station.use_engine(engine if engine is not None else FakeEngine())
    return station


def _runtime_deps(resolver: Resolver) -> Callable[[], Deps]:
    """The bound Deps, reached through the provided port's runtime (white-box access for assertions only)."""
    provided = resolver.require(MedicationRecordReader)  # type: ignore[type-abstract]
    return provided._runtime.need  # type: ignore[attr-defined,no-any-return]


def headers(token: str = "cmo", key: str = "key-00000001", **extra: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key, **extra}
