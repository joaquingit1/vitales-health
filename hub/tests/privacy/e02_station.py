"""A real composed hub with the E02 identity and consent modules over a temporary SQLite directory.

SIM profile. Storage, migrations, audit, command ledger, scheduler, clock, configuration release loading,
records, middleware and HTTP are E01's production code; identity and consent are E02's. The SIM roster
and station come from the package's crew and session examples. Units, wire conversion and the
observation quality policy are E01's documented kernel.testing counterparts, carrying no clinical
threshold. Credentials are test secrets generated here, never content.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache, partial
from pathlib import Path
from typing import Protocol

import uvicorn
from fastapi import FastAPI

from crew_health.app.assembly import BuildCatalog, RecordHttpServices, create_app
from crew_health.app.import_contracts import module_migrations
from crew_health.app.maintenance import wire_clock
from crew_health.app.privacy import ClockAdmissionAdapter, ReplayVisibility
from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.app.station import HubResources, bootstrap_release, config_artifacts, production
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.clock.application.history import MissionClockService
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.consent.adapters.repository import migration_sql as consent_migration
from crew_health.consent.application.records import ConsentRecordAuthorization
from crew_health.consent.application.service import ConsentBindings, ConsentService
from crew_health.consent.application.settings import defaults as consent_defaults
from crew_health.consent.module import audit_policies as consent_policies
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.application.delivery import EventDelivery
from crew_health.identity.adapters.repository import migration_sql as identity_migration
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import HubProfile, IdentityPorts, OperatorProof
from crew_health.identity.application.service import IdentityService
from crew_health.identity.application.settings import defaults as identity_defaults
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, RosterMember
from crew_health.identity.module import audit_policies as identity_policies
from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
from crew_health.kernel.config import CandidateRelease, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.testing import MemoryClock, MemoryUnits, MemoryWireConverter
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit
from crew_health.kernel.uow import (
    Actor,
    ReadSnapshot,
    SessionContext,
    UnitOfWork,
)
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase, SystemIds
from crew_health.platform.testing.sqlite import database
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.commands import RecordsCommands
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.domain.observations import ObservationInput, ObservationPolicy, RecordContext

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "hub/src/crew_health"
PRODUCT = ROOT / "docs/product"
CONTRACT = ROOT / "contracts/api/openapi.yaml"
SCHEMAS = ROOT / "contracts/api/schemas"
MODULES = ("clock", "config", "consent", "events", "identity", "platform", "records")
START = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
OPERATOR = Actor("operator", "operator_sim")
CONSOLE = OperatorProof(OPERATOR, None)
"""The console before any operator credential exists: physical access is the proof (E02 4)."""
MISSION_EPOCH = START - timedelta(days=1)
"""Mission day 1 began the day before START, so START is day 2 and every SIM member (from day 1) is current."""


def _example(path: str) -> dict[str, object]:
    document: object = json.loads((PRODUCT / "api/examples" / path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("value"), dict):
        raise ValueError("example without a value: " + path)
    value: dict[str, object] = document["value"]
    return value


def release_id() -> str:
    """The ID of the release every station here serves: the checkout's first-start release."""
    return _candidate()[0].release_id


def mission_id() -> str:
    value = _example("session/create-crew.json")["mission_id"]
    if not isinstance(value, str):
        raise ValueError("session example mission")
    return value


def sim_members() -> tuple[RosterMember, ...]:
    """The SIM crew of the package's listCrew example, in its order, with its officer ranks."""
    items = _example("crew/list.json")["items"]
    if not isinstance(items, list):
        raise ValueError("crew example items")
    members: list[RosterMember] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("crew example item")
        rank = item["medical_officer_rank"]
        members.append(
            RosterMember(
                str(item["person_id"]),
                str(item["display_name"]),
                index,
                int(str(item["on_roster_from_day"])),
                None,
                None if rank is None else int(str(rank)),
            )
        )
    return tuple(members)


def sim_station_principal() -> ProvisionedPrincipal:
    actor = _example("session/station-screen.json")["actor"]
    if not isinstance(actor, dict):
        raise ValueError("station example actor")
    return ProvisionedPrincipal(str(actor["id"]), "station", str(actor["display_name"]))


