"""A real composed hub with the E02 identity and consent modules over a temporary SQLite directory.

SIM profile. Storage, migrations, audit, command ledger, scheduler, clock, configuration release loading,
records, middleware and HTTP are E01's production code; identity and consent are E02's. The SIM roster
and station come from the package's crew and session examples. Units, wire conversion and the
observation quality policy are E01's documented kernel.testing counterparts, carrying no clinical
threshold. Credentials are test secrets generated here, never content.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request

from crew_health.app.assembly import BuildCatalog, ConfigHttpMetadata, RecordHttpServices, create_app
from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.domain.templates import TemplateCheck
from crew_health.consent.adapters.repository import migration_sql as consent_migration
from crew_health.consent.application.ports import AuthoredRecords
from crew_health.consent.application.records import ConsentRecordAuthorization
from crew_health.consent.application.service import AUDIT_DECLARATIONS as CONSENT_AUDIT
from crew_health.consent.application.service import ConsentService
from crew_health.consent.application.settings import defaults as consent_defaults
from crew_health.consent.application.settings import specs as consent_specs
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.module import EventAccess
from crew_health.identity.adapters.repository import migration_sql as identity_migration
from crew_health.identity.api.router import IdentitySecurity
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import HubProfile, IdentityPorts, SessionReader
from crew_health.identity.application.service import IdentityService
from crew_health.identity.application.settings import defaults as identity_defaults
from crew_health.identity.application.settings import specs as identity_specs
from crew_health.identity.application.support import AUDIT_DECLARATIONS as IDENTITY_AUDIT
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, RosterMember
from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryClock, MemoryReleaseCheck, MemoryUnits, MemoryWireConverter
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    LocalProjectionRegistration,
    ReadSnapshot,
    SessionContext,
    UnitOfWork,
)
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
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
RELEASE = "release_explicit_sim"


def _example(path: str) -> dict[str, object]:
    document: object = json.loads((PRODUCT / "api/examples" / path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("value"), dict):
        raise ValueError("example without a value: " + path)
    value: dict[str, object] = document["value"]
    return value


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
    from crew_health.clock.settings import specifications as clock_specs
    from crew_health.config.settings import specifications as config_specs
    from crew_health.events.settings import specifications as event_specs
    from crew_health.platform.settings import specifications as platform_specs
    from crew_health.records.settings import specs as record_specs

    core = (*platform_specs(), *config_specs(), *clock_specs(), *event_specs(), *record_specs())
    values: dict[str, object] = {spec.key: int(spec.maximum) for spec in core if spec.maximum is not None}
    values["clock.regression_tolerance_ms"] = 0
    values.update(identity_defaults())
    values.update(consent_defaults())
    values.update(overrides)
    return ValidatedSettings((*core, *identity_specs(), *consent_specs()), values)


def e02_policies() -> tuple[AuditPolicy, ...]:
    """E02 audit declarations as platform policies; the composer registers them (R-E02-23)."""
    identity = tuple(
        AuditPolicy(
            value.event_type,
            value.metadata,
            frozenset({value.reference_field}),
            {value.reference_field: frozenset({value.reference_type})},
        )
        for value in IDENTITY_AUDIT
    )
    consent = tuple(
        AuditPolicy(
            value.event_type,
            value.metadata,
            frozenset({value.reference_field}),
            {value.reference_field: frozenset({value.reference_type})},
        )
        for value in CONSENT_AUDIT
    )
    return (*identity, *consent)


def install_e02(db: SQLiteDatabase) -> None:
    """Composer duties E01 does not yet take from a module: E02 migrations and audit policies."""
    for name, sql in (("identity.0001", identity_migration()), ("consent.0001", consent_migration())):
        db.migrate_tracked(name, hashlib.sha256(sql.encode()).hexdigest(), sql)
    for policy in e02_policies():
        db.register_audit_policy(policy)


def static_build(directory: Path) -> BuildCatalog:
    target = directory / "packaged/crew_health"
    for module in MODULES:
        path = target / module
        path.mkdir(parents=True, exist_ok=True)
        for name in ("__init__.py", "module.py"):
            shutil.copyfile(SOURCE / module / name, path / name)
    from crew_health.clock.module import register as clock
    from crew_health.config.module import register as config
    from crew_health.consent.module import register as consent
    from crew_health.events.module import register as events
    from crew_health.identity.module import register as identity
    from crew_health.platform.module import register as platform
    from crew_health.records.module import register as records

    registrations = [clock, config, consent, events, identity, platform, records]
    inventory = {
        fn.__module__: hashlib.sha256(
            (target / fn.__module__.split(".")[1] / "module.py").read_bytes()
        ).hexdigest()
        for fn in registrations
    }
    return BuildCatalog(target, inventory, tuple(registrations))


def _candidate() -> CandidateRelease:
    documents = {}
    for name in ConfigName:
        if name.value in {"environment-hazards", "routine-schedule", "responder-flows"}:
            raw = (PRODUCT / (name.value + ".json")).read_bytes()
        else:
            suffix = (
                "api/examples/kit-map.example.json"
                if name == ConfigName.KIT_MAP
                else f"api/examples/config/{name.value}.example.json"
            )
            wrapped = json.loads((PRODUCT / suffix).read_bytes())
            raw = json.dumps(wrapped if name == ConfigName.KIT_MAP else wrapped["value"]).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease(RELEASE, documents)


@cache
def _artifacts() -> ConfigArtifacts:
    """Verified once per test process: the package files are immutable during a run."""
    names = (
        "input-dictionary.json",
        "sources.csv",
        "NASA-SOURCES.md",
        "DEMO-CONDITIONS.md",
        "RESEARCH-ANNEX.md",
        "environment-hazards.json",
        "routine-schedule.json",
        "responder-flows.json",
    )
    bindings = {
        f"api/examples/config/{name.value}.example.json": name
        for name in (ConfigName.SKILLS_CATALOG, ConfigName.ONBOARD_PLANS)
    }
    artifacts = {name: (PRODUCT / name).read_bytes() for name in (*names, *bindings)}
    manifest = json.loads((PRODUCT / "package-manifest.json").read_bytes())
    hashes = {row["path"]: row["sha256"] for row in manifest["files"]}
    return ConfigArtifacts.from_verified(
        schema_directory=SCHEMAS,
        artifacts=artifacts,
        inventory={name: hashes[name] for name in artifacts},
        source_documents=bindings,
    )


def _counterpart_checks(artifacts: ConfigArtifacts) -> LoaderChecks:
    checks = LoaderChecks(maximum=1024)
    for check in (artifacts.schemas, artifacts.sources, artifacts.indicators, TemplateCheck()):
        checks.register(check)
    for identifier in sorted(required_contributors() - {"1", "2", "9", "13"}):
        checks.register(MemoryReleaseCheck(identifier, lambda candidate: ()))
    return checks


def migrate_core(db: SQLiteDatabase, modules: tuple[str, ...]) -> None:
    """E01 core module schemas, migrated as E01's STORAGE phase does."""
    for module in modules:
        db.migrate(
            (SOURCE / module / "adapters/migrations" / ("0001_" + module + ".sql")).read_text(
                encoding="utf-8"
            )
        )


