"""Unit tests of pharmacy.exposure (spec 3): normalization, zero-order hold and gaps, the
gap policies, declared zones, custody and package windows, lot exposure, MKT (vectors
TV-MKT-2, -3, -7, -8) and the exposure summary."""

import copy
import dataclasses
import math
from collections.abc import Callable
from typing import Any

import pytest

from pharmacy import exposure
from pharmacy.config import ConfigBundle
from pharmacy.constants import ZERO_CELSIUS_K
from pharmacy.errors import (
    ConfigError,
    ConfigReferenceError,
    InputError,
    SensorDataError,
    TimeWindowError,
    UnitError,
)
from pharmacy.types import (
    EngineSettingsDoc,
    LotDoc,
    PackageDoc,
    SensorPoint,
    SensorSeries,
    StorageZoneDoc,
    iso_to_seconds,
    seconds_to_iso,
)
from tests.unit.core import builders as b

T0 = "2028-03-01T00:00:00Z"
T0_S = iso_to_seconds(T0)
HOUR = 3600.0
DAY = 86400.0


@pytest.fixture(scope="module")
def zones(bundle: ConfigBundle) -> dict[str, StorageZoneDoc]:
    return {zone["zone_id"]: zone for zone in bundle.zones["zones"]}


@pytest.fixture(scope="module")
def packages(bundle: ConfigBundle) -> dict[str, PackageDoc]:
    return {package["package_id"]: package for package in bundle.packages["packages"]}


@pytest.fixture(scope="module")
def lots(bundle: ConfigBundle) -> dict[str, LotDoc]:
    return {lot["lot_id"]: lot for lot in bundle.lots["lots"]}


@pytest.fixture(scope="module")
def settings(bundle: ConfigBundle) -> EngineSettingsDoc:
    return bundle.settings


def _sensor(zone: StorageZoneDoc, sensor_id: str) -> Any:
    return next(s for s in zone["sensors"] if s["sensor_id"] == sensor_id)


def _at(offset_s: float) -> str:
    return seconds_to_iso(T0_S + offset_s)


def _series(
    sensor_id: str,
    quantity: Any,
    unit: str,
    values: list[float],
    times: list[float],
    zone: str = "station_kit",
) -> SensorSeries:
    points = tuple(SensorPoint(_at(t), T0_S + t, v) for t, v in zip(times, values, strict=True))
    return SensorSeries(f"s-{sensor_id}", sensor_id, zone, quantity, unit, points)


def _full_station(
    hours: int, temperature: float = 298.15, rh: float = 45.0, dose: float = 1e-9
) -> list[SensorSeries]:
    end = _at(hours * HOUR)
    return [
        b.constant_series("s-t", "stk-t1", "station_kit", "temperature_k", "K", T0, end, HOUR, temperature),
        b.constant_series(
            "s-rh", "stk-rh1", "station_kit", "relative_humidity", "percent", T0, end, HOUR, rh
        ),
        b.constant_series(
            "s-dos", "stk-dos1", "station_kit", "dose_rate_gy_per_s", "Gy/s", T0, end, HOUR, dose
        ),
    ]


# =========================================================================================
# normalize_series (spec 3.3)
# =========================================================================================


def test_normalize_converts_celsius_and_applies_the_calibration_offset(
    zones: dict[str, StorageZoneDoc],
) -> None:
    sensor = copy.deepcopy(_sensor(zones["station_kit"], "stk-t1"))
    sensor["calibration_offset"]["value"] = -0.25
    raw = _series("stk-t1", "temperature_k", "degC", [20.0, 21.5], [0.0, HOUR])
    out = exposure.normalize_series(raw, sensor)
    assert out.unit == "K"
    assert [p.value for p in out.points] == [20.0 + ZERO_CELSIUS_K - 0.25, 21.5 + ZERO_CELSIUS_K - 0.25]
    assert [p.t_utc for p in out.points] == [p.t_utc for p in raw.points]
    assert exposure.normalize_series(dataclasses.replace(raw, points=()), sensor).points == ()