def sim_bootstrap(
    members: tuple[RosterMember, ...] | None = None,
    principals: tuple[ProvisionedPrincipal, ...] | None = None,
) -> RosterBootstrap:
    return RosterBootstrap(
        mission_id(),
        "SIM",
        sim_members() if members is None else members,
        (sim_station_principal(),) if principals is None else principals,
    )


def credential(principal_id: str) -> str:
    return "sim passphrase for " + principal_id


def station_settings(**overrides: int) -> ValidatedSettings:
    """Every setting the installed modules declare (as `crew-health serve` reads them): integers at their registered
    maximum, switches off and text empty, then the E02 defaults, a zero clock regression tolerance and `overrides`."""
    from crew_health.app.cli import registered_settings

    specs = registered_settings()
    values: dict[str, object] = {
        spec.key: int(spec.maximum or 0)
        if spec.kind == "integer"
        else False
        if spec.kind == "boolean"
        else ""
        for spec in specs
    }
    values["clock.regression_tolerance_ms"] = 0
    values.update(identity_defaults())
    values.update(consent_defaults())
    values.update(overrides)
    return ValidatedSettings(specs, values)


def e02_policies() -> tuple[AuditPolicy, ...]:
    """E02 audit policies exactly as the modules register them (registry.audit_policy)."""
    return (*identity_policies(), *consent_policies())


def mission_clock(db: SQLiteDatabase) -> MissionClockService:
    """E01's mission clock with one version whose day 1 began the day before START (operator authority)."""
    repository = SqlClockRepository(
        settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
    )
    service = MissionClockService(snapshots=db, repository=repository)
    with db.snapshot() as view:
        known = view.connection.rows("SELECT clock_version FROM clock_mission_version", (), limit=1)
    if not known:
        with db.begin(actor=OPERATOR, correlation_id="sim_mission_clock") as uow:
            service.append(
                uow,
                MissionClockVersion("mission_clock_sim", 1, MISSION_EPOCH, MISSION_EPOCH, None, "Day {day}"),
                ids=db.ids,
            )
    return service


def install_e02(db: SQLiteDatabase) -> None:
    """For hubs built without E01's STORAGE phase (light): E02 migrations and audit policies."""
    for name, sql in (("identity.0001", identity_migration()), ("consent.0001", consent_migration())):
        db.migrate_tracked(name, hashlib.sha256(sql.encode()).hexdigest(), sql)
    for policy in e02_policies():
        db.register_audit_policy(policy)


