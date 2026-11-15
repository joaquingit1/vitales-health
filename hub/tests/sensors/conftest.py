"""Shared fixtures for the E23 sensor rule tests.

Inputs come from the package configuration documents and the two NASA files under docs/product/, never from
clinical content typed into a test. The converter and unit resolver below are test fakes of the S5.4 ports; the
real versioned `unit-mapping.json` belongs to E01 (D12).
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.testing import MemoryRelease, MemoryUnits, MemoryWireConverter
from crew_health.kernel.units import (
    ConvertedQuantity,
    PressureContext,
    Quantity,
    Unit,
    WireQuantity,
)
from crew_health.sensors.adapters.config_reader import SensorConfig, load_sensor_config
from crew_health.sensors.domain.aggregation import DirectWindows
from crew_health.sensors.domain.channel import ChannelFacts, Freshness, Origin, SensorTypeFacts
from crew_health.sensors.domain.evaluation import EvaluationInput, LimitFacts
from crew_health.sensors.domain.loader_checks import SensorBounds
from crew_health.sensors.domain.reading import Instant

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # e23_station for the integration tests
PRODUCT = ROOT / "docs" / "product"
EXAMPLES = PRODUCT / "api" / "examples" / "config"
NASA = PRODUCT / "nasa-data"

BOUNDS = SensorBounds(aggregate_bucket_s=60, window_max_buckets=10080, window_scan_max_readings=20000)
SCAN_MAX = BOUNDS.window_scan_max_readings

# The real D12 mapping (config/unit-mapping.json) is the unit table of these tests: wire spelling to UCUM code and
# the factor of a scale row. Only the dimension tag of a UCUM code is a test fake (the registry is E01's).
MAPPING = json.loads((ROOT / "config" / "unit-mapping.json").read_text(encoding="utf-8"))["mappings"]
DIMENSION = {
    "mm[Hg]": "pressure",
    "[ppm]": "fraction",
    "uGy/h": "dose_rate",
    "mGy/h": "dose_rate",
    "mSv/d": "dose_per_day",
    "dB": "level",
    "1": "index",
    "mg/m3": "concentration",
    "ug/m3": "concentration",
    "mg/L": "mass_per_volume",
}
UCUM = {
    (m["indicator_code"], m["wire_unit"]): (m["ucum_unit"], m["conversion"], m["factor"]) for m in MAPPING
}


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e23: E23 environment profile and habitat sensors")


def document_view(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def config_bytes(name: str) -> bytes:
    return (ROOT / "config" / f"{name}.json").read_bytes()


def example_bytes(name: str) -> bytes:
    value = json.loads((EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8"))["value"]
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


@pytest.fixture(scope="session")
def raw_documents() -> dict[ConfigName, bytes]:
    return {
        ConfigName.SENSOR_CATALOG: config_bytes("sensor-catalog"),
        ConfigName.SENSOR_LIMITS: config_bytes("sensor-limits"),
        ConfigName.ENVIRONMENT_PROFILES: config_bytes("environment-profiles"),
        ConfigName.UNIT_MAPPING: config_bytes("unit-mapping"),
        ConfigName.ENVIRONMENT_HAZARDS: (PRODUCT / "environment-hazards.json").read_bytes(),
    }


@pytest.fixture(scope="session")
def config(raw_documents: dict[ConfigName, bytes]) -> SensorConfig:
    return load_sensor_config(lambda name: raw_documents[name])


@pytest.fixture(scope="session")
def release(raw_documents: dict[ConfigName, bytes]) -> MemoryRelease:
    views = {name: document_view(name, raw) for name, raw in raw_documents.items()}
    return MemoryRelease("rel_test_0001", views)


@pytest.fixture(scope="session")
def units() -> MemoryUnits:
    table: dict[str, tuple[str, Decimal, Decimal]] = {}
    for code, _, _ in UCUM.values():
        table[code] = (DIMENSION.get(code, code), Decimal(1), Decimal(0))
    return MemoryUnits(table)


def ucum_for(sensor_type_id: str, wire_unit: str) -> str | None:
    entry = UCUM.get((sensor_type_id, wire_unit))
    return None if entry is None else entry[0]


@pytest.fixture(scope="session")
def converter(config: SensorConfig) -> MemoryWireConverter:
    """Test fake of WireUnitConverter driven by the catalog's accepted_units."""

    def convert(
        value: WireQuantity, indicator: str, release: object, to: Unit, pressure: PressureContext | None
    ) -> ConvertedQuantity:
        row = UCUM.get((indicator, value.unit))  # the factor comes from the mapping file, not the catalog
        if row is None:
            raise AssertionError("fake converter reached with an unmapped unit")
        _, method, factor = row
        if method == "identity":
            canonical = value.value
        elif method == "scale":
            assert factor is not None
            canonical = value.value * Decimal(factor)
        else:
            assert pressure is not None
            canonical = value.value / Decimal(1_000_000) * pressure.pressure.value
        return ConvertedQuantity(
            value, Quantity(canonical, to), method, "test-mapping-1", pressure, "half_even_4dp"
        )

    return MemoryWireConverter(convert)


