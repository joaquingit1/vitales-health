"""A real E23 station for integration tests: create_app over a temporary on-disk SQLite, E01's real mechanisms
(unit of work, ledger, scheduler, clock, event outbox, config loader), the real mission and sensors modules and
real HTTP through the hub's middleware. Only what E01 has no production provider for is a named fake here: the
authenticated session source, the unit registry and wire converter (E01 D12), the evaluation isolation wiring and
the roster (E02). Configuration is the shipped config/ documents, never typed clinical content."""

from __future__ import annotations

import hashlib
import json
import runpy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.testclient import TestClient

from crew_health.app.assembly import ConfigHttpMetadata, create_app
from crew_health.app.registry import ModuleRegistry
from crew_health.clock.application.clock import PersistentClocks
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, required_contributors
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.module import EventAccess
from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationIsolation
from crew_health.kernel.testing import MemoryClock, MemoryReleaseCheck, MemoryUnits, MemoryWireConverter
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit, UnitRegistry, WireQuantity, WireUnitConverter
from crew_health.kernel.uow import Actor, Audience, ReadSnapshot, SessionContext
from crew_health.mission import module as mission_module
from crew_health.mission.application.ports import MissionInitializer, RosterPort
from crew_health.mission.settings import DEFAULTS as MISSION_DEFAULTS
from crew_health.mission.settings import specifications as mission_specs
from crew_health.platform.adapters.evaluation import EvaluationRepository
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.sensors import module as sensors_module
from crew_health.sensors.application.ports import FreshnessSweeper
from crew_health.sensors.settings import DEFAULTS as SENSOR_DEFAULTS
from crew_health.sensors.settings import specifications as sensor_specs

ROOT = Path(__file__).resolve().parents[3]
E01 = runpy.run_path(str(ROOT / "eval/scenarios/E01/test_acceptance.py"))
START = datetime(2026, 11, 14, 9, 0, 0, tzinfo=UTC)
HUB_CLOCK, ADAPTER = "hub_clock", "adp_sim"
MINE = {"4", "7", "12.sensor", "12.effect"}
ROSTER = frozenset({"per_a", "per_b"})


class Security:
    """Explicit test sessions. Never a production fallback."""

    def __init__(self) -> None:
        self.tokens: dict[str, SessionContext] = {
            "operator": SessionContext("s_op", "operator", Actor("operator", "operator_sim"), None, frozenset({Role.OPERATOR})),
            "crew_a": SessionContext("s_a", "crew", Actor("person", "per_a"), "per_a", frozenset({Role.CREW})),
            "crew_b": SessionContext("s_b", "crew", Actor("person", "per_b"), "per_b", frozenset({Role.CREW})),
            "cmo": SessionContext("s_c", "crew", Actor("person", "per_a"), "per_a", frozenset({Role.CREW_MEDICAL_OFFICER})),
            "adapter": SessionContext("s_ad", "adapter", Actor("adapter", ADAPTER), None, frozenset({Role.ADAPTER})),
            "other_adapter": SessionContext("s_ad2", "adapter", Actor("adapter", "adp_other"), None, frozenset({Role.ADAPTER})),
            "ground": SessionContext("s_g", "ground", Actor("ground", "ground_sim"), None, frozenset({Role.GROUND})),
        }

    def authenticate(self, request: Request) -> SessionContext:
        header = request.headers.get("Authorization", "")
        session = self.tokens.get(header.removeprefix("Bearer "))
        if session is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, "explicit test authentication")
        return session

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        return None

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return True