@cache
def resources() -> HubResources:
    """The station resources E01's app tests start from (tests/app/conftest.py): the repository's own first-start
    release is refused on main (test_serve.py pins it), so config/ pairs the package's kit-map and plans examples."""
    spec = importlib.util.spec_from_file_location("e01_station_resources", ROOT / "hub/tests/app/conftest.py")
    if spec is None or spec.loader is None:
        raise ImportError("tests/app/conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = Path(tempfile.mkdtemp(prefix="e02-resources-")) / "resources"
    return HubResources(module.copy_station_resources(target))


@cache
def _production() -> Callable[[ModuleRegistry], None]:
    """`production`'s configure hook for these resources, built once per test process: building it verifies the
    offline package artifacts, which do not change between stations."""
    return production(resources(), origin="SIM")


@cache
def _candidate() -> tuple[CandidateRelease, str]:
    """The production first-start release of this checkout (app/station.py), with its provenance note."""
    return bootstrap_release(resources(), SystemIds())


@cache
def _artifacts() -> ConfigArtifacts:
    """The production artifacts of this checkout (app/station.py), verified once per test process."""
    return config_artifacts(resources())


def migrate_core(db: SQLiteDatabase, modules: tuple[str, ...]) -> None:
    """E01 module schemas, every tracked migration of each, applied as E01's STORAGE phase applies them."""
    for module in modules:
        for path in module_migrations(SOURCE / module):
            raw = path.read_bytes()
            db.migrate_tracked(
                module + "." + path.name[:4], hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")
            )


def _clocks(db: SQLiteDatabase) -> PersistentClocks:
    """E01's clock service as the clock module builds it, with the hub's own clock identity (app/assembly.py)."""
    return PersistentClocks(
        repository=SqlClockRepository(
            settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
        ),
        hub_source_clock_id=db.ids.derived("hubclock", db.origin_node_id),
        settings=db.settings,
        ids=db.ids,
    )


def _sim_clock(db: SQLiteDatabase, clocks: PersistentClocks) -> None:
    with db.begin(actor=OPERATOR, correlation_id="sim_clock_setup") as uow:
        clocks.open(uow, ClockSegment("sim_source", "sim_oscillator", "sim_segment", None, 1, 1))
        clocks.apply(
            uow,
            ClockCorrection(
                "sim_correction", "sim_source", "sim_segment", 1, START, None, 0, 0, uow.now, "operator"
            ),
        )


class SimBinding:
    """The subject and operator of a capture: the named patient, captured by the signed-in person."""

    def bind(self, uow: UnitOfWork, session: SessionContext, value: ObservationInput) -> RecordContext:
        if session.person_id is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        return RecordContext(
            value.patient_id,
            session.person_id,
            mission_id(),
            "SIM",
            TimeInput(value.observed_at, "sim_source", "sim_segment", 1, "source_raw", "SIM"),
            "self_capture" if value.patient_id == session.person_id else "officer_capture",
        )


class SyntheticQualityPolicy:
    def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy:
        # Unitless parser and quality mechanism only (E01 SIM profile); no clinical threshold.
        return ObservationPolicy(
            "indicator",
            "sim_quality",
            "synthetic_mechanism_profile",
            Unit("1"),
            frozenset({"1"}),
            frozenset({"self_report"}),
            Decimal(0),
            Decimal(100),
            Decimal(0),
            Decimal(100),
            3,
            Decimal(10),
            True,
        )


def _records(db: SQLiteDatabase, resolver: Resolver) -> RecordHttpServices:
    clocks = resolver.require(PersistentClocks)
    repository = RecordRepository()
    authorization = resolver.require(ConsentRecordAuthorization)
    commands = RecordsCommands(
        repository=repository,
        ids=db.ids,
        units=MemoryUnits({"1": ("scalar", Decimal(1), Decimal(0))}),
        converter=MemoryWireConverter(
            lambda value, code, release, target, pressure: ConvertedQuantity(
                value, Quantity(value.value, target), "identity", "sim_mapping", None, "exact"
            )
        ),
        authorization=authorization,
        binding=SimBinding(),
        policies=SyntheticQualityPolicy(),
        clocks=clocks,
    )
    return RecordHttpServices(
        commands,
        RecordsQueries(repository, authorization, db.settings),
        ReplayVisibility(resolver.require(IdentityService)),
    )


class _Provide(Protocol):
    def provide(
        self,
        port: type[object],
        factory: Callable[[Resolver], object],
        *,
        requires: tuple[type[object], ...] = (),
    ) -> None: ...


@dataclass
class E02Station:
    directory: Path
    database: SQLiteDatabase
    clock: MemoryClock
    build: BuildCatalog
    clocks: PersistentClocks
    profile: HubProfile | None = None
    served: FastAPI | None = field(default=None, repr=False)

    def configure(self, registry: ModuleRegistry) -> None:
        db = self.database
        # The served hub's own configure (app/station.py `production`): the release artifacts, the E02 composition
        # (app/privacy.py) under every consumer's real port class, and fail-closed stand-ins for the rest.
        _production()(registry)
        # E01's record HTTP services have no production provider yet (docs/requests/E02.md); the harness supplies
        # them over the real consent record authorization and replay visibility.
        registry.provide(
            RecordHttpServices,
            lambda resolver: _records(db, resolver),
            requires=(ConsentRecordAuthorization, IdentityService, PersistentClocks),
        )
        if self.profile is not None:
            profile = self.profile
            registry.provide(HubProfile, lambda resolver: profile)

    def app(self) -> FastAPI:
        """The hub's one app: a database serves a single process (its bus refuses a second subscriber set), so the
        first start builds it and every later call returns the same app. `restart` opens a new process."""
        if self.served is None:
            self.served = self._start()
        return self.served

    def _start(self) -> FastAPI:
        return create_app(
            database=self.database,
            build=self.build,
            contract_path=CONTRACT,
            schema_directory=SCHEMAS,
            configure=self.configure,
            bootstrap=partial(bootstrap_release, resources()),
        )

    def restart(
        self, *, epoch: str, monotonic: int = 0, settings: dict[str, int] | None = None
    ) -> E02Station:
        """A new hub process on the same data directory: new epoch, monotonic counter restarted.

        `settings` starts it with changed operational settings, as an operator's edit takes effect.
        """
        with self.database.snapshot() as view:
            pin = view.release
        previous = self.database
        if self.served is not None:
            self.served.state.runtime.shutdown()  # the old process stops its workers before its database closes
        previous.close()
        clock = MemoryClock(self.clock.at, monotonic, epoch)
        reopened = SQLiteDatabase(
            previous.path,
            clock=clock,
            ids=previous.ids,
            release=pin,
            settings=previous.settings if settings is None else station_settings(**settings),
            origin_node_id=previous.origin_node_id,
            origin_incarnation=previous.origin_incarnation,
            initialize=False,
        )
        reopened.set_release_reader(lambda view: SQLiteConfigReader(view, ConfigRepository()).active())
        install_e02(reopened)
        return E02Station(self.directory, reopened, clock, self.build, self.clocks, self.profile)


def station(
    directory: Path, *, profile: HubProfile | None = None, settings: dict[str, int] | None = None
) -> E02Station:
    clock = MemoryClock(START, 1_000_000_000, "epoch_first")
    db = database(directory / "state", clock=clock)
    db.settings = station_settings(**(settings or {}))
    clocks = _clocks(db)
    hub = E02Station(directory, db, clock, BuildCatalog.installed(SOURCE), clocks, profile)
    # First start exactly as `crew-health serve` does it: STORAGE migrates every installed module, CONFIG installs
    # the first-start release through the real loader checks, and the operator's time confirmation lifts the first
    # start clock freeze (app/cli.py `--confirm-time-*`).
    runtime = hub.app().state.runtime
    if not runtime.serving or runtime.resolver is None or runtime.supervisor is None:
        raise AssertionError("first start did not serve: " + str(runtime.fault_code))
    hub.clocks = runtime.resolver.require(PersistentClocks)
    _sim_clock(db, hub.clocks)
    with db.begin(actor=OPERATOR, correlation_id="sim_time_confirmed") as uow:
        hub.clocks.confirm_hub_time(
            uow, correction_id=db.ids.new("correction"), offset_ms=0, uncertainty_ms=0
        )
    runtime.supervisor.run_once()
    with db.snapshot() as view:
        state = hub.clocks.repository.state(view.connection)
    if state is None or state.frozen:
        raise AssertionError("the time confirmation did not lift the first start freeze")
    mission_clock(db)
    return hub


@dataclass(frozen=True, slots=True)
class Running:
    """A serving app with its bound E02 services, as an operator at the console would reach them."""

    app: FastAPI
    identity: IdentityService
    admin: IdentityAdmin
    consent: ConsentService
    clocks: PersistentClocks
    events: EventDelivery


def serve(hub: E02Station, *, seed: bool = True) -> Running:
    app = hub.app()
    runtime = app.state.runtime
    if not runtime.serving or runtime.resolver is None:
        raise AssertionError("hub did not serve: " + str(runtime.fault_code))
    resolver: Resolver = runtime.resolver
    running = Running(
        app,
        resolver.require(IdentityService),
        resolver.require(IdentityAdmin),
        resolver.require(ConsentService),
        resolver.require(PersistentClocks),
        resolver.require(EventDelivery),
    )
    if seed:
        seed_roster(running)
    return running


def seed_roster(running: Running | Light, bootstrap: RosterBootstrap | None = None) -> None:
    document = sim_bootstrap() if bootstrap is None else bootstrap
    operators = [p.principal_id for p in document.principals if p.kind == "operator"]
    # The first roster names the console operator (E02 4); its own credential is provisioned last, because
    # afterwards every console command needs that credential proven.
    console = OperatorProof(Actor("operator", operators[0]), None) if operators else CONSOLE
    running.admin.apply_roster(document, expected_revision=0, operator=console, correlation_id="sim_roster")
    for principal_id in (
        *(member.person_id for member in document.members),
        *(p.principal_id for p in document.principals if p.kind not in ("adapter", "operator")),
        *operators,
    ):
        running.admin.set_credential(
            principal_id,
            credential(principal_id),
            current_credential=None,
            operator=console,
            client="console",
            correlation_id="sim_credential",
        )


class Ports:
    """The kernel ports E01's registry hands a module, keyed by port class, for a hub without HTTP."""

    def __init__(self, values: dict[type[object], object]) -> None:
        self.values = values

    def require(self, port: type[object]) -> object:
        return self.values[port]


@dataclass(frozen=True, slots=True)
class Light:
    """Identity and consent built by their own module wiring over a real SQLite station, without HTTP.

    For service tests that need no startup phase, clock bus or router; those use `station` and `serve`.
    """

    database: SQLiteDatabase
    clock: MemoryClock
    identity: IdentityService
    admin: IdentityAdmin
    consent: ConsentService
    observations: KnownObservations
    clocks: PersistentClocks


@dataclass(frozen=True, slots=True)
class ObservationFact:
    patient_id: str
    mission_id: str
    origin_mode: str


class KnownObservations:
    """Consent's ObservationPartitions for a hub without the records assembly: the subject, mission and origin of
    each observation a test stored. The served station binds E01's records store instead (app/privacy.py)."""

    def __init__(self) -> None:
        self.facts: dict[str, ObservationFact] = {}

    def add(
        self, observation_id: str, patient_id: str, *, mission: str | None = None, origin: str = "SIM"
    ) -> None:
        self.facts[observation_id] = ObservationFact(patient_id, mission or mission_id(), origin)

    def observation(self, view: ReadSnapshot | UnitOfWork, observation_id: str) -> ObservationFact | None:
        return self.facts.get(observation_id)


def light(
    directory: Path, *, ground: bool = False, seed: bool = True, settings: dict[str, int] | None = None
) -> Light:
    from crew_health.consent.module import build_deps as consent_deps
    from crew_health.identity.application.support import GroundSignIn, MissionDays
    from crew_health.identity.module import build_deps as identity_deps
    from crew_health.identity.module import consumer_ports
    from crew_health.kernel.clock import Clock
    from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
    from crew_health.kernel.ids import IdFactory
    from crew_health.kernel.settings import HubSettings
    from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
    from crew_health.platform.adapters.ledger import CommandLedger

    clock = MemoryClock(START, 1_000_000_000, "epoch_first")
    db = database(directory / "state", clock=clock)
    db.settings = station_settings(**(settings or {}))
    migrate_core(db, ("platform", "records", "clock"))
    install_e02(db)
    ledger = CommandLedger(db.ids, settings=db.settings)
    ports = Ports(
        {
            UnitOfWorkFactory: db,
            ReadSnapshotFactory: db,
            Clock: db.clock,
            IdFactory: db.ids,
            HubSettings: db.settings,
            IdempotencyStore: ledger,
            CommandReceipts: ledger,
        }
    )
    # E01's clock as a served hub runs it: the regression guard on every write, the hub segment, and the
    # operator's time confirmation swept so clearing is not frozen.
    clocks = _clocks(db)
    wire_clock(db, clocks)
    with db.begin(actor=OPERATOR, correlation_id="sim_time_confirmed") as uow:
        clocks.confirm_hub_time(uow, correction_id=db.ids.new("correction"), offset_ms=0, uncertainty_ms=0)
    with db.begin(actor=Actor("engine", "engine_clock"), correlation_id="sim_clock_sweep") as uow:
        version = clocks.pending_sweep(uow)
        if version is None:
            raise AssertionError("no clock sweep pending after the time confirmation")
        clocks.complete_sweep(uow, correction_version=version)
    days = MissionDays(mission_clock(db))
    identity = IdentityService(identity_deps(ports, GroundSignIn(ground), days))
    ports.values[IdentityPorts] = consumer_ports(identity)
    observations = KnownObservations()
    observations.add("obs_1", "mara")
    bound = ConsentBindings(time=ClockAdmissionAdapter(clocks), observations=observations)
    services = Light(
        db,
        clock,
        identity,
        IdentityAdmin(identity.deps, identity),
        ConsentService(consent_deps(ports, bound)),
        observations,
        clocks,
    )
    if seed:
        seed_roster(services)
    return services


def main(argv: Sequence[str] | None = None) -> None:
    """Serves a seeded SIM station on 127.0.0.1 until stopped: the crew app's browser tests run against it."""
    parser = argparse.ArgumentParser(prog="e02_station")
    parser.add_argument("--port", type=int, required=True)
    port: int = parser.parse_args(argv).port
    with tempfile.TemporaryDirectory(prefix="e02-station-", ignore_cleanup_errors=True) as directory:
        hub = station(Path(directory))
        try:
            uvicorn.run(serve(hub).app, host="127.0.0.1", port=port, log_level="warning")
        finally:
            hub.database.close()


if __name__ == "__main__":
    main()
