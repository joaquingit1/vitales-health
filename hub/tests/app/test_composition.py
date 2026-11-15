"""SYSTEM 6 composition: create_app assembles every first-party module on this checkout from the generated static
import list, verified against the build manifest, over a real SQLite store and the package's configuration.

Only epics that are not built yet are stood in for, each by a small explicit fake: E02 sessions and the per-module
request authenticators, the E02 roster and person directory, E23 mission facts, overlays and day labels."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from crew_health.app.assembly import BuildCatalog, ConfigHttpMetadata, Runtime, create_app
from crew_health.app.cli import TimeConfirmation, confirm_time
from crew_health.app.installed_modules import REGISTRATIONS
from crew_health.app.registry import DeclarationRegistry, ModuleRegistry, Resolver, StartupPhase
from crew_health.app.station import HubResources, bootstrap_release, config_artifacts
from crew_health.clock.application.clock import PersistentClocks
from crew_health.config.adapters.repository import PinnedRelease
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.loader import required_contributors
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.events.module import EventAccess
from crew_health.help.application.ports import BroadcastRolesSource
from crew_health.kernel.audit import AuditEntry, AuditPolicy
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ReleaseCheck, TemplateRenderer
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.replication import (
    ProjectionKey,
    ProjectionProvider,
    ProjectionRegistration,
    ReplicaApplier,
    ReplicatedTable,
    ReplicationAllowlist,
)
from crew_health.kernel.scheduler import HealthState, WorkerLifecycle
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.testing import MemoryReleaseCheck
from crew_health.kernel.units import Quantity, Unit, UnitRegistry
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
)
from crew_health.kitmap.api.router import RequireRoles
from crew_health.kitmap.application.ports import (
    ConditionRegistryPort,
)
from crew_health.kitmap.application.ports import MissionStatePort as KitMission
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.records.application.ports import ObservationCommands
from crew_health.roles.api.router import RequestAuthenticator as RolesAuthenticator
from crew_health.roles.application.ports import (
    DayLabelPort,
    MissionFacts,
    MissionStatePort,
    OverlayFact,
    OverlayPort,
    RosterMember,
    RosterPort,
)
from crew_health.teach.application.ports import PersonDirectory, PersonRef, RecordRouting
from crew_health.teach.application.ports import RequestAuthenticator as TeachAuthenticator

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.6", "SYSTEM.4")]
ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "hub/src/crew_health"
CREW = (RosterMember("person_sim", "Ana", 0, 1), RosterMember("person_other", "Ben", 1, None))


class Sessions:
    """E02 stand-in: three bearer tokens, one role each."""

    def __init__(self) -> None:
        self.editor = SessionContext(
            "session_editor", "editor", Actor("operator", "operator_sim"), None, frozenset({Role.EDITOR})
        )
        self.crew = SessionContext(
            "session_crew", "crew", Actor("person", "person_sim"), "person_sim", frozenset({Role.CREW})
        )

    def from_headers(self, headers: Mapping[str, str]) -> SessionContext:
        session = {"Bearer editor": self.editor, "Bearer crew": self.crew}.get(
            headers.get("authorization", "")
        )
        if session is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, "composition session fixture")
        return session

    def authenticate(self, request: Request) -> SessionContext:
        return self.from_headers(request.headers)

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        editing = {"createConfigRelease", "activateConfigRelease", "listConfigReleases", "getConfigRelease"}
        if operation_id in editing and Role.EDITOR not in session.roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "editor role required")

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        return True

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == "config.release"


@dataclass(frozen=True, slots=True)
class Visibility:
    sessions: Sessions

    def authorize(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None:
        self.sessions.authorize(session, identity.operation)
        if session.actor.id != identity.actor_ref:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "result owner mismatch")


@dataclass(frozen=True, slots=True)
class Roster:
    def crew(self, at: datetime) -> Sequence[RosterMember]:
        return CREW


@dataclass(frozen=True, slots=True)
class Overlays:
    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]:
        return {person: () for person in person_ids}


@dataclass(frozen=True, slots=True)
class Mission:
    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return MissionFacts("msn_sim", "SIM", "lunar_surface", 180, "node_base")

    def day_end(self, instant: datetime) -> datetime:
        return instant.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DayLabels:
    def inline_label_at(self, instant: datetime) -> str:
        return "day 1"


@dataclass(frozen=True, slots=True)
class People:
    def person(self, person_id: str) -> PersonRef | None:
        member = next((m for m in CREW if m.person_id == person_id), None)
        return None if member is None else PersonRef(member.person_id, member.display_name, True)


@dataclass(frozen=True, slots=True)
class Routing:
    def record_origin(self, person_id: str) -> str:
        return "SIM"

    def record_for(self, person_id: str, origin: str) -> str:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "records routing is E02")


@dataclass(frozen=True, slots=True)
class KitMissionFacts:
    def mission_mode(self, uow: object = None) -> str | None:
        return "SIM"

    def active_environment_id(self, uow: object = None) -> str | None:
        return "station_leo"


@dataclass(frozen=True, slots=True)
class PackageConditions:
    """E10 stand-in: the condition ids of the shipped package inventory."""

    def active_ids(self) -> frozenset[str]:
        rows = json.loads((ROOT / "docs/product/condition-assessments-119.json").read_bytes())
        return frozenset(row["condition_id"] for row in rows)


def candidate(release_id: str) -> CandidateRelease:
    documents = {}
    for name in ConfigName:
        # config/kit-map.json fails check 1 on main (ICL36A/ICL36B break the condition_id pattern): the
        # package example stands in for it until E27 fixes the document.
        if name != ConfigName.KIT_MAP and (ROOT / "config" / (name.value + ".json")).is_file():
            raw = (ROOT / "config" / (name.value + ".json")).read_bytes()
        elif name.value in {"environment-hazards", "routine-schedule"}:
            raw = (ROOT / "docs/product" / (name.value + ".json")).read_bytes()
        elif name == ConfigName.KIT_MAP:
            raw = json.dumps(
                json.loads((ROOT / "docs/product/api/examples/kit-map.example.json").read_bytes())
            ).encode()
        else:
            example = ROOT / "docs/product/api/examples/config" / (name.value + ".example.json")
            raw = json.dumps(json.loads(example.read_bytes())["value"]).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease(release_id, documents)


def summaries() -> dict[ConfigName, str]:
    index = json.loads((ROOT / "docs/product/api/examples/configuration/index.json").read_bytes())
    summaries = {ConfigName(row["name"]): row["basis_summary"] for row in index["value"]["documents"]}
    # The package index predates D12; the 19th document's summary is this fixture's own text.
    summaries[ConfigName.UNIT_MAPPING] = "D12 accepted unit mapping, config/unit-mapping.json"
    return summaries


def registered() -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    for register in REGISTRATIONS:
        register(registry)
    return registry


def settings() -> ValidatedSettings:
    specs = [
        value for value in registered().declarations["settings"].values() if isinstance(value, SettingSpec)
    ]
    # Integers at their registered maximum, switches off and text settings empty (the forecast worker stays off).
    assert {spec.kind for spec in specs} <= {"integer", "boolean", "text"}
    values: dict[str, object] = {
        spec.key: int(spec.maximum or 0)
        if spec.kind == "integer"
        else False
        if spec.kind == "boolean"
        else ""
        for spec in specs
    }
    values["supervisor.poll_ms"] = 50
    return ValidatedSettings(specs, values)


def _constant(value: object, resolver: object) -> object:
    return value


@dataclass
class Station:
    db: SQLiteDatabase
    sessions: Sessions
    configure: Callable[[ModuleRegistry], None]
    resources: HubResources

    def app(self, build: BuildCatalog | None = None) -> FastAPI:
        return create_app(
            database=self.db,
            build=build or BuildCatalog.installed(SOURCE),
            contract_path=ROOT / "contracts/api/openapi.yaml",
            schema_directory=ROOT / "contracts/api/schemas",
            security=self.sessions,
            configure=self.configure,
            bootstrap=partial(bootstrap_release, self.resources),
        )


def station(tmp_path: Path, resources: Path, *, omit: frozenset[type[object]] = frozenset()) -> Station:
    """A hub over the station resources of conftest.py: `config_artifacts` binds every loader check and the
    first start installs the release built from them."""
    db = database(tmp_path / "state")
    db.settings = settings()
    for module in ("config", "clock", "events"):
        db.migrate(
            (SOURCE / module / "adapters/migrations" / ("0001_" + module + ".sql")).read_text(
                encoding="utf-8"
            )
        )
    station_resources = HubResources(resources)
    verified, sessions = config_artifacts(station_resources), Sessions()
    db.event_sink = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"),
    ).append
    own = {
        cast(ReleaseCheck, check).check_id for check in registered().declarations["loader_checks"].values()
    }

    @dataclass(frozen=True, slots=True)
    class RolesAuth:
        def session(self, request: Request, operation_id: str) -> SessionContext:
            return sessions.authenticate(request)

    @dataclass(frozen=True, slots=True)
    class TeachAuth:
        def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext:
            return sessions.from_headers(headers)

    def kit_roles(operation_id: str) -> Callable[[Request], SessionContext]:
        return sessions.authenticate

    stand_ins: dict[type[object], object] = {
        ConfigArtifacts: verified,
        ConfigHttpMetadata: ConfigHttpMetadata(summaries(), lambda view: "SIM", Visibility(sessions)),
        SessionAuthorizer: sessions,
        RosterPort: Roster(),
        OverlayPort: Overlays(),
        MissionStatePort: Mission(),
        DayLabelPort: DayLabels(),
        RolesAuthenticator: RolesAuth(),
        PersonDirectory: People(),
        RecordRouting: Routing(),
        TeachAuthenticator: TeachAuth(),
        RequireRoles: kit_roles,
        KitMission: KitMissionFacts(),
        ConditionRegistryPort: PackageConditions(),
    }

    def configure(registry: ModuleRegistry) -> None:
        for port, value in stand_ins.items():
            if port not in omit:
                registry.provide(port, partial(_constant, value))
        for identifier in sorted(required_contributors() - own):
            registry.loader_check(MemoryReleaseCheck(identifier, lambda candidate: ()))

    return Station(db, sessions, configure, station_resources)


def health(app: FastAPI) -> str:
    async def read() -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            return str((await client.get("/v1/health")).json()["status"])

    return asyncio.run(read())


def correct_hub_clock(runtime: Runtime) -> None:
    """The crew's time confirmation through the production command; the supervisor's clock sweep then unfreezes
    clearing."""
    confirm_time(runtime, TimeConfirmation(0, 500, "operator_sim"))


def test_every_installed_module_composes_and_serves_a_cross_module_path(
    tmp_path: Path, station_resources: Path
) -> None:
    hub = station(tmp_path, station_resources)
    app = hub.app()
    runtime = app.state.runtime
    assert runtime.serving, runtime.fault_code
    assert runtime.completed_phases == list(StartupPhase)
    installed = {path.parent.name for path in SOURCE.glob("*/module.py")}
    assert {fn.__module__.split(".")[1] for fn in BuildCatalog.installed(SOURCE).registrations} == installed
    # First start: clearing stays frozen until the hub's own clock is corrected and swept, and health says so.
    assert health(app) == "degraded"
    correct_hub_clock(runtime)
    assert health(app) == "ok"
    resolver = runtime.resolver
    # E01 providers the feature modules consume resolve by class (SYSTEM 6.4).
    assert resolver.require(UnitRegistry).convert(Quantity(Decimal(1), Unit("h")), Unit("min")) == Quantity(
        Decimal(60), Unit("min")
    )
    renderer = resolver.require(TemplateRenderer)
    unknown = json.loads(candidate("x").documents[ConfigName.UI_COPY].raw)["designed_states"]["unknown"]
    assert renderer.render("ui-copy/duration_words/minutes", "{n} min", {}) == unknown + " min"
    # records' real ObservationCommands reads canonical units from the active release's unit-mapping.
    observations = resolver.require(ObservationCommands)
    assert observations.canonical_unit("pulse") == Unit("/min")
    with pytest.raises(ProblemError) as refused:
        observations.canonical_unit("pulse_rate")
    assert refused.value.code == ErrorCode.INVALID_UNIT
    # roles provides help's BroadcastRolesSource; an unknown help session lists nobody.
    assert list(resolver.require(BroadcastRolesSource).roles_for_session("help_absent")) == []

    async def drive() -> str:
        editor, crew = {"Authorization": "Bearer editor"}, {"Authorization": "Bearer crew"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            assert (await client.get("/v1/health")).json()["status"] == "ok"
            document = await client.get("/v1/config/ui-copy", headers=editor)
            assert document.status_code == 200, document.text
            changed = document.json()["document"]
            changed["designed_states"]["unknown"] = "Not known yet"
            proposed = await client.post(
                "/v1/config/releases",
                headers={**editor, "Idempotency-Key": "composition_proposal"},
                json={"documents": [{"name": "ui-copy", "document": changed}]},
            )
            assert proposed.status_code == 201, proposed.text
            release_id = proposed.json()["release_id"]
            activated = await client.post(
                f"/v1/config/releases/{release_id}/activate",
                headers={
                    **editor,
                    "Idempotency-Key": "composition_activate",
                    "If-Match": proposed.headers["ETag"],
                },
            )
            assert activated.status_code == 200, activated.text
            kit = await client.get("/v1/kit-map", headers=crew)
            assert kit.status_code == 200, kit.text
            assert kit.headers["Config-Release"] == release_id
            return str(release_id)

    release_id = asyncio.run(drive())
    # Receipts name the canonical command target of their identity (SYSTEM 5.8), never the raw request URL.
    with hub.db.snapshot() as view:
        receipts = {
            str(key): json.loads(cast(bytes, raw))
            for key, raw in view.connection.rows(
                "SELECT command_key, receipt FROM platform_command_ledger WHERE command_key IN (?, ?)",
                ("composition_proposal", "composition_activate"),
                limit=2,
            )
        }
    assert {key: (value["operation_id"], value["target"]) for key, value in receipts.items()} == {
        "composition_proposal": ("createConfigRelease", "/v1/config/releases"),
        "composition_activate": ("activateConfigRelease", f"/v1/config/releases/{release_id}/activate"),
    }
    # The activated release reaches the module-facing renderer with no restart.
    assert renderer.render("ui-copy/duration_words/minutes", "{n} min", {}) == "Not known yet min"
    assert runtime.supervisor is not None and not runtime.supervisor.running


def test_health_is_degraded_while_any_component_is_degraded_or_an_evaluation_is_pending(
    tmp_path: Path,
    station_resources: Path,
) -> None:
    hub = station(tmp_path, station_resources)
    app = hub.app()
    runtime = app.state.runtime
    correct_hub_clock(runtime)
    assert health(app) == "ok"
    components = runtime.resolver.require(HealthState)
    components.mark_degraded("events.subscriber.probe", ErrorCode.INTERNAL_ERROR.value, hub.db.clock.now())
    assert health(app) == "degraded"
    components.mark_ok("events.subscriber.probe")
    assert health(app) == "ok"
    with hub.db.begin(actor=Actor("engine", "engine_probe"), correlation_id="pending_probe") as uow:
        uow.connection.execute(
            "INSERT INTO platform_evaluation_pending VALUES (?,?,?,?,?)",
            ("completion_probe", b"{}", uow.now.isoformat(), 1, ErrorCode.INTERNAL_ERROR.value),
        )
        uow.replicate(
            ReplicationChange(
                None,
                "evaluation",
                "completion_probe",
                "version",
                b"{}",
                (),
                uow.release.release_id,
                Audience(
                    "evaluation.internal",
                    "completion_probe",
                    AudienceProjectionKey("platform", "evaluation", 1),
                    "1",
                ),
            )
        )
    assert health(app) == "degraded"


def test_feature_module_with_absent_optional_provider_starts_and_fails_closed(
    tmp_path: Path, station_resources: Path
) -> None:
    app = station(tmp_path, station_resources, omit=frozenset({RequireRoles})).app()
    assert app.state.runtime.serving, app.state.runtime.fault_code

    async def drive() -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            return (await client.get("/v1/kit-map", headers={"Authorization": "Bearer crew"})).status_code

    assert asyncio.run(drive()) in {401, 403, 503}


def test_unlisted_or_missing_module_is_rejected_at_startup(tmp_path: Path, station_resources: Path) -> None:
    hub = station(tmp_path, station_resources)
    packaged = tmp_path / "packaged/crew_health"
    shutil.copytree(SOURCE, packaged, ignore=shutil.ignore_patterns("__pycache__"))
    unlisted = packaged / "intruder"
    unlisted.mkdir()
    (unlisted / "__init__.py").write_text("", encoding="utf-8")
    (unlisted / "module.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")
    app = hub.app(BuildCatalog.installed(packaged))
    assert not app.state.runtime.serving
    assert app.state.runtime.completed_phases == []
    shutil.rmtree(unlisted)
    shutil.rmtree(packaged / "teach")
    missing = hub.app(BuildCatalog.installed(packaged))
    assert not missing.state.runtime.serving
    assert BuildCatalog.installed(packaged).inventory.keys() >= {"crew_health.teach.module"}


PROBE_TABLE = ReplicatedTable(
    "help_broadcast",
    "help_broadcast",
    "broadcast_id",
    projection=ProjectionKey("help", "broadcast", 1),
    allowed_fields=frozenset({"/state", "/started_at"}),
)


def _declaring(hub: Station, *, projection: bool = True) -> Station:
    """A feature module's declarations, made through the same ModuleRegistry calls its register(...) would use."""
    schema = b'{"type":"object"}'

    def configure(registry: ModuleRegistry) -> None:
        hub.configure(registry)
        registry.audit_policy(
            AuditPolicy(
                "help.probe_recorded",
                {"outcome": frozenset({"ok"})},
                frozenset({"broadcast_id"}),
                {"broadcast_id": frozenset({"help_broadcast"})},
            )
        )
        registry.replicated_table(PROBE_TABLE)
        if projection:
            registry.projection(
                ProjectionRegistration(
                    ProjectionKey("help", "broadcast", 1),
                    schema,
                    hashlib.sha256(schema).hexdigest(),
                    cast(ProjectionProvider, None),
                    cast(ReplicaApplier, None),
                )
            )

    return Station(hub.db, hub.sessions, configure, hub.resources)