def assumed_pressure(mmhg: str = "760") -> PressureContext:
    """A declared assumption with its source, never a default of the code under test."""
    return PressureContext(Quantity(Decimal(mmhg), Unit("mm[Hg]")), "assumed", "test:assumed-sea-level", None)


def sensor(config: SensorConfig, sensor_type_id: str) -> SensorTypeFacts:
    found = config.sensor_type(sensor_type_id)
    assert found is not None
    return found


def channel_for(
    config: SensorConfig,
    sensor_type_id: str,
    *,
    channel_id: str = "ch_test",
    origin: Origin = "REPLAY",
    interval: int | None = None,
    location: str | None = None,
    source: str = "Replay, test",
    environments: tuple[str, ...] = ("station_leo",),
    note: str | None = None,
) -> ChannelFacts:
    kind = sensor(config, sensor_type_id)
    return ChannelFacts(
        channel_id=channel_id,
        sensor_type_id=sensor_type_id,
        hazard_id=kind.hazard_id,
        adapter_id="adp_test",
        origin_mode=origin,
        location_label=location,
        source_label=source,
        dataset="test dataset" if origin == "REPLAY" else None,
        display_note=note,
        expected_interval_seconds=interval,
        stale_after_seconds=kind.stale_after_seconds,
        environment_ids=environments,
    )


def evaluation_input(
    config: SensorConfig,
    sensor_type_id: str,
    instants: Sequence[Instant],
    *,
    interval: int | None,
    environment: str = "station_leo",
    freshness: Freshness = "current",
    advanced: bool = True,
    trusted: bool = True,
    limits: tuple[LimitFacts, ...] | None = None,
    kind: SensorTypeFacts | None = None,
    scan_max: int = SCAN_MAX,
    evaluated_at: datetime | None = None,
) -> EvaluationInput:
    chosen = kind if kind is not None else sensor(config, sensor_type_id)
    ordered = tuple(sorted(instants, key=lambda i: i.at))
    return EvaluationInput(
        sensor_type=chosen,
        channel=channel_for(config, sensor_type_id, interval=interval, environments=(environment,)),
        environment_id=environment,
        limits=config.limits if limits is None else limits,
        windows=DirectWindows(ordered, interval, scan_max),
        window_end=max((i.at for i in ordered), default=None),
        freshness=freshness,
        window_advanced=advanced,
        clock_trusted=trusted,
        evaluated_at=evaluated_at if evaluated_at is not None else utc(2026, 11, 14),
    )


def point(at: datetime, value: str | Decimal) -> Instant:
    amount = Decimal(value)
    return Instant(at, amount, amount)


@dataclass(frozen=True, slots=True)
class CsvRow:
    at: datetime
    value: Decimal


def read_rr1_ppm() -> list[CsvRow]:
    """RR-1 ISS CO2 in ppm (column co2_iss), time as in the source file, read as UTC."""
    with (NASA / "habitat-environment" / "eda_rr1_telemetry.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return [
            CsvRow(datetime.fromisoformat(r["time"]).replace(tzinfo=UTC), Decimal(r["co2_iss"]))
            for r in csv.DictReader(handle)
        ]


def read_dostel1() -> list[CsvRow]:
    with (NASA / "radiation" / "radlab_dostel_sample.csv").open(newline="", encoding="utf-8") as handle:
        return [
            CsvRow(
                datetime.fromisoformat(r["timestamp"]).replace(tzinfo=UTC), Decimal(r["absorbed_dose_rate"])
            )
            for r in csv.DictReader(handle)
            if r["instrument_id"] == "DosTel1"
        ]


def hours(count: int) -> timedelta:
    return timedelta(hours=count)


Mutator = Callable[[dict[str, object]], None]


def _publish_helpers() -> None:
    """Test modules import these as `e23_helpers`: importlib import mode cannot import conftest directly."""
    helpers = types.ModuleType("e23_helpers")
    for name in (
        "BOUNDS",
        "SCAN_MAX",
        "UCUM",
        "NASA",
        "PRODUCT",
        "EXAMPLES",
        "ROOT",
        "assumed_pressure",
        "channel_for",
        "document_view",
        "evaluation_input",
        "example_bytes",
        "hours",
        "point",
        "read_dostel1",
        "read_rr1_ppm",
        "sensor",
        "ucum_for",
        "utc",
        "CsvRow",
    ):
        setattr(helpers, name, globals()[name])
    sys.modules["e23_helpers"] = helpers


_publish_helpers()