def _documents() -> dict[ConfigName, ConfigDocumentView]:
    views: dict[ConfigName, ConfigDocumentView] = {}
    for name in ConfigName:
        real = ROOT / "config" / f"{name.value}.json"
        hazard = ROOT / "docs/product" / f"{name.value}.json"
        if real.is_file() and name.value in {"sensor-catalog", "sensor-limits", "environment-profiles"}:
            raw = real.read_bytes()
        elif hazard.is_file():
            raw = hazard.read_bytes()
        else:
            suffix = "api/examples/kit-map.example.json" if name == ConfigName.KIT_MAP else f"api/examples/config/{name.value}.example.json"
            wrapped = json.loads((ROOT / "docs/product" / suffix).read_bytes())
            raw = json.dumps(wrapped if name == ConfigName.KIT_MAP else wrapped["value"]).encode()
        views[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return views


def _settings(tmp: Path, registry_file: Path) -> ValidatedSettings:
    from crew_health.clock.settings import specifications as clock_specs
    from crew_health.config.settings import specifications as config_specs
    from crew_health.events.settings import specifications as event_specs
    from crew_health.platform.settings import specifications as platform_specs
    from crew_health.records.settings import specs as record_specs

    core = (*platform_specs(), *config_specs(), *clock_specs(), *event_specs(), *record_specs())
    specs = (*core, *mission_specs(), *sensor_specs())
    values: dict[str, object] = {s.key: int(s.maximum) for s in core if s.maximum is not None}
    values.update(MISSION_DEFAULTS)
    values.update(SENSOR_DEFAULTS)
    values["sensors.channel_registry_path"] = str(registry_file)
    values["sensors.unit_mapping_path"] = str(ROOT / "config/unit-mapping.json")
    return ValidatedSettings(specs, values)


def _mapping_converter() -> MemoryWireConverter:
    rows = {
        (m["indicator_code"], m["wire_unit"]): (m["conversion"], m["factor"])
        for m in json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))["mappings"]
    }

    def convert(value: WireQuantity, code: str, release: object, to: Unit, pressure: Any) -> ConvertedQuantity:
        method, factor = rows[(code, value.unit)]
        if method == "identity":
            canonical = value.value
        elif method == "scale":
            canonical = value.value * Decimal(str(factor))
        else:
            canonical = value.value / Decimal(1_000_000) * pressure.pressure.value
        return ConvertedQuantity(value, Quantity(canonical, to), method, "test-mapping", pressure, "half_even_4dp")

    return MemoryWireConverter(convert)


@dataclass
class Station:
    tmp: Path
    db: SQLiteDatabase
    app: Any
    client: TestClient
    security: Security
    clocks: PersistentClocks
    registry_file: Path

    @property
    def clock(self) -> MemoryClock:
        return self.db.clock  # type: ignore[return-value]

    def advance(self, **delta: float) -> None:
        self.clock.at = self.clock.at + timedelta(**delta)

    def headers(self, who: str, key: str | None = None, **extra: str) -> dict[str, str]:
        found = {"Authorization": "Bearer " + who}
        if key is not None:
            found["Idempotency-Key"] = key
        return {**found, **extra}

    def resolver(self) -> Any:
        return self.app.state.runtime.resolver

    def initialize(self, mode: str = "SIM", environment_id: str = "station_leo") -> str:
        return self.resolver().require(MissionInitializer).initialize(
            operator_id="operator_sim", mission_mode=mode, planned_length_days=None, site_node_id="node_1", environment_id=environment_id
        )

    def begin(self, actor: Actor | None = None) -> Any:
        return self.db.begin(actor=actor or Actor("operator", "operator_sim"), correlation_id="test_" + self.db.ids.new("t"))

    def dispatch(self) -> int:
        with self.begin(Actor("engine", "scheduler")) as uow:
            return self.db.scheduler.dispatch(uow, limit=50)

    def sweep(self) -> int:
        return self.resolver().require(FreshnessSweeper).sweep_once()