def _install_release(db: SQLiteDatabase, artifacts: ConfigArtifacts) -> None:
    migrate_core(db, ("config", "clock", "events", "records"))
    db.event_sink = EventRepository(
        settings=db.settings, ids=db.ids, validator=EventValidator(schema_directory=SCHEMAS)
    ).append
    service = ReleaseActivationService(
        ConfigRepository(),
        ConfigLoader(
            _counterpart_checks(artifacts), max_document_bytes=db.settings.integer("commands.max_bytes")
        ),
        db.ids,
        origin=lambda view: "SIM",
    )
    editor = SessionContext("session_profile", "editor", OPERATOR, None, frozenset({Role.EDITOR}))
    with db.begin(actor=OPERATOR, correlation_id="sim_profile_install") as uow:
        service.propose(uow, editor, _candidate())
        service.activate(uow, editor, release_id=RELEASE, expected_etag=f'"{RELEASE}:1:0"')
    db.set_release_reader(lambda view: SQLiteConfigReader(view, ConfigRepository()).active())


def _sim_clock(db: SQLiteDatabase) -> PersistentClocks:
    clocks = PersistentClocks(
        repository=SqlClockRepository(
            settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
        ),
        settings=db.settings,
        ids=db.ids,
    )
    with db.begin(actor=OPERATOR, correlation_id="sim_clock_setup") as uow:
        clocks.open(uow, ClockSegment("sim_source", "sim_oscillator", "sim_segment", None, 1, 1))
        clocks.apply(
            uow,
            ClockCorrection(
                "sim_correction", "sim_source", "sim_segment", 1, START, None, 0, 0, uow.now, "operator"
            ),
        )
    return clocks


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


class OwnerVisibility:
    """Command results replay only to the actor that issued the command."""

    def authorize(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None:
        if session.actor.id != identity.actor_ref:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


@dataclass(frozen=True, slots=True)
class LiveSessionAudience:
    """The configuration release projection is visible to any live session."""

    sessions: SessionReader

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == "config.release" and self.sessions.alive(snapshot, session)


@dataclass
class DeferredSecurity:
    """create_app takes its HttpSecurity before modules resolve; this forwards to E02's once bound."""

    target: object = None

    def _bound(self) -> tuple[Callable[[Request], SessionContext], Callable[[SessionContext, str], None]]:
        authenticate = getattr(self.target, "authenticate", None)
        authorize = getattr(self.target, "authorize", None)
        if not callable(authenticate) or not callable(authorize):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)
        return authenticate, authorize

    def authenticate(self, request: Request) -> SessionContext:
        value = self._bound()[0](request)
        if not isinstance(value, SessionContext):
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return value

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        self._bound()[1](session, operation_id)