def _bad_series(kind: str) -> tuple[SensorSeries, str]:
    good = [SensorPoint(_at(0.0), T0_S, 40.0), SensorPoint(_at(HOUR), T0_S + HOUR, 41.0)]
    base = SensorSeries("s-rh", "stk-rh1", "station_kit", "relative_humidity", "percent", tuple(good))
    if kind == "quantity":
        return dataclasses.replace(base, quantity="temperature_k", unit="K"), "SensorDataError"
    if kind == "sensor":
        return dataclasses.replace(base, sensor_id="stk-rh9"), "SensorDataError"
    if kind == "unit":
        return dataclasses.replace(base, unit="fraction"), "UnitError"
    if kind == "order":
        return dataclasses.replace(base, points=(good[1], good[0])), "SensorDataError"
    if kind == "repeat":
        return dataclasses.replace(base, points=(good[0], good[0])), "SensorDataError"
    if kind == "agreement":
        return dataclasses.replace(
            base, points=(SensorPoint(_at(0.0), T0_S + 0.01, 40.0),)
        ), "SensorDataError"
    if kind == "timestamp":
        return dataclasses.replace(base, points=(SensorPoint("yesterday", T0_S, 40.0),)), "SensorDataError"
    if kind == "nan":
        return dataclasses.replace(base, points=(SensorPoint(_at(0.0), T0_S, math.nan),)), "SensorDataError"
    if kind == "domain":
        return dataclasses.replace(base, points=(SensorPoint(_at(0.0), T0_S, 100.5),)), "SensorDataError"
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind", ["quantity", "sensor", "unit", "order", "repeat", "agreement", "timestamp", "nan", "domain"]
)
def test_normalize_rejects_bad_series(kind: str, zones: dict[str, StorageZoneDoc]) -> None:
    series, error = _bad_series(kind)
    expected = {"SensorDataError": SensorDataError, "UnitError": UnitError}[error]
    with pytest.raises(expected):
        exposure.normalize_series(series, _sensor(zones["station_kit"], "stk-rh1"))


def test_normalize_domain_per_quantity(zones: dict[str, StorageZoneDoc]) -> None:
    cold = _series("stk-t1", "temperature_k", "degC", [-274.0], [0.0])
    with pytest.raises(SensorDataError):
        exposure.normalize_series(cold, _sensor(zones["station_kit"], "stk-t1"))
    negative = _series("stk-dos1", "dose_rate_gy_per_s", "Gy/s", [-1e-12], [0.0])
    with pytest.raises(SensorDataError):
        exposure.normalize_series(negative, _sensor(zones["station_kit"], "stk-dos1"))
    zero = _series("stk-dos1", "dose_rate_gy_per_s", "Gy/s", [0.0], [0.0])
    assert exposure.normalize_series(zero, _sensor(zones["station_kit"], "stk-dos1")).points[0].value == 0.0


def test_series_pin_covers_points_up_to_as_of_only() -> None:
    series = _series("stk-t1", "temperature_k", "K", [290.0, 291.0, 292.0], [0.0, HOUR, 2 * HOUR])
    pin = exposure.series_pin(series, T0_S + HOUR)
    later_changed = dataclasses.replace(
        series, points=(*series.points[:2], SensorPoint(_at(2 * HOUR), T0_S + 2 * HOUR, 300.0))
    )
    assert exposure.series_pin(later_changed, T0_S + HOUR) == pin
    earlier_changed = dataclasses.replace(
        series, points=(SensorPoint(_at(0.0), T0_S, 289.0), *series.points[1:])
    )
    assert exposure.series_pin(earlier_changed, T0_S + HOUR).sha256 != pin.sha256
    assert pin.series_id == series.series_id
    assert len(pin.sha256) == 64


# =========================================================================================
# build_zone_history (spec 3.4 to 3.6)
# =========================================================================================


def test_fully_measured_zone_merges_into_one_segment(zones: dict[str, StorageZoneDoc]) -> None:
    history = exposure.build_zone_history(zones["station_kit"], _full_station(24), T0_S, T0_S + DAY)
    assert len(history.segments) == 1
    (segment,) = history.segments
    assert (segment.t0_s, segment.t1_s) == (T0_S, T0_S + DAY)
    assert segment.temperature_k == 298.15
    assert segment.relative_humidity_percent == 45.0
    assert segment.relative_humidity_low_percent == 45.0
    assert segment.dose_rate_gy_per_s == 1e-9
    assert segment.illuminance_lux == 0.0
    assert segment.uv_w_per_m2 == 0.0
    assert segment.package_id is None
    assert segment.gap_quantities == ()
    assert segment.declared_quantities == ()
    assert history.gaps == ()
    assert not history.coverage_low
    by_quantity = {c.quantity: c for c in history.coverage}
    # Readings after the window are not looked at, so the last one in it is held to the end.
    assert by_quantity["temperature_k"].measured_s == DAY - HOUR
    assert by_quantity["temperature_k"].held_s == HOUR
    assert by_quantity["temperature_k"].coverage_fraction == 1.0
    assert by_quantity["illuminance_lux"].not_applicable_s == DAY
    assert [pin.series_id for pin in history.series_pins] == ["s-dos", "s-rh", "s-t"]