def build_station(tmp_path: Path, *, registry: dict[str, Any] | None = None, build_app: bool = True) -> Station:
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    registry_file = tmp_path / "sensor-channels.json"
    if registry is not None:
        registry_file.write_text(json.dumps(registry), encoding="utf-8")
    db = database(state)
    db.settings = _settings(tmp_path, registry_file)
    db.clock = MemoryClock(START, 0, "epoch_1")  # type: ignore[assignment]
    for module in ("config", "clock", "events", "records"):
        db.migrate((ROOT / "hub/src/crew_health" / module / "adapters/migrations" / f"0001_{module}.sql").read_text())
    artifacts, security, repository = E01["fixture_artifacts"](), Security(), ConfigRepository()
    editor = SessionContext("s_ed", "editor", Actor("operator", "operator_sim"), None, frozenset({Role.EDITOR}))
    event_repository = EventRepository(settings=db.settings, ids=db.ids, validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"))
    db.event_sink = event_repository.append
    service = ReleaseActivationService(
        repository,
        ConfigLoader(E01["counterpart_checks"](artifacts), max_document_bytes=db.settings.integer("commands.max_bytes")),
        db.ids,
        origin=lambda view: "SIM",
    )
    with db.begin(actor=editor.actor, correlation_id="e23_install") as uow:
        service.propose(uow, editor, CandidateRelease("release_e23", _documents()))
        service.activate(uow, editor, release_id="release_e23", expected_etag='"release_e23:1:0"')
    db.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
    _allow_sensor_inputs(db)
    clocks = _open_clocks(db)
    app = _app(db, security, artifacts, tmp_path) if build_app else None
    client = TestClient(app, raise_server_exceptions=False) if app is not None else None
    return Station(tmp_path, db, app, client, security, clocks, registry_file)  # type: ignore[arg-type]


def _allow_sensor_inputs(db: SQLiteDatabase) -> None:
    """E01 gap named in the PR report: its `evaluation.*` audit policies accept only observation and answer inputs,
    while S5.10.2 is also E23's isolation. The test station widens them to the sensor input manifest, nothing else."""
    from crew_health.platform.domain.audit import AuditPolicy

    for result in ("pending", "completed"):
        db.audit_policies["evaluation." + result] = AuditPolicy(
            "evaluation." + result,
            {},
            frozenset({"input", "completion"}),
            {"input": frozenset({"observation", "answer", "sensors_evaluation_input"}), "completion": frozenset({"evaluation"})},
        )


def _open_clocks(db: SQLiteDatabase) -> PersistentClocks:
    from crew_health.clock.adapters.repository import SqlClockRepository

    clocks = PersistentClocks(
        repository=SqlClockRepository(settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation),
        settings=db.settings,
        ids=db.ids,
    )
    version = 0
    with db.begin(actor=Actor("operator", "operator_sim"), correlation_id="e23_clock") as uow:
        for source in (HUB_CLOCK, ADAPTER):
            version += 1  # the segment's capture revision is the correction that opens it
            clocks.open(uow, ClockSegment(source, source + "_osc", source + "_seg", None, 0, version))
            clocks.apply(
                uow,
                ClockCorrection(
                    "cor_" + source, source, source + "_seg", version, datetime(1990, 1, 1, tzinfo=UTC), None, 0, 0, uow.now, "operator"
                ),
            )
        from crew_health.kernel.met import MissionClockVersion

        clocks.repository.append_mission_version(
            uow.connection,
            MissionClockVersion("clock_v1", 1, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC), None, "Day {day}"),
        )
    return clocks


