"""Explicit test profiles, boundary fakes and a real records-backed SQLite station."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.contracts_gen.enums import Role
from crew_health.kernel.clock import ClockCorrection, ClockSegment, NormalizedTime, TimeInput
from crew_health.kernel.config import ConfigDocumentView, ConfigName, ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryIds,
    MemoryMissionClock,
    MemoryRelease,
    MemorySettings,
    MemoryUnits,
    MemoryWireConverter,
)
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit, WireQuantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings as platform_settings
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.commands import RecordsCommands
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.application.visibility import OwnerAuthorization
from crew_health.records.domain.observations import (
    ObservationInput,
    ObservationPolicy,
    ObservationRecord,
    RecordContext,
)
from crew_health.trends.application.ports import (
    ExclusionPeriods,
    ForecastRequest,
    OriginMode,
    SeriesSlice,
    WorkerForecast,
    WorkerUnavailable,
)
from crew_health.trends.settings import specs

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
EPOCH = datetime(2026, 11, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


def defaults() -> dict[str, object]:
    return {
        "trends.history_max_rows": 1000,
        "trends.max_codes": 200,
        "trends.environment_points_max": 1000,
        "trends.forecast_steps_max": 20,
        "trends.forecast_staging_max": 8,
        "workers.forecast.enabled": False,
        "workers.forecast.base_url": "http://127.0.0.1:1",
        "workers.forecast.token_path": "missing-test-token",
        "workers.forecast.timeout_ms": 100,
        "workers.forecast.response_max_bytes": 4096,
    }


def settings(**overrides: object) -> MemorySettings:
    return MemorySettings(specs(), defaults() | overrides)


def catalog_value() -> dict[str, object]:
    raw: object = json.loads(
        (ROOT / "docs/product/api/examples/config/indicator-catalog.example.json").read_text(encoding="utf-8")
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("value"), dict):
        raise ValueError("explicit catalog profile missing")
    return cast(dict[str, object], raw["value"])


def catalog_release(value: dict[str, object] | None = None) -> MemoryRelease:
    raw = json.dumps(catalog_value() if value is None else value, sort_keys=True).encode("utf-8")
    document = ConfigDocumentView(ConfigName.INDICATOR_CATALOG, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release_1", {ConfigName.INDICATOR_CATALOG: document})


def person_session(
    person_id: str = "person", kind: Literal["person", "station", "assistant"] = "person"
) -> SessionContext:
    return SessionContext(
        "session_" + person_id,
        "crew" if kind == "person" else "station",
        Actor(kind, person_id),
        person_id,
        frozenset({Role.CREW}),
    )


def mission_clock() -> MemoryMissionClock:
    return MemoryMissionClock((MissionClockVersion("mission_clock_1", 1, EPOCH, EPOCH, None, "Day {day}"),))


def record(
    observation_id: str,
    quantity: Quantity,
    at: datetime,
    *,
    origin_mode: OriginMode = "SIM",
    code: str = "pulse",
    state: Literal["known", "unknown", "future", "replay"] = "known",
) -> ObservationRecord:
    """A records ObservationRecord as RecordsQueries returns it; trends reads it through its structural port."""
    return ObservationRecord(
        observation_id,
        "person",
        "person",
        "mission",
        None,
        code,
        quantity,
        ConvertedQuantity(
            WireQuantity(quantity.value, quantity.unit.code),
            quantity,
            "identity",
            "unit_mapping_test",
            None,
            "none",
        ),
        TimeInput(at, "source", "segment", 1, "source_raw", origin_mode),
        NormalizedTime(at, None if state == "unknown" else at, None, None, state),
        at,
        at,
        origin_mode,
        "self_report",
        "accepted",
        "quality_test_1",
        (),
        None,
        (),
        None,
        None,
        "release_1",
        None,
        None,
        "care_basis_test",
    )


def timed(
    row: ObservationRecord, at: datetime, state: Literal["known", "unknown", "future", "replay"] = "known"
) -> ObservationRecord:
    return replace(
        row,
        observed=replace(row.observed, original_at=at, origin_mode=row.origin_mode),
        normalized=NormalizedTime(at, None if state == "unknown" else at, None, None, state),
    )


class EpisodeReader:
    def __init__(self, value: ExclusionPeriods | None = None) -> None:
        self.value = value if value is not None else ExclusionPeriods("episode_policy_1", (), True)

    def periods(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        until: datetime,
        limit: int,
    ) -> ExclusionPeriods:
        return self.value


class SensorReader:
    def __init__(self, value: SeriesSlice) -> None:
        self.value = value
        self.limit: int | None = None

    def accepted(self, channel_id: str, *, until: datetime, limit: int) -> SeriesSlice:
        self.limit = limit
        return self.value


class Worker:
    def __init__(self, value: WorkerForecast | WorkerUnavailable) -> None:
        self.value = value
        self.calls: list[ForecastRequest] = []

    def forecast(self, request: ForecastRequest) -> WorkerForecast | WorkerUnavailable:
        self.calls.append(request)
        return self.value


class _StationSettings:
    def __init__(self) -> None:
        self.base = platform_settings()
        self.trends = settings()

    def integer(self, key: str) -> int:
        return (self.trends if key in defaults() else self.base).integer(key)

    def quantity(self, key: str) -> Quantity:
        return self.base.quantity(key)

    def boolean(self, key: str) -> bool:
        return self.trends.boolean(key)

    def text(self, key: str) -> str:
        return self.trends.text(key)


class _Binding:
    def bind(self, uow: UnitOfWork, session: SessionContext, value: ObservationInput) -> RecordContext:
        if session.person_id is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        return RecordContext(
            value.patient_id,
            session.person_id,
            "mission",
            value.origin_mode,
            TimeInput(value.observed_at, "source", "segment", 1, "source_raw", value.origin_mode),
            "self_capture",
        )


class _Policies:
    def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy:
        # Synthetic quality limits isolate trends from clinical policy decisions.
        unit = {
            "sleep_duration": "h",
            "pvt_median": "ms",
            "pulse": "/min",
            "respiratory_rate": "/min",
            "spo2": "%",
        }.get(code, "1")
        return ObservationPolicy(
            code,
            "quality_test_1",
            "synthetic_parser_test_profile",
            Unit(unit),
            frozenset({unit}),
            frozenset({"self_report", "manual_instrument"}),
            Decimal("-10000"),
            Decimal("10000"),
            Decimal("-10000"),
            Decimal("10000"),
            1000,
            Decimal("100000"),
            True,
        )


@dataclass(frozen=True)
class Station:
    db: SQLiteDatabase
    repo: RecordRepository
    commands: RecordsCommands
    queries: RecordsQueries
    reader: RecordsQueries
    session: SessionContext
    clock: MemoryClock


def station(tmp_path: Path) -> Station:
    clock = MemoryClock(NOW, 0, "epoch_1")
    ids = MemoryIds(identifiers=tuple(str(index) for index in range(10000)))
    hub_settings: HubSettings = _StationSettings()
    db = SQLiteDatabase(
        tmp_path / "hub.sqlite",
        clock=clock,
        ids=ids,
        release=catalog_release(),
        settings=hub_settings,
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=True,
    )
    for module in ("records", "clock", "trends"):
        db.migrate(
            (ROOT / "hub/src/crew_health" / module / "adapters/migrations" / f"0001_{module}.sql").read_text(
                encoding="utf-8"
            )
        )
    clocks = PersistentClocks(
        repository=SqlClockRepository(
            settings=hub_settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
        ),
        settings=hub_settings,
        ids=ids,
    )
    with db.begin(actor=Actor("operator", "operator"), correlation_id="setup") as uow:
        clocks.open(uow, ClockSegment("source", "oscillator", "segment", None, 1, 1))
        clocks.apply(
            uow, ClockCorrection("correction", "source", "segment", 1, EPOCH, None, 0, 0, NOW, "operator")
        )
    repo = RecordRepository()
    authorization = OwnerAuthorization()
    commands = RecordsCommands(
        repository=repo,
        ids=ids,
        units=MemoryUnits({unit: (unit, Decimal(1), Decimal(0)) for unit in ("1", "h", "ms", "/min", "%")}),
        converter=MemoryWireConverter(
            lambda value, code, release, to, pressure: ConvertedQuantity(
                value, Quantity(value.value, to), "identity", "mapping", None, "exact"
            )
        ),
        authorization=authorization,
        binding=_Binding(),
        policies=_Policies(),
        clocks=clocks,
    )
    queries = RecordsQueries(repo, authorization, hub_settings)
    return Station(db, repo, commands, queries, queries, person_session(), clock)
