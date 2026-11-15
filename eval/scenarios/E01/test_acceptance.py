"""E01 assembled SIM mechanism evidence with explicitly fake clinical counterparts.

This profile is not a clinical-validation or production release. The real configuration
schema, source, indicator and template checks execute; unbuilt owner checks are named fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

# isort: split
from crew_health.app.assembly import BuildCatalog, ConfigHttpMetadata, RecordHttpServices, create_app
from crew_health.app.cli import TimeConfirmation, confirm_time
from crew_health.app.registry import ModuleRegistry, StartupPhase
from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.clock.settings import specifications as clock_specs
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.domain.package_checks import PACKAGE_CHECK_IDS
from crew_health.config.domain.templates import TemplateCheck
from crew_health.config.settings import specifications as config_specs
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.contracts_gen.schemas.event_page_schema import EventPage
from crew_health.contracts_gen.schemas.observation_schema import Observation
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.events.settings import specifications as event_specs
from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryReleaseCheck, MemoryUnits, MemoryWireConverter
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
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.settings import specifications as platform_specs
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.commands import RecordsCommands
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.application.visibility import OwnerAuthorization
from crew_health.records.domain.observations import ObservationInput, ObservationPolicy, RecordContext
from crew_health.records.settings import specs as record_specs

pytestmark = [
    pytest.mark.E01,
    pytest.mark.trace(
        "T-E01-02",
        "T-E01-03",
        "T-E01-06",
        "T-E01-08",
        "T-E01-09",
        "T-E01-11",
        "T-E01-13",
        "T-E01-20",
        "R01",
        "R06",
        "R25",
        "V75",
    ),
]
ROOT = Path(__file__).resolve().parents[3]


class SimSecurity:
    def __init__(self) -> None:
        self.editor = SessionContext(
            "session_editor", "editor", Actor("operator", "operator_sim"), None, frozenset({Role.EDITOR})
        )
        self.crew = SessionContext(
            "session_crew", "crew", Actor("person", "person_sim"), "person_sim", frozenset({Role.CREW})
        )
        self.other = SessionContext(
            "session_other", "crew", Actor("person", "person_other"), "person_other", frozenset({Role.CREW})
        )
        self.revoked: set[str] = set()

    def authenticate(self, request: Request) -> SessionContext:
        tokens = {
            "Bearer editor-fixture": self.editor,
            "Bearer crew-fixture": self.crew,
            "Bearer other-fixture": self.other,
        }
        session = tokens.get(request.headers.get("Authorization", ""))
        if session is None or session.session_id in self.revoked:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, "explicit SIM authentication fixture")
        return session

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        if (
            operation_id
            in {"createConfigRelease", "activateConfigRelease", "listConfigReleases", "getConfigRelease"}
            and Role.EDITOR not in session.roles
        ):
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "editor fixture role required")
        if operation_id in {"recordObservation", "getObservation"} and Role.CREW not in session.roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "crew fixture role required")

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        return session.session_id not in self.revoked

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == "config.release" and self.alive(snapshot, session)

    def authorize_result(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None:
        self.authorize(session, identity.operation)
        if session.actor.id != identity.actor_ref:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "result owner mismatch")


@dataclass(frozen=True, slots=True)
class FixtureVisibility:
    security: SimSecurity

    def authorize(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None:
        self.security.authorize_result(snapshot, session, identity, result)


def fixture_candidate() -> CandidateRelease:
    documents = {}
    for name in ConfigName:
        if name == ConfigName.UNIT_MAPPING:
            # D12: the accepted unit mapping lives only in config/, with no package example.
            raw = (ROOT / "config/unit-mapping.json").read_bytes()
        elif name.value in {"environment-hazards", "routine-schedule", "responder-flows"}:
            raw = (ROOT / "docs/product" / (name.value + ".json")).read_bytes()
        else:
            suffix = (
                "api/examples/kit-map.example.json"
                if name == ConfigName.KIT_MAP
                else f"api/examples/config/{name.value}.example.json"
            )
            wrapped = json.loads((ROOT / "docs/product" / suffix).read_bytes())
            raw = json.dumps(wrapped if name == ConfigName.KIT_MAP else wrapped["value"]).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease("release_explicit_sim", documents)


def fixture_artifacts() -> ConfigArtifacts:
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
    artifacts = {name: (ROOT / "docs/product" / name).read_bytes() for name in (*names, *bindings)}
    manifest = json.loads((ROOT / "docs/product/package-manifest.json").read_bytes())
    hashes = {row["path"]: row["sha256"] for row in manifest["files"]}
    return ConfigArtifacts.from_verified(
        schema_directory=ROOT / "contracts/api/schemas",
        artifacts=artifacts,
        inventory={name: hashes[name] for name in artifacts},
        source_documents=bindings,
    )


def fixture_settings() -> ValidatedSettings:

    specs = (
        *platform_specs(),
        *config_specs(),
        *clock_specs(),
        *event_specs(),
        *record_specs(),
    )
    return ValidatedSettings(
        specs, {spec.key: int(spec.maximum) for spec in specs if spec.maximum is not None}
    )


def static_core(tmp_path: Path) -> BuildCatalog:
    target = tmp_path / "packaged/crew_health"
    for module in ("clock", "config", "events", "platform", "records"):
        path = target / module
        path.mkdir(parents=True)
        for name in ("__init__.py", "module.py"):
            shutil.copyfile(ROOT / "hub/src/crew_health" / module / name, path / name)
        shutil.copytree(
            ROOT / "hub/src/crew_health" / module / "adapters/migrations", path / "adapters/migrations"
        )
    return BuildCatalog.core(target)


def counterpart_checks(artifacts: ConfigArtifacts, *, missing: str | None = None) -> LoaderChecks:
    checks = LoaderChecks(maximum=1024)
    for check in (artifacts.schemas, artifacts.sources, artifacts.indicators, TemplateCheck()):
        checks.register(check)
    for identifier in sorted(required_contributors() - {"1", "2", "9", "13"}):
        if identifier != missing:
            checks.register(MemoryReleaseCheck(identifier, lambda candidate: ()))
    return checks


def fixture_clocks(db: SQLiteDatabase) -> PersistentClocks:
    clocks = PersistentClocks(
        repository=SqlClockRepository(
            settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
        ),
        settings=db.settings,
        ids=db.ids,
    )
    with db.begin(actor=Actor("operator", "operator_sim"), correlation_id="sim_clock_setup") as uow:
        clocks.open(uow, ClockSegment("sim_source", "sim_oscillator", "sim_segment", None, 1, 1))
        clocks.apply(
            uow,
            ClockCorrection(
                "sim_correction",
                "sim_source",
                "sim_segment",
                1,
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
                0,
                0,
                uow.now,
                "operator",
            ),
        )
    return clocks


class SimBinding:
    def bind(self, uow: UnitOfWork, session: SessionContext, value: ObservationInput) -> RecordContext:
        if session.person_id is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "SIM capture owner required")
        return RecordContext(
            value.patient_id,
            session.person_id,
            "mission_sim",
            "SIM",
            TimeInput(value.observed_at, "sim_source", "sim_segment", 1, "source_raw", "SIM"),
            "self_capture",
        )


class SyntheticQualityPolicy:
    def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy:
        # Unitless parser/quality golden. This is no clinical threshold or LIVE policy.
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


def record_services(
    db: SQLiteDatabase, clocks: PersistentClocks, security: SimSecurity
) -> RecordHttpServices:
    repository, authorization = RecordRepository(), OwnerAuthorization()
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
        commands, RecordsQueries(repository, authorization, db.settings), FixtureVisibility(security)
    )


@dataclass(frozen=True, slots=True)
class SimStation:
    database: SQLiteDatabase
    build: BuildCatalog
    security: SimSecurity
    configure: Callable[[ModuleRegistry], None]

    def reopen(self) -> SimStation:
        with self.database.snapshot() as view:
            pin = view.release
        previous = self.database
        previous.close()
        reopened = SQLiteDatabase(
            previous.path,
            clock=previous.clock,
            ids=previous.ids,
            release=pin,
            settings=previous.settings,
            origin_node_id=previous.origin_node_id,
            origin_incarnation=previous.origin_incarnation,
            initialize=False,
        )
        repository = ConfigRepository()
        reopened.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
        return SimStation(reopened, self.build, self.security, self.configure)

    def app(self) -> FastAPI:
        return create_app(
            database=self.database,
            build=self.build,
            contract_path=ROOT / "contracts/api/openapi.yaml",
            schema_directory=ROOT / "contracts/api/schemas",
            security=self.security,
            configure=self.configure,
        )


def explicit_sim_station(tmp_path: Path, *, missing: str | None = None) -> SimStation:
    db = database(tmp_path / "state")
    db.settings = fixture_settings()
    for module in ("config", "clock", "events", "records"):
        db.migrate(
            (
                ROOT / "hub/src/crew_health" / module / "adapters/migrations" / ("0001_" + module + ".sql")
            ).read_text()
        )
    artifacts, security, repository = fixture_artifacts(), SimSecurity(), ConfigRepository()
    event_repository = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"),
    )
    db.event_sink = event_repository.append
    service = ReleaseActivationService(
        repository,
        ConfigLoader(
            counterpart_checks(artifacts), max_document_bytes=db.settings.integer("commands.max_bytes")
        ),
        db.ids,
        origin=lambda view: "SIM",
    )
    with db.begin(actor=security.editor.actor, correlation_id="explicit_sim_profile_install") as uow:
        service.propose(uow, security.editor, fixture_candidate())
        service.activate(
            uow,
            security.editor,
            release_id="release_explicit_sim",
            expected_etag='"release_explicit_sim:1:0"',
        )
    db.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
    clocks = fixture_clocks(db)
    record_http = record_services(db, clocks, security)
    descriptions = json.loads((ROOT / "docs/product/api/examples/configuration/index.json").read_bytes())[
        "value"
    ]["documents"]
    summaries = {ConfigName(row["name"]): row["basis_summary"] for row in descriptions}
    # The package index predates D12; the 19th document's summary is this fixture's own text.
    summaries[ConfigName.UNIT_MAPPING] = "D12 accepted unit mapping, config/unit-mapping.json"
    projection = LocalProjectionRegistration(
        AudienceProjectionKey("config", "release", 1),
        (ROOT / "hub/src/crew_health/config/replica.schema.json").read_bytes(),
        ("/release_id", "/state", "/validation"),
        security,
    )

    # Config's package checks bind from the artifacts; the one left out stays unbound and fails closed.
    stand_ins = tuple(
        MemoryReleaseCheck(identifier, lambda candidate: ())
        for identifier in PACKAGE_CHECK_IDS
        if identifier != missing
    )
    station_artifacts = replace(artifacts, package=stand_ins)

    def configure(registry: ModuleRegistry) -> None:
        registry.provide(ConfigArtifacts, lambda resolver: station_artifacts)
        registry.provide(
            ConfigHttpMetadata,
            lambda resolver: ConfigHttpMetadata(summaries, lambda view: "SIM", FixtureVisibility(security)),
        )
        registry.provide(SessionAuthorizer, lambda resolver: security)
        registry.local_projection(projection)
        registry.provide(RecordHttpServices, lambda resolver: record_http)

    return SimStation(db, static_core(tmp_path), security, configure)


def health_status(app: FastAPI) -> str:
    async def read() -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await client.get("/v1/health")
            assert response.status_code == 200
            return str(response.json()["status"])

    return asyncio.run(read())


def correct_hub_clock(app: FastAPI) -> None:
    """The crew's time confirmation through the production command; the supervisor's sweep then allows
    clearing (SYSTEM 5.2: a fresh hub starts frozen)."""
    confirm_time(app.state.runtime, TimeConfirmation(0, 0, "operator_fixture"))


def test_full_startup_configuration_http_event_and_restart(tmp_path: Path) -> None:
    station = explicit_sim_station(tmp_path)
    app = station.app()
    assert app.state.runtime.serving, app.state.runtime.fault_code
    assert app.state.runtime.completed_phases == list(StartupPhase)
    assert health_status(app) == "degraded"
    correct_hub_clock(app)

    async def run() -> str:
        editor = {"Authorization": "Bearer editor-fixture"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            health = await client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["assistant_state"] == "unavailable"
            assert health.json()["adapters"] == []
            assert (await client.post("/v1/observations", json={})).status_code == 401
            document = await client.get("/v1/config/ui-copy", headers=editor)
            assert document.status_code == 200
            changed = document.json()["document"]
            changed["designed_states"]["unknown"] += " (explicit SIM edit)"
            proposed = await client.post(
                "/v1/config/releases",
                headers={
                    **editor,
                    "Idempotency-Key": "sim_proposal_1",
                    "Config-Release": "release_explicit_sim",
                },
                json={"documents": [{"name": "ui-copy", "document": changed}]},
            )
            assert proposed.status_code == 201, proposed.text
            identifier = proposed.json()["release_id"]
            with station.database.snapshot() as old:
                activated = await client.post(
                    "/v1/config/releases/" + identifier + "/activate",
                    headers={
                        **editor,
                        "Idempotency-Key": "sim_activate_1",
                        "If-Match": proposed.headers["ETag"],
                    },
                )
                assert activated.status_code == 200, activated.text
                assert old.release.release_id == "release_explicit_sim"
            refreshed = await client.get("/v1/config/ui-copy", headers=editor)
            assert refreshed.json()["document"]["designed_states"]["unknown"].endswith("(explicit SIM edit)")
            events = await client.get("/v1/events", headers={**editor, "Accept": "application/json"})
            assert events.status_code == 200, events.text
            page = EventPage.model_validate(events.json())
            assert any(
                event.type == "config.release_activated" and event.config_release_id.root == identifier
                for event in page.events
            )
            return str(identifier)

    active_id = asyncio.run(run())
    reopened_station = station.reopen()
    assert reopened_station.database is not station.database
    restarted = reopened_station.app()
    assert restarted.state.runtime.serving, restarted.state.runtime.fault_code
    assert restarted.state.runtime.completed_phases == list(StartupPhase)
    # The hub correction survives the restart: a new process opens a new segment but stays allowed to clear.
    assert health_status(restarted) == "ok"
    with reopened_station.database.snapshot() as view:
        assert view.release.release_id == active_id
        assert b"explicit SIM edit" in view.release.document(ConfigName.UI_COPY).raw


def test_missing_counterpart_check_prevents_sim_serving(tmp_path: Path) -> None:
    station = explicit_sim_station(tmp_path, missing="14")
    app = station.app()
    assert not app.state.runtime.serving
    assert app.state.runtime.fault_code is not None


def test_manual_sim_capture_without_optional_parts_and_owner_privacy(tmp_path: Path) -> None:
    station = explicit_sim_station(tmp_path)
    app = station.app()
    assert app.state.runtime.serving, app.state.runtime.fault_code

    async def run() -> None:
        headers = {
            "Authorization": "Bearer crew-fixture",
            "Idempotency-Key": "sim_observation_1",
            "Config-Release": "release_explicit_sim",
        }
        body = {
            "patient_id": "person_sim",
            "code": "indicator",
            "value": 50,
            "unit": "1",
            "observed_at": station.database.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "origin_mode": "SIM",
            "acquisition_method": "self_report",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            captured = await client.post("/v1/observations", headers=headers, json=body)
            assert captured.status_code == 201, captured.text
            observed = Observation.model_validate(captured.json())
            assert observed.origin_mode.root == "SIM"
            assert observed.value == 50
            identifier = captured.json()["observation_id"]
            assert (
                await client.get(
                    "/v1/observations/" + identifier, headers={"Authorization": "Bearer crew-fixture"}
                )
            ).status_code == 200
            denied = await client.get(
                "/v1/observations/" + identifier, headers={"Authorization": "Bearer other-fixture"}
            )
            assert denied.status_code == 403
            assert denied.json()["code"] == "UNAUTHORIZED_SCOPE"
            retry = await client.post("/v1/observations", headers=headers, json=body)
            assert retry.status_code == 409, retry.text
            receipt = retry.json()
            assert receipt["code"] == "DUPLICATE_COMMAND"
            assert receipt["retryable"] is False
            assert set(receipt["current_state"]) == {
                "kind",
                "command_key",
                "operation_id",
                "target",
                "receipt_id",
                "accepted_at",
            }
            assert receipt["current_state"]["kind"] == "accepted_command"
            assert receipt["current_state"]["command_key"] == "sim_observation_1"
            assert receipt["current_state"]["operation_id"] == "recordObservation"
            assert receipt["current_state"]["target"] == "/v1/observations"
        with station.database.snapshot() as view:
            assert view.connection.rows("SELECT count(*) FROM records_observation", (), limit=1) == ((1,),)

    asyncio.run(run())