def _broadcast(uow: UnitOfWork, broadcast_id: str, *, declare: bool) -> None:
    now = uow.now.isoformat()
    uow.connection.execute(
        "INSERT INTO help_session VALUES (?,1,'closed',NULL,?,NULL,'{}')", ("session_" + broadcast_id, now)
    )
    uow.connection.execute(
        "INSERT INTO help_broadcast VALUES (?,?,'ended',?,'{}')",
        (broadcast_id, "session_" + broadcast_id, now),
    )
    if declare:
        audience = Audience(
            "help.broadcast", broadcast_id, AudienceProjectionKey("help", "broadcast", 1), "1"
        )
        uow.replicate(
            ReplicationChange(
                None, "help_broadcast", broadcast_id, "create", b"{}", (), uow.release.release_id, audience
            )
        )


def test_feature_migrations_run_and_module_declarations_take_effect_in_create_app(
    tmp_path: Path, station_resources: Path
) -> None:
    app = _declaring(station(tmp_path, station_resources)).app()
    runtime: Runtime = app.state.runtime
    assert runtime.serving, runtime.fault_code and runtime.resolver is not None
    db = runtime.database
    with db.snapshot() as view:
        applied = {
            str(row[0]) for row in view.connection.rows("SELECT name FROM platform_migration", (), limit=64)
        }
        tables = view.connection.rows(
            "SELECT name FROM sqlite_schema WHERE name='help_broadcast'", (), limit=1
        )
    feature = {"help.0001", "measurement.0001", "roles.0001", "teach.0001"}
    assert feature <= applied and tables == (("help_broadcast",),)
    # Projection allowlist: only from the ReplicatedTable declarations.
    assert runtime.resolver is not None
    allowlist = runtime.resolver.require(ReplicationAllowlist)
    assert allowlist.allowed(ProjectionKey("help", "broadcast", 1)) == frozenset({"/state", "/started_at"})
    assert allowlist.allowed(ProjectionKey("help", "other", 1)) == frozenset()
    actor = Actor("person", "person_probe")
    # Replicated table: a write without its replication declaration in the same unit of work fails the commit.
    with pytest.raises(ProblemError) as undeclared, db.begin(actor=actor, correlation_id="probe_1") as uow:
        _broadcast(uow, "broadcast_undeclared", declare=False)
    assert undeclared.value.code == ErrorCode.INTERNAL_ERROR
    # Audit policy: the declared event type is accepted with a reference the replication made known.
    with db.begin(actor=actor, correlation_id="probe_2") as uow:
        _broadcast(uow, "broadcast_declared", declare=True)
        refs = {"broadcast_id": "broadcast_declared"}
        uow.audit(
            AuditEntry(
                "audit_probe_1", "help.probe_recorded", 1, actor, refs, "probe_2", None, {"outcome": "ok"}
            )
        )
    with pytest.raises(ProblemError) as unknown, db.begin(actor=actor, correlation_id="probe_3") as uow:
        uow.audit(AuditEntry("audit_probe_2", "help.not_declared", 1, actor, {}, "probe_3", None, {}))
    assert unknown.value.code == ErrorCode.INVALID_VALUE
    with db.snapshot() as view:
        assert view.connection.rows(
            "SELECT event_id FROM platform_audit WHERE event_id LIKE 'audit_probe_%'", (), limit=2
        ) == (("audit_probe_1",),)


