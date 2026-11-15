"""The served hub binds the platform's UCUM registry (not a test double), so every UCUM code the unit mapping names
must parse there; otherwise check 12 rejects the real sensor catalog and the first release never activates."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from crew_health.config.adapters.units import ReleaseWireUnitConverter
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Unit, WireQuantity
from crew_health.platform.adapters.units import UcumUnitRegistry
from crew_health.sensors.adapters.config_reader import load_sensor_config
from crew_health.sensors.domain.config import SensorConfig

ROOT = Path(__file__).resolve().parents[3]


def test_every_ucum_unit_of_the_unit_mapping_parses_in_the_platform_registry() -> None:
    registry = UcumUnitRegistry(max_cached=64)
    rows = json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))["mappings"]
    unknown = sorted({row["ucum_unit"] for row in rows if _fails(registry, row["ucum_unit"])})
    assert unknown == []


def _fails(registry: UcumUnitRegistry, code: str) -> bool:
    try:
        registry.parse(code)
    except Exception:
        return True
    return False


def test_every_canonical_unit_converts_through_the_real_converter_and_registry(
    config: SensorConfig, release: MemoryRelease
) -> None:
    """The catalog spells a canonical unit for people (mmHg, dBA); the converter and registry speak UCUM, so ingest
    hands them the unit mapping's code. Each sensor type's own unit must round trip through the real pair."""
    registry = UcumUnitRegistry(max_cached=64)
    converter = ReleaseWireUnitConverter(registry)
    for kind in config.sensor_types:
        code = config.canonical_unit(kind.id)
        assert code is not None, kind.id
        converted = converter.convert(
            WireQuantity(Decimal(2), kind.unit), indicator_code=kind.id, release=release, to=Unit(code)
        )
        assert converted.canonical.unit == Unit(code) and converted.canonical.value == Decimal(2), kind.id


def test_a_release_without_a_unit_mapping_names_no_canonical_unit(
    raw_documents: dict[ConfigName, bytes],
) -> None:
    bare = {name: raw for name, raw in raw_documents.items() if name != ConfigName.UNIT_MAPPING}
    config = load_sensor_config(lambda name: bare[name])
    assert config.sensor_types and all(config.canonical_unit(kind.id) is None for kind in config.sensor_types)