def _app(db: SQLiteDatabase, security: Security, artifacts: Any, tmp_path: Path) -> Any:
    summaries = {ConfigName(r["name"]): r["basis_summary"] for r in json.loads((ROOT / "docs/product/api/examples/configuration/index.json").read_bytes())["value"]["documents"]}
    dimensions = {"mm[Hg]": "pressure", "[ppm]": "fraction", "uGy/h": "dose_rate", "mGy/h": "dose_rate", "mSv/d": "dose_per_day", "dB[A]": "level", "1": "index", "mg/m3": "concentration", "ug/m3": "concentration", "mg/L": "mass_per_volume"}
    codes = {m["ucum_unit"] for m in json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))["mappings"]}
    units = MemoryUnits({code: (dimensions.get(code, code), Decimal(1), Decimal(0)) for code in codes})
    holder: dict[str, Any] = {}

    def configure(registry: ModuleRegistry) -> None:
        registry.provide(E01["ConfigArtifacts"], lambda r: artifacts)
        registry.provide(ConfigHttpMetadata, lambda r: ConfigHttpMetadata(summaries, lambda view: "SIM", E01["FixtureVisibility"](E01["SimSecurity"]())))
        registry.provide(EventAccess, lambda r: EventAccess(security, (), {"config.release": security}))
        for identifier in sorted(required_contributors() - {"1", "2", "9", "13"} - MINE):
            registry.loader_check(MemoryReleaseCheck(identifier, lambda candidate: ()))
        registry.provide(UnitRegistry, lambda r: units)  # type: ignore[type-abstract]
        registry.provide(WireUnitConverter, lambda r: _mapping_converter())  # type: ignore[type-abstract]
        registry.provide(RosterPort, lambda r: _Roster())  # type: ignore[type-abstract]

        def isolation(r: Any) -> EvaluationService:
            return EvaluationService(
                tuple(registry.declarations["evaluators"].values()),  # type: ignore[arg-type]
                db.settings,
                db.ids,
                repository=EvaluationRepository(),
                capture=lambda uow: TimeInput(uow.now, HUB_CLOCK, HUB_CLOCK + "_seg", 1, "hub_corrected", "LIVE"),
                fault_codes=frozenset({"NON_FINITE_COMPUTATION"}),
            )

        registry.provide(EvaluationIsolation, isolation)  # type: ignore[type-abstract]
        mission_module.register(registry)
        sensors_module.register(registry)
        holder["registry"] = registry

    import traceback

    import crew_health.app.assembly as assembly

    original = assembly.Runtime.fail

    def loud(self: Any, code: str) -> None:  # a startup failure keeps its traceback in the test output
        traceback.print_exc()
        original(self, code)

    assembly.Runtime.fail = loud  # type: ignore[method-assign]
    try:
        app = _create(db, security, tmp_path, configure)
    finally:
        assembly.Runtime.fail = original  # type: ignore[method-assign]
    return app


def _create(db: SQLiteDatabase, security: Security, tmp_path: Path, configure: Any) -> Any:
    return create_app(
        database=db,
        build=E01["static_core"](tmp_path),
        contract_path=ROOT / "contracts/api/openapi.yaml",
        schema_directory=ROOT / "contracts/api/schemas",
        security=security,
        configure=configure,
    )


class _Roster:
    def person_ids(self) -> frozenset[str]:
        return ROSTER


def channel(channel_id: str, sensor_type_id: str, hazard_id: str, origin: str, unit: str, **extra: Any) -> dict[str, Any]:
    """A registry item from the catalog's own values; the cadence and staleness are passed in by the test."""
    item: dict[str, Any] = {
        "channel_id": channel_id,
        "sensor_type_id": sensor_type_id,
        "hazard_id": hazard_id,
        "environment_ids": ["station_leo", "transit"],
        "location": "cabin",
        "location_label": None,
        "adapter_id": ADAPTER,
        "origin_mode": origin,
        "input_unit": unit,
        "expected_interval_seconds": 5,
        "stale_after_seconds": 300,
        "source_label": "Test " + origin,
        "dataset": "Test dataset" if origin == "REPLAY" else None,
        "display_note": None,
        "trusted": True,
        "health_url": "http://127.0.0.1:9/health",
    }
    item.update(extra)
    return item


def reading(channel_id: str, sequence: int, value: float, unit: str, observed_at: datetime, origin: str = "SIM", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "channel_id": channel_id,
        "adapter_sequence": sequence,
        "value": value,
        "unit": unit,
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin_mode": origin,
        "quality_status": "accepted",
    }
    if origin == "REPLAY":
        row["replay"] = {"dataset": "Test dataset", "time_basis": "as in the source file"}
    row.update(extra)
    return row


__all__ = ["ADAPTER", "START", "Station", "build_station", "channel", "reading"]
