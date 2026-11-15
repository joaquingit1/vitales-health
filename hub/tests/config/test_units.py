"""D12, SYSTEM 5.4, AGENTS 1.1/1.8: release-pinned, sourced wire conversion."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.config.adapters.repository import PinnedRelease
from crew_health.config.adapters.units import ReleaseWireUnitConverter
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import PressureContext, Quantity, Unit, WireQuantity
from crew_health.platform.adapters.units import UcumUnitRegistry

pytestmark = [pytest.mark.E01, pytest.mark.trace("D12", "SYSTEM-5.4", "AGENTS-1.1", "AGENTS-1.8")]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def converter() -> ReleaseWireUnitConverter:
    return ReleaseWireUnitConverter(UcumUnitRegistry(max_cached=64))


def pinned(mapping: object | None = None) -> PinnedRelease:
    raw = (
        (ROOT / "config/unit-mapping.json").read_bytes() if mapping is None else json.dumps(mapping).encode()
    )
    sensors = json.loads(
        (ROOT / "docs/product/api/examples/config/sensor-catalog.example.json").read_bytes()
    )["value"]
    return PinnedRelease(
        "release_test",
        {
            name: ConfigDocumentView(name, hashlib.sha256(data).hexdigest(), data)
            for name, data in (
                (ConfigName.UNIT_MAPPING, raw),
                (ConfigName.SENSOR_CATALOG, json.dumps(sensors).encode()),
            )
        },
    )


@pytest.mark.parametrize("code,wire,target", [("pulse", "bpm", "/min"), ("systolic_bp", "mmHg", "mm[Hg]")])
def test_wire_spellings_map_by_indicator(
    converter: ReleaseWireUnitConverter, code: str, wire: str, target: str
) -> None:
    original = WireQuantity(Decimal("72"), wire)
    result = converter.convert(original, indicator_code=code, release=pinned(), to=Unit(target))
    assert result.canonical == Quantity(Decimal("72.0000"), Unit(target))
    assert result.original == original
    # The conversion carries the deployed document's own version (E07 extended it to spo2 and temperature).
    assert result.mapping_version == json.loads((ROOT / "config/unit-mapping.json").read_bytes())["version"]
    assert result.rounding == "half_even:4"
    assert result.pressure is None


def test_ppm_uses_explicit_sourced_pressure(converter: ReleaseWireUnitConverter) -> None:
    pressure = PressureContext(
        Quantity(Decimal("700"), Unit("mm[Hg]")), "measured", "observation_pressure", None
    )
    result = converter.convert(
        WireQuantity(Decimal("5000"), "ppm"),
        indicator_code="co2",
        release=pinned(),
        to=Unit("mm[Hg]"),
        pressure=pressure,
    )
    assert result.canonical == Quantity(Decimal("3.5000"), Unit("mm[Hg]"))
    assert result.pressure == pressure
    assert result.conversion == "ppm_to_mmhg"


@pytest.mark.parametrize(
    "wire,code,target,expected",
    [
        ("ppm", "co2", "mm[Hg]", ErrorCode.INVALID_VALUE),
        ("BPM", "pulse", "/min", ErrorCode.INVALID_UNIT),
        ("bpm", "unknown", "/min", ErrorCode.INVALID_UNIT),
        ("bpm", "pulse", "mm[Hg]", ErrorCode.INVALID_UNIT),
    ],
)
def test_missing_pressure_and_unknown_units_fail(
    converter: ReleaseWireUnitConverter, wire: str, code: str, target: str, expected: ErrorCode
) -> None:
    with pytest.raises(ProblemError) as error:
        converter.convert(
            WireQuantity(Decimal("5"), wire), indicator_code=code, release=pinned(), to=Unit(target)
        )
    assert error.value.code == expected


@pytest.mark.parametrize(
    "value,expected", [("1.23445", "1.2344"), ("1.23455", "1.2346"), ("-1.23445", "-1.2344")]
)
def test_canonical_rounds_once_half_even(
    converter: ReleaseWireUnitConverter, value: str, expected: str
) -> None:
    result = converter.convert(
        WireQuantity(Decimal(value), "bpm"), indicator_code="pulse", release=pinned(), to=Unit("/min")
    )
    assert result.canonical.value == Decimal(expected)
    assert result.original.value == Decimal(value)


@given(
    st.decimals(min_value="-1000000", max_value="1000000", places=4, allow_nan=False, allow_infinity=False)
)
def test_per_minute_conversion_round_trip_is_exact(
    converter: ReleaseWireUnitConverter, value: Decimal
) -> None:
    result = converter.convert(
        WireQuantity(value, "bpm"), indicator_code="pulse", release=pinned(), to=Unit("/min")
    )
    round_trip = converter.registry.convert(result.canonical, Unit("/min"))
    assert round_trip.value == value


def test_configuration_edit_changes_mapping_without_code_change(converter: ReleaseWireUnitConverter) -> None:
    mapping = json.loads((ROOT / "config/unit-mapping.json").read_bytes())
    mapping["version"] = "edited-mapping"
    mapping["mappings"][0]["ucum_unit"] = "/s"
    old = converter.convert(
        WireQuantity(Decimal("1"), "bpm"), indicator_code="pulse", release=pinned(), to=Unit("/min")
    )
    new = converter.convert(
        WireQuantity(Decimal("1"), "bpm"), indicator_code="pulse", release=pinned(mapping), to=Unit("/min")
    )
    assert old.canonical.value == Decimal("1")
    assert new.canonical.value == Decimal("60")
    assert new.mapping_version == "edited-mapping"


@pytest.mark.parametrize(
    "pressure",
    [
        PressureContext(Quantity(Decimal("0"), Unit("mm[Hg]")), "assumed", "source", None),
        PressureContext(Quantity(Decimal("700"), Unit("mm[Hg]")), "assumed", "", None),
    ],
)
def test_invalid_pressure_never_supplies_default(
    converter: ReleaseWireUnitConverter, pressure: PressureContext
) -> None:
    with pytest.raises(ProblemError) as error:
        converter.convert(
            WireQuantity(Decimal("5000"), "ppm"),
            indicator_code="co2",
            release=pinned(),
            to=Unit("mm[Hg]"),
            pressure=pressure,
        )
    assert error.value.code == ErrorCode.INVALID_VALUE


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing_source", "unknown_ucum", "sensor_disallows", "bad_scale"]
)
def test_mapping_defects_fail_closed(converter: ReleaseWireUnitConverter, fault: str) -> None:
    mapping = json.loads((ROOT / "config/unit-mapping.json").read_bytes())
    code, wire, target = "pulse", "bpm", "/min"
    expected = ErrorCode.INVALID_UNIT
    if fault == "duplicate":
        mapping["mappings"].append(mapping["mappings"][0])
    elif fault == "missing_source":
        mapping["mappings"][0]["source_refs"] = []
        expected = ErrorCode.INVALID_VALUE
    elif fault == "unknown_ucum":
        mapping["mappings"][0]["ucum_unit"] = "not_a_unit"
    elif fault == "sensor_disallows":
        mapping["mappings"][0]["indicator_code"] = "co2"
        code = "co2"
    else:
        mapping["mappings"][0]["conversion"] = "scale"
        expected = ErrorCode.INVALID_VALUE
    with pytest.raises(ProblemError) as error:
        converter.convert(
            WireQuantity(Decimal("1"), wire), indicator_code=code, release=pinned(mapping), to=Unit(target)
        )
    assert error.value.code == expected


def test_registered_converter_resolves_with_injected_registry() -> None:
    from collections.abc import Callable
    from typing import cast

    from crew_health.app.registry import DeclarationRegistry, Resolver
    from crew_health.config.module import register
    from crew_health.kernel.units import UnitRegistry, WireUnitConverter

    declarations = DeclarationRegistry(max_ports=32, max_hooks=128)
    register(declarations)
    factory, dependencies = declarations.providers[WireUnitConverter]
    assert dependencies == (UnitRegistry,)
    fresh = DeclarationRegistry(max_ports=8, max_hooks=16)
    registry = UcumUnitRegistry(max_cached=64)
    fresh.provide(UnitRegistry, lambda resolver: registry)  # type: ignore[type-abstract]
    built = cast(Callable[[Resolver], WireUnitConverter], factory)
    fresh.provide(WireUnitConverter, built, requires=dependencies)  # type: ignore[type-abstract]
    converter = fresh.resolve().require(WireUnitConverter)  # type: ignore[type-abstract]
    result = converter.convert(
        WireQuantity(Decimal("72"), "bpm"), indicator_code="pulse", release=pinned(), to=Unit("/min")
    )
    assert result.canonical.value == Decimal("72.0000")


def test_scale_factor_uses_decimal_configuration(converter: ReleaseWireUnitConverter) -> None:
    mapping = json.loads((ROOT / "config/unit-mapping.json").read_bytes())
    mapping["mappings"][0]["conversion"] = "scale"
    mapping["mappings"][0]["factor"] = "0.5"
    result = converter.convert(
        WireQuantity(Decimal("72"), "bpm"), indicator_code="pulse", release=pinned(mapping), to=Unit("/min")
    )
    assert result.canonical.value == Decimal("36.0000")


def test_unrepresentable_canonical_value_is_invalid_value(converter: ReleaseWireUnitConverter) -> None:
    with pytest.raises(ProblemError) as error:
        converter.convert(
            WireQuantity(Decimal("1e100"), "bpm"), indicator_code="pulse", release=pinned(), to=Unit("/min")
        )
    assert error.value.code == ErrorCode.INVALID_VALUE


@pytest.mark.parametrize("fault", ["invalid_catalog", "contextual_ucum", "zero_scale", "identity_factor"])
def test_invalid_pinned_conversion_metadata_is_rejected(
    converter: ReleaseWireUnitConverter, fault: str
) -> None:
    mapping = json.loads((ROOT / "config/unit-mapping.json").read_bytes())
    code, wire, target = "pulse", "bpm", "/min"
    expected = ErrorCode.INVALID_VALUE
    if fault == "contextual_ucum":
        mapping["mappings"][4]["ucum_unit"] = "/min"
        code, wire, target = "co2", "ppm", "mm[Hg]"
        expected = ErrorCode.INVALID_UNIT
    elif fault == "zero_scale":
        mapping["mappings"][0].update(conversion="scale", factor="0")
    elif fault == "identity_factor":
        mapping["mappings"][0]["factor"] = "2"
    release = pinned(mapping)
    if fault == "invalid_catalog":
        raw = b"{}"
        release = PinnedRelease(
            "release_bad",
            {
                **release.documents,
                ConfigName.SENSOR_CATALOG: ConfigDocumentView(
                    ConfigName.SENSOR_CATALOG, hashlib.sha256(raw).hexdigest(), raw
                ),
            },
        )
    with pytest.raises(ProblemError) as error:
        converter.convert(
            WireQuantity(Decimal("1"), wire), indicator_code=code, release=release, to=Unit(target)
        )
    assert error.value.code == expected


def test_ppm_can_retain_ucum_ratio_without_inventing_pressure(converter: ReleaseWireUnitConverter) -> None:
    result = converter.convert(
        WireQuantity(Decimal("5000"), "ppm"), indicator_code="co2", release=pinned(), to=Unit("[ppm]")
    )
    assert result.canonical == Quantity(Decimal("5000.0000"), Unit("[ppm]"))
    assert result.pressure is None