def test_replicated_table_naming_an_unregistered_projection_stops_startup(
    tmp_path: Path, station_resources: Path
) -> None:
    app = _declaring(station(tmp_path, station_resources), projection=False).app()
    assert not app.state.runtime.serving
    assert app.state.runtime.completed_phases == []


def test_changed_or_unconventional_feature_migration_is_rejected_at_startup(
    tmp_path: Path, station_resources: Path
) -> None:
    hub = station(tmp_path, station_resources)
    packaged = tmp_path / "packaged/crew_health"
    shutil.copytree(SOURCE, packaged, ignore=shutil.ignore_patterns("__pycache__"))
    migration = packaged / "help/adapters/migrations/0001_help.sql"
    migration.write_text(migration.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    assert not hub.app(BuildCatalog.installed(packaged)).state.runtime.serving
    shutil.copyfile(SOURCE / "help/adapters/migrations/0001_help.sql", migration)
    (packaged / "help/adapters/migrations/0003_help.sql").write_text("SELECT 1;", encoding="utf-8")
    assert not hub.app(BuildCatalog.installed(packaged)).state.runtime.serving


def _worker_station(hub: Station, threads: list[threading.Thread], *, fault: bool = False) -> Station:
    """A feature module whose binding starts a real worker thread and reports it through WorkerLifecycle."""

    def bind(resolver: Resolver) -> None:
        halt = threading.Event()
        thread = threading.Thread(target=halt.wait, name="probe-worker", daemon=True)
        thread.start()
        threads.append(thread)

        def stop(timeout_s: float) -> bool:
            halt.set()
            thread.join(timeout_s)
            return not thread.is_alive()

        resolver.require(WorkerLifecycle).started("probe.worker", stop)  # type: ignore[type-abstract]

    def configure(registry: ModuleRegistry) -> None:
        hub.configure(registry)
        registry.bind("probe.worker", bind)
        if fault:
            registry.bind("zz_probe.fault", _unresolvable)  # bindings run sorted: after the worker

    return Station(hub.db, hub.sessions, configure, hub.resources)


class NeverProvided:
    pass


def _unresolvable(resolver: Resolver) -> None:
    resolver.require(NeverProvided)


def test_runtime_shutdown_stops_module_workers_and_a_failed_startup_stops_them_too(
    tmp_path: Path, station_resources: Path
) -> None:
    threads: list[threading.Thread] = []
    hub = station(tmp_path, station_resources)
    before = set(threading.enumerate())
    runtime: Runtime = _worker_station(hub, threads).app().state.runtime
    assert runtime.serving, runtime.fault_code
    roles = [t for t in threading.enumerate() if t not in before and t.name == "roles-recompute"]
    # assistant.turns: E13's conversation worker, composed once its seams resolve on the station.
    assert set(runtime.workers.names()) == {"probe.worker", "roles.recompute", "assistant.turns"}
    assert threads[0].is_alive()
    assert len(roles) == 1 and roles[0].is_alive()
    assert runtime.shutdown() == ()
    assert not runtime.serving and not threads[0].is_alive() and not roles[0].is_alive()
    faulted: Runtime = _worker_station(hub, threads, fault=True).app().state.runtime
    assert not faulted.serving and len(threads) == 2
    assert all(not thread.is_alive() for thread in threads)


@pytest.mark.trace("SYSTEM.5.7", "SYSTEM.5.10.1", "SYSTEM.5.10.2")
def test_event_delivery_uses_the_declared_audiences_and_the_supervisor_drives_evaluation_and_tasks(
    tmp_path: Path,
    station_resources: Path,
) -> None:
    """Review N5 and our item 3: production consumes the audience rules and local projections modules declare,
    and composes EvaluationService and TaskService so evaluation retry and task lease recovery always run."""
    runtime: Runtime = station(tmp_path, station_resources).app().state.runtime
    assert runtime.serving, runtime.fault_code
    assert runtime.resolver is not None and runtime.supervisor is not None
    declared = set(runtime.registry.declarations["audiences"])
    access = runtime.resolver.require(EventAccess)
    assert {"config.release"} < declared and set(access.audiences) == declared
    assert access.authorizer is runtime.resolver.require(SessionAuthorizer)  # type: ignore[type-abstract]
    assert runtime.resolver.optional(EvaluationService) is not None
    assert runtime.resolver.optional(TaskService) is not None
    assert "tasks.recover" in {step.name for step in runtime.supervisor.steps}
    assert "platform.evaluation.retry" in runtime.database.scheduler.handlers


@pytest.mark.trace("SYSTEM.5.10")
def test_one_transient_health_read_fault_reports_degraded_once_and_never_latches(
    tmp_path: Path, station_resources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review N6: a busy or I/O fault inside getHealth degrades that one response; the hub keeps serving."""
    app = station(tmp_path, station_resources).app()
    runtime: Runtime = app.state.runtime
    correct_hub_clock(runtime)
    assert health(app) == "ok" and runtime.resolver is not None
    clocks = runtime.resolver.require(PersistentClocks)
    original, calls = clocks.clearing_allowed, [0]

    def flaky(view: ReadSnapshot) -> bool:
        calls[0] += 1
        if calls[0] == 1:
            raise StorageUnavailable("locked")
        return original(view)

    monkeypatch.setattr(clocks, "clearing_allowed", flaky)
    assert health(app) == "degraded"
    assert health(app) == "ok"
    assert runtime.serving and runtime.fault_code is None


@pytest.mark.trace("SYSTEM.5.2")
def test_no_write_opens_before_the_clock_guard_and_binding_writes_are_guarded(
    tmp_path: Path, station_resources: Path
) -> None:
    """Review N15: a provider factory that writes during resolution is refused (CAPABILITY_UNAVAILABLE stops the
    startup), while a binding's write already runs under the regression guard and HLC stamping."""
    hub = station(tmp_path, station_resources)

    def writing_factory(resolver: Resolver) -> NeverProvided:
        with hub.db.begin(actor=Actor("engine", "engine_probe"), correlation_id="early_write"):
            pass
        return NeverProvided()

    def with_early_write(registry: ModuleRegistry) -> None:
        hub.configure(registry)
        registry.provide(NeverProvided, writing_factory)

    early = Station(hub.db, hub.sessions, with_early_write, hub.resources).app().state.runtime
    assert not early.serving and early.fault_code == "CAPABILITY_UNAVAILABLE" and early.resolver is None
    guarded: list[bool] = []

    def binding_write(resolver: Resolver) -> None:
        with hub.db.begin(actor=Actor("engine", "engine_probe"), correlation_id="binding_write") as uow:
            guarded.append(uow.connection.rows("SELECT count(*) FROM clock_state", (), limit=1) == ((1,),))

    def with_binding_write(registry: ModuleRegistry) -> None:
        hub.configure(registry)
        registry.bind("aa_probe.write", binding_write)

    runtime = Station(hub.db, hub.sessions, with_binding_write, hub.resources).app().state.runtime
    assert runtime.serving, runtime.fault_code
    assert guarded == [True]  # the regression guard ran first and initialized the durable clock state


@pytest.mark.trace("SYSTEM.5.6", "D12")
def test_a_release_from_before_d12_upgrades_with_only_the_missing_document(
    tmp_path: Path, station_resources: Path
) -> None:
    """Review N16: an active release that lacks unit-mapping is upgraded at start: every active document is kept
    and only unit-mapping is added from the first-start resources, with provenance. Without a bootstrap source the
    hub refuses it with a typed CONFIG_INVALID."""
    hub = station(tmp_path, station_resources)
    first: Runtime = hub.app().state.runtime
    assert first.serving and first.bootstrapped is not None
    old = "release_pre_d12"
    # The stored state of an older hub, written below the storage layer as that hub's own migration left it.
    with closing(sqlite3.connect(hub.db.path)) as connection, connection:
        connection.execute(
            "INSERT INTO config_release SELECT ?,'active',created_at,created_by_json,activated_at,"
            "activated_by_json,NULL,validation_json,NULL,1 FROM config_release WHERE release_id=?",
            (old, first.bootstrapped),
        )
        connection.execute(
            "INSERT INTO config_release_pin SELECT ?,name,version FROM config_release_pin "
            "WHERE release_id=? AND name<>'unit-mapping'",
            (old, first.bootstrapped),
        )
        connection.execute("UPDATE config_activation SET release_id=? WHERE singleton=1", (old,))
    hub = reopen(hub)
    plain = create_app(
        database=hub.db,
        build=BuildCatalog.installed(SOURCE),
        contract_path=ROOT / "contracts/api/openapi.yaml",
        schema_directory=ROOT / "contracts/api/schemas",
        security=hub.sessions,
        configure=hub.configure,
    ).state.runtime
    assert not plain.serving and plain.fault_code == "CONFIG_INVALID"
    hub = reopen(hub)
    upgraded: Runtime = hub.app().state.runtime
    assert upgraded.serving, upgraded.fault_code
    with hub.db.snapshot() as view:
        pins = dict(
            (row[0], row[1])
            for row in view.connection.rows(
                "SELECT p.name,p.version FROM config_release_pin p JOIN config_activation a "
                "ON a.release_id=p.release_id",
                (),
                limit=32,
            )
        )
        before = dict(
            (row[0], row[1])
            for row in view.connection.rows(
                "SELECT name,version FROM config_release_pin WHERE release_id=?", (old,), limit=32
            )
        )
        note = view.connection.rows(
            "SELECT note FROM config_release WHERE release_id=?", (upgraded.bootstrapped,), limit=1
        )
    assert set(pins) == {name.value for name in ConfigName}
    assert {name: pins[name] for name in before} == before
    assert json.loads(str(note[0][0])) == {
        "provenance": "upgrade",
        "documents": {"unit-mapping": json.loads(first_note(hub))["documents"]["unit-mapping"]},
    }


def first_note(hub: Station) -> str:
    _, note = bootstrap_release(hub.resources, hub.db.ids)
    return note


def reopen(hub: Station) -> Station:
    """A process restart: the same database file under a new handle with no release pinned yet."""
    previous = hub.db
    previous.close()
    db = SQLiteDatabase(
        previous.path,
        clock=previous.clock,
        ids=previous.ids,
        release=PinnedRelease("release_unpinned", {}),
        settings=previous.settings,
        origin_node_id=previous.origin_node_id,
        origin_incarnation=previous.origin_incarnation,
        initialize=False,
    )
    return Station(db, hub.sessions, hub.configure, hub.resources)