def test_hold_gap_and_leading_gap_use_the_worst_case(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["station_kit"]
    temperature = _series("stk-t1", "temperature_k", "K", [295.0, 296.0], [2 * HOUR, 8 * HOUR])
    series = [temperature, *_full_station(12)[1:]]
    history = exposure.build_zone_history(zone, series, T0_S, T0_S + 12 * HOUR)
    worst = zone["worst_case"]
    t_max, t_min = worst["temperature_max_k"]["value"], worst["temperature_min_k"]["value"]
    temps = [(s.t0_s - T0_S, s.t1_s - T0_S, s.temperature_k, s.gap_quantities) for s in history.segments]
    assert temps == [
        (0.0, 2 * HOUR, t_max, ("temperature_k",)),
        (2 * HOUR, 3 * HOUR, 295.0, ()),
        (3 * HOUR, 8 * HOUR, t_max, ("temperature_k",)),
        (8 * HOUR, 9 * HOUR, 296.0, ()),
        (9 * HOUR, 12 * HOUR, t_max, ("temperature_k",)),
    ]
    assert [
        (g.t0_s - T0_S, g.t1_s - T0_S, g.fill_kind, g.fill_value, g.fill_value_low) for g in history.gaps
    ] == [
        (0.0, 2 * HOUR, "worst_case", t_max, t_min),
        (3 * HOUR, 8 * HOUR, "worst_case", t_max, t_min),
        (9 * HOUR, 12 * HOUR, "worst_case", t_max, t_min),
    ]
    coverage = {c.quantity: c for c in history.coverage}["temperature_k"]
    assert coverage.gap_s == 10 * HOUR
    assert coverage.held_s == 2 * HOUR
    assert coverage.longest_gap_s == 5 * HOUR
    assert coverage.coverage_fraction == pytest.approx(2 / 12)
    assert history.coverage_low


def test_reading_before_the_window_starts_it(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["station_kit"]
    temperature = _series("stk-t1", "temperature_k", "K", [294.0, 297.0], [-1800.0, 1800.0])
    series = [temperature, *_full_station(2)[1:]]
    history = exposure.build_zone_history(zone, series, T0_S, T0_S + HOUR)
    assert [(s.t1_s - T0_S, s.temperature_k) for s in history.segments] == [(1800.0, 294.0), (HOUR, 297.0)]
    assert {c.quantity: c for c in history.coverage}["temperature_k"].measured_s == 1800.0


def test_rh_and_dose_gaps(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["station_kit"]
    t, _, _ = _full_station(4)
    history = exposure.build_zone_history(zone, [t], T0_S, T0_S + 4 * HOUR)
    worst = zone["worst_case"]
    (segment,) = history.segments
    assert segment.relative_humidity_percent == worst["rh_max_percent"]["value"]
    assert segment.relative_humidity_low_percent == worst["rh_min_percent"]["value"]
    assert segment.dose_rate_gy_per_s == worst["dose_rate_max_gy_per_s"]["value"]
    assert segment.gap_quantities == ("relative_humidity", "dose_rate_gy_per_s")
    dose_gap = next(g for g in history.gaps if g.quantity == "dose_rate_gy_per_s")
    assert dose_gap.fill_value_low is None


def _last_plus_bound(zone: StorageZoneDoc, **bounds: float | None) -> Any:
    out: Any = copy.deepcopy(zone)
    out["gap_policy"]["kind"] = "last_plus_bound"
    for key, value in bounds.items():
        out["gap_policy"]["bounds"][key] = (
            None
            if value is None
            else {
                "value": value,
                "unit": "x",
                "basis": "team_default",
                "source_ids": [],
                "to_be_set_by": None,
                "note": "",
            }
        )
    return out


def test_last_plus_bound_policy(zones: dict[str, StorageZoneDoc]) -> None:
    zone = _last_plus_bound(
        zones["station_kit"], temperature_k=2.0, relative_humidity_percent=10.0, dose_rate_gy_per_s=1e-9
    )
    series = [
        _series("stk-t1", "temperature_k", "K", [295.0], [0.0]),
        _series("stk-rh1", "relative_humidity", "percent", [95.0], [0.0]),
        _series("stk-dos1", "dose_rate_gy_per_s", "Gy/s", [2e-9], [HOUR]),
    ]
    history = exposure.build_zone_history(zone, series, T0_S, T0_S + 3 * HOUR)
    last = history.segments[-1]
    assert last.temperature_k == 297.0
    assert (last.relative_humidity_percent, last.relative_humidity_low_percent) == (100.0, 85.0)
    assert last.dose_rate_gy_per_s == pytest.approx(3e-9)
    first = history.segments[0]
    # Before the first dose reading nothing is known: the worst case applies even here.
    assert first.dose_rate_gy_per_s == zone["worst_case"]["dose_rate_max_gy_per_s"]["value"]
    kinds = {(g.quantity, g.fill_kind) for g in history.gaps}
    assert ("dose_rate_gy_per_s", "worst_case") in kinds
    assert ("temperature_k", "last_plus_bound") in kinds
    t_gap = next(g for g in history.gaps if g.quantity == "temperature_k")
    assert (t_gap.fill_value, t_gap.fill_value_low) == (297.0, 293.0)
    rh_low = _series("stk-rh1", "relative_humidity", "percent", [5.0], [0.0])
    history = exposure.build_zone_history(zone, [series[0], rh_low, series[2]], T0_S, T0_S + 3 * HOUR)
    assert history.segments[-1].relative_humidity_low_percent == 0.0
    missing = _last_plus_bound(zones["station_kit"], temperature_k=None)
    with pytest.raises(ConfigError):
        exposure.build_zone_history(missing, series, T0_S, T0_S + 3 * HOUR)


def test_declared_zone_gives_one_declared_segment(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["ground_storage"]
    history = exposure.build_zone_history(zone, [], T0_S, T0_S + 10 * DAY)
    (segment,) = history.segments
    declared = zone["declared_conditions"]
    assert segment.temperature_k == declared["temperature_k"]["mu"]  # type: ignore[index]
    assert segment.relative_humidity_percent == declared["relative_humidity_percent"]["mu"]  # type: ignore[index]
    assert segment.dose_rate_gy_per_s == declared["dose_rate_gy_per_s"]["mu"]  # type: ignore[index]
    assert segment.declared_quantities == ("temperature_k", "relative_humidity", "dose_rate_gy_per_s")
    assert history.gaps == ()
    assert not history.coverage_low
    assert history.series_pins == ()
    assert {c.quantity: c for c in history.coverage}["temperature_k"].declared_s == 10 * DAY


def test_lognormal_declared_prior_takes_its_median(zones: dict[str, StorageZoneDoc]) -> None:
    zone: Any = copy.deepcopy(zones["ground_storage"])
    zone["declared_conditions"]["dose_rate_gy_per_s"] = {
        "family": "lognormal",
        "mu": math.log(3e-11),
        "sigma": 0.3,
    }
    zone["declared_conditions"]["illuminance_lux"] = None
    history = exposure.build_zone_history(zone, [], T0_S, T0_S + DAY)
    assert history.segments[0].dose_rate_gy_per_s == pytest.approx(3e-11, rel=1e-15)
    assert {c.quantity: c for c in history.coverage}["illuminance_lux"].not_applicable_s == DAY


def test_light_zone_measures_illuminance(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["mars_base_pharmacy"]
    end = _at(2 * HOUR)
    series = [
        b.constant_series("m-t", "mrp-t1", "mars_base_pharmacy", "temperature_k", "K", T0, end, HOUR, 296.0),
        b.constant_series(
            "m-rh", "mrp-rh1", "mars_base_pharmacy", "relative_humidity", "percent", T0, end, HOUR, 40.0
        ),
        b.constant_series(
            "m-d", "mrp-dos1", "mars_base_pharmacy", "dose_rate_gy_per_s", "Gy/s", T0, end, HOUR, 1e-9
        ),
        b.constant_series(
            "m-l", "mrp-lux1", "mars_base_pharmacy", "illuminance_lux", "lx", T0, end, HOUR, [300.0, 0.0]
        ),
    ]
    history = exposure.build_zone_history(zone, series, T0_S, T0_S + 2 * HOUR)
    assert [s.illuminance_lux for s in history.segments] == [300.0, 0.0]
    assert all(s.uv_w_per_m2 == 0.0 for s in history.segments)
    coverage = {c.quantity: c for c in history.coverage}
    assert coverage["uv_w_per_m2"].not_applicable_s == 2 * HOUR
    assert coverage["illuminance_lux"].measured_s + coverage["illuminance_lux"].held_s == 2 * HOUR
    no_light = exposure.build_zone_history(zone, series[:3], T0_S, T0_S + 2 * HOUR)
    assert no_light.segments[0].illuminance_lux == zone["worst_case"]["illuminance_max_lux"]["value"]
    assert no_light.coverage_low  # an unlit light zone counts its illuminance gap


def test_zone_history_errors(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["station_kit"]
    with pytest.raises(TimeWindowError):
        exposure.build_zone_history(zone, [], T0_S, T0_S)
    with pytest.raises(TimeWindowError):
        exposure.build_zone_history(zone, [], T0_S, math.inf)
    foreign = dataclasses.replace(_full_station(1)[0], zone_id="transit_kit")
    with pytest.raises(SensorDataError):
        exposure.build_zone_history(zone, [foreign], T0_S, T0_S + HOUR)
    unknown = dataclasses.replace(_full_station(1)[0], sensor_id="trk-t1")
    with pytest.raises(SensorDataError):
        exposure.build_zone_history(zone, [unknown], T0_S, T0_S + HOUR)
    first = _series("stk-t1", "temperature_k", "K", [290.0], [0.0])
    second = dataclasses.replace(first, series_id="s-other")
    with pytest.raises(SensorDataError):
        exposure.build_zone_history(zone, [first, second], T0_S, T0_S + HOUR)
    doubled: Any = copy.deepcopy(zone)
    doubled["sensors"].append({**copy.deepcopy(doubled["sensors"][0]), "sensor_id": "stk-t2"})
    with pytest.raises(ConfigError):
        exposure.build_zone_history(doubled, [], T0_S, T0_S + HOUR)
    blind: Any = copy.deepcopy(zone)
    blind["sensors"] = [s for s in blind["sensors"] if s["quantity"] != "temperature_k"]
    with pytest.raises(ConfigError):
        exposure.build_zone_history(blind, [], T0_S, T0_S + HOUR)


def test_two_series_of_one_sensor_are_joined(zones: dict[str, StorageZoneDoc]) -> None:
    zone = zones["station_kit"]
    early = dataclasses.replace(_series("stk-t1", "temperature_k", "K", [290.0], [0.0]), series_id="s-a")
    late = dataclasses.replace(_series("stk-t1", "temperature_k", "K", [291.0], [HOUR]), series_id="s-b")
    history = exposure.build_zone_history(zone, [late, early, *_full_station(2)[1:]], T0_S, T0_S + 2 * HOUR)
    assert [s.temperature_k for s in history.segments] == [290.0, 291.0]
    assert [p.series_id for p in history.series_pins] == ["s-a", "s-b", "s-dos", "s-rh"]


# =========================================================================================
# declared_segment (spec 11.4 forecast projection)
# =========================================================================================


def test_declared_segment(zones: dict[str, StorageZoneDoc]) -> None:
    segment = exposure.declared_segment(zones["ground_storage"], 0.0, 10.0, "original_bottle")
    history = exposure.build_zone_history(zones["ground_storage"], [], 0.0, 10.0)
    assert segment == dataclasses.replace(history.segments[0], package_id="original_bottle")
    with pytest.raises(ConfigReferenceError):
        exposure.declared_segment(zones["station_kit"], 0.0, 10.0, None)
    with pytest.raises(TimeWindowError):
        exposure.declared_segment(zones["ground_storage"], 10.0, 10.0, None)
    lit: Any = copy.deepcopy(zones["mars_base_pharmacy"])
    lit["declared_conditions"] = copy.deepcopy(zones["ground_storage"]["declared_conditions"])
    lit["declared_conditions"]["illuminance_lux"] = {"family": "fixed", "mu": 120.0, "sigma": 0.0}
    lit["declared_conditions"]["uv_w_per_m2"] = None
    segment = exposure.declared_segment(lit, 0.0, 10.0, None)
    assert segment.illuminance_lux == 120.0
    assert segment.uv_w_per_m2 == 0.0
    assert "uv_w_per_m2" not in segment.declared_quantities


# =========================================================================================
# Custody, packages and lot exposure (spec 3.7)
# =========================================================================================


def _lot(lots: dict[str, LotDoc], **changes: Any) -> Any:
    lot: Any = copy.deepcopy(lots["lot-apap-001"])
    lot.update(changes)
    return lot


def test_custody_windows_clip_at_as_of(lots: dict[str, LotDoc]) -> None:
    lot = lots["lot-apap-001"]
    as_of = iso_to_seconds("2028-01-12T00:00:00Z")
    windows = exposure.custody_windows(lot, as_of)
    assert windows == (
        ("ground_storage", iso_to_seconds("2027-06-24T00:00:00Z"), iso_to_seconds("2028-01-10T00:00:00Z")),
        ("transit_kit", iso_to_seconds("2028-01-10T00:00:00Z"), as_of),
    )
    assert exposure.custody_windows(lot, iso_to_seconds(lot["manufactured_at_utc"])) == ()


@pytest.mark.parametrize(
    "case", ["empty", "late_start", "hole", "open_middle", "reversed", "short", "bad_time"]
)
def test_custody_errors(case: str, lots: dict[str, LotDoc]) -> None:
    custody: Any = copy.deepcopy(lots["lot-apap-001"]["custody"])
    as_of = iso_to_seconds("2028-02-01T00:00:00Z")
    if case == "empty":
        custody = []
    elif case == "late_start":
        custody[0]["from_utc"] = "2027-06-25T00:00:00Z"
    elif case == "hole":
        custody[1]["from_utc"] = "2028-01-11T00:00:00Z"
    elif case == "open_middle":
        custody[1]["to_utc"] = None
    elif case == "reversed":
        custody[1]["to_utc"] = "2028-01-10T00:00:00Z"
    elif case == "short":
        custody[2]["to_utc"] = "2028-01-20T00:00:00Z"
    else:
        custody[1]["to_utc"] = "not a time"
    with pytest.raises(TimeWindowError):
        exposure.custody_windows(_lot({"lot-apap-001": lots["lot-apap-001"]}, custody=custody), as_of)


def test_package_windows(lots: dict[str, LotDoc]) -> None:
    lot = lots["lot-apap-001"]
    as_of = iso_to_seconds("2028-01-12T00:00:00Z")
    assert exposure.package_windows(lot, as_of) == (
        ("original_bottle", iso_to_seconds("2027-06-24T00:00:00Z"), iso_to_seconds("2027-12-11T00:00:00Z")),
        ("repackaged_bag", iso_to_seconds("2027-12-11T00:00:00Z"), as_of),
    )
    early = iso_to_seconds("2027-07-01T00:00:00Z")
    assert [w[0] for w in exposure.package_windows(lot, early)] == ["original_bottle"]
    instant: Any = _lot(
        {"lot-apap-001": lot},
        package_history=[
            {"package_id": "original_blister", "from_utc": "2027-06-24T00:00:00Z"},
            {"package_id": "original_bottle", "from_utc": "2027-06-24T00:00:00Z"},
        ],
    )
    assert [w[0] for w in exposure.package_windows(instant, early)] == ["original_bottle"]
    for history in (
        [],
        [{"package_id": "original_bottle", "from_utc": "2027-06-25T00:00:00Z"}],
        [
            {"package_id": "original_bottle", "from_utc": "2027-06-24T00:00:00Z"},
            {"package_id": "repackaged_bag", "from_utc": "2027-12-11T00:00:00Z"},
            {"package_id": "original_bottle", "from_utc": "2027-12-10T00:00:00Z"},
        ],
    ):
        with pytest.raises(TimeWindowError):
            exposure.package_windows(_lot({"lot-apap-001": lot}, package_history=history), early)


def _transit_series() -> dict[str, list[SensorSeries]]:
    start, end = "2028-01-10T00:00:00Z", "2028-01-15T00:00:00Z"
    return {
        "transit_kit": [
            b.constant_series(
                "t-t", "trk-t1", "transit_kit", "temperature_k", "K", start, end, HOUR, [296.0, 300.0]
            ),
            b.constant_series(
                "t-rh", "trk-rh1", "transit_kit", "relative_humidity", "percent", start, end, HOUR, 35.0
            ),
            b.constant_series(
                "t-d", "trk-dos1", "transit_kit", "dose_rate_gy_per_s", "Gy/s", start, end, HOUR, 5e-9
            ),
        ]
    }


def test_lot_exposure_splits_at_custody_and_package_changes(
    lots: dict[str, LotDoc], zones: dict[str, StorageZoneDoc], packages: dict[str, PackageDoc]
) -> None:
    lot = lots["lot-apap-001"]
    as_of = iso_to_seconds("2028-01-10T06:00:00Z")
    found = exposure.build_lot_exposure(lot, zones, packages, _transit_series(), as_of)
    repack = iso_to_seconds("2027-12-11T00:00:00Z")
    launch = iso_to_seconds("2028-01-10T00:00:00Z")
    assert found.t_start_s == iso_to_seconds(lot["manufactured_at_utc"])
    assert found.t_end_s == as_of
    assert [(s.zone_id, s.package_id) for s in found.segments[:3]] == [
        ("ground_storage", "original_bottle"),
        ("ground_storage", "repackaged_bag"),
        ("transit_kit", "repackaged_bag"),
    ]
    assert found.segments[0].t1_s == repack
    assert found.segments[1].t1_s == launch
    assert [s.temperature_k for s in found.segments[2:]] == [296.0, 300.0] * 3
    assert [h.zone_id for h in found.zone_histories] == ["ground_storage", "transit_kit"]
    assert not found.coverage_low
    assert all(a.t1_s == c.t0_s for a, c in zip(found.segments, found.segments[1:], strict=False))


def test_lot_exposure_edges_and_errors(
    lots: dict[str, LotDoc], zones: dict[str, StorageZoneDoc], packages: dict[str, PackageDoc]
) -> None:
    lot = lots["lot-apap-001"]
    made = iso_to_seconds(lot["manufactured_at_utc"])
    empty = exposure.build_lot_exposure(lot, zones, packages, {}, made)
    assert empty.segments == ()
    assert empty.zone_histories == ()
    assert not empty.coverage_low
    with pytest.raises(TimeWindowError):
        exposure.build_lot_exposure(lot, zones, packages, {}, made - 1.0)
    with pytest.raises(TimeWindowError):
        exposure.build_lot_exposure(lot, zones, packages, {}, math.nan)
    unsensed = exposure.build_lot_exposure(lot, zones, packages, {}, iso_to_seconds("2028-01-11T00:00:00Z"))
    assert unsensed.coverage_low
    with pytest.raises(InputError):
        exposure.build_lot_exposure(lot, zones, {}, {}, made + DAY)
    with pytest.raises(InputError):
        exposure.build_lot_exposure(lot, {}, packages, {}, made + DAY)


# =========================================================================================
# Mean kinetic temperature (spec 3.8)
# =========================================================================================


def test_tv_mkt_2(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-MKT-2")
    inputs = v["inputs"]
    mkt = exposure.mean_kinetic_temperature(
        inputs["temperatures_k"],
        inputs["durations_s"],
        inputs["activation_energy_j_per_mol"],
        inputs["gas_constant_j_per_mol_k"],
    )
    celsius = mkt - ZERO_CELSIUS_K
    assert celsius == pytest.approx(v["expected"]["mkt_c"], abs=v["tolerance"]["abs"])
    assert mkt == pytest.approx(v["our_recomputation"]["foundation_mkt_c"] + ZERO_CELSIUS_K, rel=1e-9)
    assert celsius > v["our_recomputation"]["arithmetic_mean_c"]


def test_tv_mkt_3(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-MKT-3")
    inputs = v["inputs"]
    mkt = exposure.mean_kinetic_temperature(
        inputs["temperatures_k"],
        inputs["durations_s"],
        inputs["activation_energy_j_per_mol"],
        inputs["gas_constant_j_per_mol_k"],
    )
    celsius = mkt - inputs["kelvin_offset"]
    assert celsius == pytest.approx(v["expected"]["mkt_c"], abs=v["tolerance"]["abs"])
    assert celsius == pytest.approx(v["our_recomputation"]["foundation_mkt_c_with_273_1"], abs=1e-9)
    standard = vector("TV-MKT-2")["our_recomputation"]["foundation_mkt_c"]
    assert abs(celsius - standard) < v["expected"]["max_difference_from_273_15_k"]


def test_tv_mkt_7(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-MKT-7")
    inputs = v["inputs"]
    mkt = exposure.mean_kinetic_temperature(
        inputs["temperatures_k"],
        inputs["durations_s"],
        inputs["activation_energy_j_per_mol"],
        inputs["gas_constant_j_per_mol_k"],
    )
    assert mkt - ZERO_CELSIUS_K == pytest.approx(v["expected"]["mkt_c"], abs=v["tolerance"]["abs"])
    assert mkt == pytest.approx(v["our_recomputation"]["foundation_mkt_c"] + ZERO_CELSIUS_K, rel=1e-9)


def test_tv_mkt_8(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-MKT-8")
    inputs = v["inputs"]
    results = []
    for ea, key in zip(inputs["activation_energies_j_per_mol"], ("83144", "120000"), strict=True):
        mkt = exposure.mean_kinetic_temperature(
            inputs["temperatures_k"], inputs["durations_s"], ea, inputs["gas_constant_j_per_mol_k"]
        )
        assert mkt - ZERO_CELSIUS_K == pytest.approx(
            v["expected"][f"mkt_c_at_{key}"], abs=v["tolerance"]["abs"]
        )
        assert mkt == pytest.approx(
            v["our_recomputation"][f"foundation_mkt_c_at_{key}"] + ZERO_CELSIUS_K, rel=1e-9
        )
        results.append(mkt)
    assert results[1] > results[0]


def test_mkt_errors_and_edge_cases() -> None:
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([300.0], [1.0, 2.0], 83144.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([], [], 83144.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([300.0], [1.0], 0.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([300.0], [1.0], 83144.0, 0.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([0.0], [1.0], 83144.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([300.0], [-1.0], 83144.0)
    with pytest.raises(InputError):
        exposure.mean_kinetic_temperature([300.0, 310.0], [0.0, 0.0], 83144.0)
    # Zero-duration entries carry no weight; cold series do not underflow.
    assert exposure.mean_kinetic_temperature([300.0, 400.0], [5.0, 0.0], 83144.0) == pytest.approx(
        300.0, rel=1e-14
    )
    cold = exposure.mean_kinetic_temperature([20.0, 21.0], [1.0, 1.0], 5e5)  # exp(-ratio/T) underflows alone
    assert 20.5 < cold < 21.0


# =========================================================================================
# summarize_exposure (spec 3.8)
# =========================================================================================


def test_summary_over_transit(
    lots: dict[str, LotDoc],
    zones: dict[str, StorageZoneDoc],
    packages: dict[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> None:
    lot = lots["lot-apap-001"]
    as_of = iso_to_seconds("2028-01-11T00:00:00Z")
    found = exposure.build_lot_exposure(lot, zones, packages, _transit_series(), as_of)
    summary = exposure.summarize_exposure(found, zones["transit_kit"], settings)
    segments = found.segments
    durations = [s.t1_s - s.t0_s for s in segments]
    activation = settings["mean_kinetic_temperature"]["activation_energy_j_per_mol"]["value"]
    assert summary.mkt_k == exposure.mean_kinetic_temperature(
        [s.temperature_k for s in segments], durations, activation
    )
    assert summary.duration_s == as_of - found.t_start_s
    windows = {w.window_id: w for w in summary.mkt_windows}
    day = windows["cold_chain_24_hours"]
    assert day.mkt_k == pytest.approx(
        exposure.mean_kinetic_temperature([296.0, 300.0] * 12, [HOUR] * 24, activation), rel=1e-14
    )
    assert summary.min_temperature_k == 296.0
    assert summary.max_temperature_k == 300.0
    above = {t.threshold_k: t.seconds for t in summary.time_above}
    assert above[298.15] == pytest.approx(12 * HOUR)
    assert above[313.15] == 0.0
    declared_dose = zones["ground_storage"]["declared_conditions"]["dose_rate_gy_per_s"]["mu"]  # type: ignore[index]
    ground_s = iso_to_seconds("2028-01-10T00:00:00Z") - found.t_start_s
    assert summary.cumulative_absorbed_dose_gy == pytest.approx(
        declared_dose * ground_s + 5e-9 * DAY, rel=1e-12
    )
    assert summary.visible_dose_lux_h == 0.0
    assert summary.uv_dose_w_h_per_m2 == 0.0
    coverage = {(c.zone_id, c.quantity): c for c in summary.coverage}
    transit = coverage[("transit_kit", "temperature_k")]
    assert transit.measured_s + transit.held_s == DAY
    assert transit.coverage_fraction == 1.0
    assert coverage[("ground_storage", "temperature_k")].declared_s == ground_s
    assert coverage[("ground_storage", "illuminance_lux")].coverage_fraction == 1.0
    assert summary.longest_gap_s == 0.0


def test_summary_of_a_short_and_an_empty_history(
    lots: dict[str, LotDoc],
    zones: dict[str, StorageZoneDoc],
    packages: dict[str, PackageDoc],
    settings: EngineSettingsDoc,
) -> None:
    lot = lots["lot-apap-001"]
    made = iso_to_seconds(lot["manufactured_at_utc"])
    short = exposure.build_lot_exposure(lot, zones, packages, {}, made + 2 * DAY)
    summary = exposure.summarize_exposure(short, zones["ground_storage"], settings)
    windows = {w.window_id: w.mkt_k for w in summary.mkt_windows}
    assert windows["crt_30_days"] is None
    assert windows["cold_chain_24_hours"] == pytest.approx(298.15)
    empty = exposure.build_lot_exposure(lot, zones, packages, {}, made)
    summary = exposure.summarize_exposure(empty, zones["ground_storage"], settings)
    assert summary.mkt_k is None
    assert summary.mean_temperature_k is None
    assert summary.min_temperature_k is None
    assert summary.coverage == ()
    assert summary.longest_gap_s == 0.0
    assert all(w.mkt_k is None for w in summary.mkt_windows)