def _records(db: SQLiteDatabase, clocks: PersistentClocks, resolver: Resolver) -> RecordHttpServices:
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
        commands, RecordsQueries(repository, authorization, db.settings), OwnerVisibility()
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
    security: DeferredSecurity = field(default_factory=DeferredSecurity)

    def configure(self, registry: ModuleRegistry) -> None:
        artifacts, db, clocks = _artifacts(), self.database, self.clocks
        summaries = {
            ConfigName(row["name"]): row["basis_summary"]
            for row in json.loads((PRODUCT / "api/examples/configuration/index.json").read_bytes())["value"][
                "documents"
            ]
        }

        def events(resolver: Resolver) -> EventAccess:
            rule = LiveSessionAudience(resolver.require(IdentityPorts).sessions)
            projection = LocalProjectionRegistration(
                AudienceProjectionKey("config", "release", 1),
                (SOURCE / "config/replica.schema.json").read_bytes(),
                ("/release_id", "/state", "/validation"),
                rule,
            )
            return EventAccess(rule.sessions, (projection,), {"config.release": rule})

        def bind(resolver: Resolver) -> None:
            self.security.target = resolver.require(IdentitySecurity)

        registry.provide(ConfigArtifacts, lambda resolver: artifacts)
        registry.provide(
            ConfigHttpMetadata,
            lambda resolver: ConfigHttpMetadata(summaries, lambda view: "SIM", OwnerVisibility()),
        )
        registry.provide(EventAccess, events, requires=(IdentityPorts,))
        loose: _Provide = registry
        loose.provide(AuthoredRecords, lambda resolver: RecordRepository())
        registry.provide(
            RecordHttpServices,
            lambda resolver: _records(db, clocks, resolver),
            requires=(ConsentRecordAuthorization,),
        )
        if self.profile is not None:
            profile = self.profile
            registry.provide(HubProfile, lambda resolver: profile)
        for identifier in sorted(required_contributors() - {"1", "2", "9", "13"}):
            registry.loader_check(MemoryReleaseCheck(identifier, lambda candidate: ()))
        registry.bind("composer.security", bind)

    def app(self) -> FastAPI:
        return create_app(
            database=self.database,
            build=self.build,
            contract_path=CONTRACT,
            schema_directory=SCHEMAS,
            security=self.security,
            configure=self.configure,
        )

    def restart(self, *, epoch: str, monotonic: int = 0) -> E02Station:
        """A new hub process on the same data directory: new epoch, monotonic counter restarted."""
        with self.database.snapshot() as view:
            pin = view.release
        previous = self.database
        previous.close()
        clock = MemoryClock(self.clock.at, monotonic, epoch)
        reopened = SQLiteDatabase(
            previous.path,
            clock=clock,
            ids=previous.ids,
            release=pin,
            settings=previous.settings,
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
    _install_release(db, _artifacts())
    install_e02(db)
    clocks = _sim_clock(db)
    return E02Station(directory, db, clock, static_build(directory), clocks, profile)


@dataclass(frozen=True, slots=True)
class Running:
    """A serving app with its bound E02 services, as an operator at the console would reach them."""

    app: FastAPI
    identity: IdentityService
    admin: IdentityAdmin
    consent: ConsentService
    clocks: PersistentClocks


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
    )
    if seed:
        seed_roster(running)
    return running


def seed_roster(running: Running | Light, bootstrap: RosterBootstrap | None = None) -> None:
    document = sim_bootstrap() if bootstrap is None else bootstrap
    running.admin.apply_roster(document, expected_revision=0, operator=OPERATOR, correlation_id="sim_roster")
    for principal_id in (
        *(member.person_id for member in document.members),
        *(principal.principal_id for principal in document.principals if principal.kind != "adapter"),
    ):
        running.admin.set_credential(
            principal_id,
            credential(principal_id),
            current_credential=None,
            operator=OPERATOR,
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


def light(
    directory: Path, *, ground: bool = False, seed: bool = True, settings: dict[str, int] | None = None
) -> Light:
    from crew_health.consent.module import build_deps as consent_deps
    from crew_health.identity.application.support import GroundSignIn
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
    migrate_core(db, ("records",))
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
    identity = IdentityService(identity_deps(ports, GroundSignIn(ground)))
    ports.values[IdentityPorts] = consumer_ports(identity)
    services = Light(
        db, clock, identity, IdentityAdmin(identity.deps, identity), ConsentService(consent_deps(ports))
    )
    if seed:
        seed_roster(services)
    return services
