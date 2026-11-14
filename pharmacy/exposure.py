"""Exposure stage: raw sensor series to piecewise-constant, gap-bounded storage histories.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 3.

Inputs are raw ``SensorSeries`` (any accepted unit), the zone configuration and the lot's
custody. Outputs are ``ZoneHistory`` (per zone), ``LotExposure`` (per lot, package set per
segment) and ``ExposureSummary`` (display only: MKT, extremes, time above thresholds,
cumulative doses, coverage). Potency never uses MKT.
"""

import dataclasses
import itertools
import math
from collections.abc import Mapping, Sequence
from typing import Final

from pharmacy.config import canonical_sha256
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_HOUR, ZERO_CELSIUS_K
from pharmacy.errors import (
    ConfigError,
    ConfigReferenceError,
    InputError,
    SensorDataError,
    TimeWindowError,
    UnitError,
)
from pharmacy.types import (
    CANONICAL_UNITS,
    QUANTITIES,
    CoverageState,
    EngineSettingsDoc,
    ExposureSummary,
    GapFillKind,
    GapRecord,
    JSONValue,
    LotDoc,
    LotExposure,
    PackageDoc,
    Prior,
    Quantity,
    QuantityCoverage,
    Segment,
    SensorPoint,
    SensorSeries,
    SensorSpecDoc,
    SeriesPin,
    SourcedValue,
    StorageZoneDoc,
    TimeAbove,
    WindowMkt,
    ZoneHistory,
    iso_to_seconds,
    seconds_to_iso,
    to_json,
)

_TIME_AGREEMENT_S: Final[float] = 1e-3
"""ISO and float timestamps must agree within 1 ms (spec 1.2)."""

_LIGHT_QUANTITIES: Final[frozenset[str]] = frozenset({"illuminance_lux", "uv_w_per_m2"})
_OPTIONAL_QUANTITIES: Final[frozenset[str]] = frozenset(
    {"dose_rate_gy_per_s", "illuminance_lux", "uv_w_per_m2"}
)


# =========================================================================================
# Normalization (spec 3.3)
# =========================================================================================


def _check_time(point: SensorPoint, label: str) -> None:
    try:
        parsed = iso_to_seconds(point.t_utc)
    except ValueError as error:
        raise SensorDataError(f"{label}: bad timestamp {point.t_utc!r}: {error}") from error
    if not math.isfinite(point.t_s) or abs(parsed - point.t_s) > _TIME_AGREEMENT_S:
        raise SensorDataError(
            f"{label}: t_utc {point.t_utc!r} and t_s {point.t_s!r} differ by more than 1 ms"
        )


def normalize_series(series: SensorSeries, sensor: SensorSpecDoc) -> SensorSeries:
    """Validate a raw series and return it in canonical units with the sensor's calibration
    offset applied (spec 3.3).

    Checks: quantity matches ``sensor``; unit in ``CANONICAL_UNITS[quantity]``; timestamps
    strictly increasing; ``t_utc`` and ``t_s`` agree within 1 ms; values finite and in the
    physical domain (T > 0 K after conversion, 0 <= RH <= 100, dose rate, illuminance and
    UV >= 0). Conversion: degC to K by adding ``ZERO_CELSIUS_K``. Calibration:
    value_out = value_in + calibration_offset (in canonical units).

    Raises ``SensorDataError`` or ``UnitError``. An empty series is valid (all gap).
    """
    label = f"series {series.series_id!r}"
    if series.quantity != sensor["quantity"]:
        raise SensorDataError(
            f"{label} measures {series.quantity!r} but sensor {sensor['sensor_id']!r} measures "
            f"{sensor['quantity']!r}"
        )
    if series.sensor_id != sensor["sensor_id"]:
        raise SensorDataError(f"{label} names sensor {series.sensor_id!r}, not {sensor['sensor_id']!r}")
    units = CANONICAL_UNITS[series.quantity]
    if series.unit not in units:
        raise UnitError(f"{label}: unit {series.unit!r} is not one of {units}")
    offset = float(sensor["calibration_offset"]["value"])
    points: list[SensorPoint] = []
    previous: float | None = None
    for point in series.points:
        _check_time(point, label)
        if previous is not None and not point.t_s > previous:
            raise SensorDataError(f"{label}: timestamps must strictly increase at {point.t_utc!r}")
        previous = point.t_s
        if not math.isfinite(point.value):
            raise SensorDataError(f"{label}: non-finite value at {point.t_utc!r}")
        value = point.value + ZERO_CELSIUS_K if series.unit == "degC" else point.value
        value = value + offset
        if series.quantity == "temperature_k":
            valid = value > 0.0
        elif series.quantity == "relative_humidity":
            valid = 0.0 <= value <= 100.0
        else:
            valid = value >= 0.0
        if not valid:
            raise SensorDataError(f"{label}: value {value!r} outside the physical domain at {point.t_utc!r}")
        points.append(SensorPoint(t_utc=point.t_utc, t_s=point.t_s, value=value))
    return dataclasses.replace(series, unit=units[0], points=tuple(points))


# =========================================================================================
# Per-quantity timelines (spec 3.4, 3.5)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Piece:
    """One interval of one quantity: value (high), low value (RH and T gaps), state."""

    t0: float
    t1: float
    value: float
    low: float
    state: CoverageState


def _declared_prior(zone: StorageZoneDoc, quantity: Quantity) -> Prior | None:
    declared = zone["declared_conditions"]
    if quantity == "temperature_k":
        return declared["temperature_k"]
    if quantity == "relative_humidity":
        return declared["relative_humidity_percent"]
    if quantity == "dose_rate_gy_per_s":
        return declared["dose_rate_gy_per_s"]
    if quantity == "illuminance_lux":
        return declared["illuminance_lux"]
    return declared["uv_w_per_m2"]


def _gap_bound(zone: StorageZoneDoc, quantity: Quantity) -> SourcedValue | None:
    bounds = zone["gap_policy"]["bounds"]
    if quantity == "temperature_k":
        return bounds["temperature_k"]
    if quantity == "relative_humidity":
        return bounds["relative_humidity_percent"]
    if quantity == "dose_rate_gy_per_s":
        return bounds["dose_rate_gy_per_s"]
    if quantity == "illuminance_lux":
        return bounds["illuminance_lux"]
    return bounds["uv_w_per_m2"]


def _declared_value(prior: Prior) -> float:
    if prior["family"] == "lognormal":
        # mu is a log; the declared value is the median exp(mu) (docs/requests/B1.md R-10).
        return math.exp(prior["mu"])
    return float(prior["mu"])


def _worst_case(zone: StorageZoneDoc, quantity: Quantity) -> tuple[float, float]:
    worst = zone["worst_case"]
    if quantity == "temperature_k":
        return float(worst["temperature_max_k"]["value"]), float(worst["temperature_min_k"]["value"])
    if quantity == "relative_humidity":
        return float(worst["rh_max_percent"]["value"]), float(worst["rh_min_percent"]["value"])
    if quantity == "dose_rate_gy_per_s":
        value = float(worst["dose_rate_max_gy_per_s"]["value"])
        return value, value
    if not zone["light_exposure"]:
        return 0.0, 0.0
    value = float(
        worst["illuminance_max_lux"]["value"]
        if quantity == "illuminance_lux"
        else worst["uv_max_w_per_m2"]["value"]
    )
    return value, value


def _gap_fill(
    zone: StorageZoneDoc, quantity: Quantity, last: float | None
) -> tuple[float, float, GapFillKind]:
    """Fill value (high, low) and the policy actually applied (spec 3.5)."""
    policy = zone["gap_policy"]
    if policy["kind"] == "worst_case" or last is None:
        high, low = _worst_case(zone, quantity)
        return high, low, "worst_case"
    bound_doc = _gap_bound(zone, quantity)
    if bound_doc is None:
        raise ConfigError(f"zone {zone['zone_id']!r}: last_plus_bound needs a bound for {quantity!r}")
    bound = float(bound_doc["value"])
    if quantity == "temperature_k":
        return last + bound, last - bound, "last_plus_bound"
    if quantity == "relative_humidity":
        return min(100.0, last + bound), max(0.0, last - bound), "last_plus_bound"
    return last + bound, last + bound, "last_plus_bound"


def _sensed_pieces(
    zone: StorageZoneDoc,
    quantity: Quantity,
    points: Sequence[SensorPoint],
    hold_s: float,
    t_start: float,
    t_end: float,
) -> tuple[list[_Piece], list[GapRecord]]:
    """Pieces of a sensed quantity over [t_start, t_end): measured, held and gap (spec 3.4)."""
    prior = [p for p in points if p.t_s < t_start]
    inside = [p for p in points if t_start <= p.t_s < t_end]
    readings = ([prior[-1]] if prior else []) + inside
    raw: list[tuple[float, float, float, float, CoverageState, GapFillKind | None]] = []

    def gap(t0: float, t1: float, last: float | None) -> None:
        high, low, kind = _gap_fill(zone, quantity, last)
        raw.append((t0, t1, high, low, "gap_filled", kind))

    if not readings:
        gap(t_start, t_end, None)
    else:
        if readings[0].t_s > t_start:
            gap(t_start, readings[0].t_s, None)
        for index, reading in enumerate(readings):
            t_next = readings[index + 1].t_s if index + 1 < len(readings) else None
            if t_next is not None and t_next - reading.t_s <= hold_s:
                raw.append((reading.t_s, t_next, reading.value, reading.value, "measured", None))
                continue
            stop = t_end if t_next is None else t_next
            hold_end = reading.t_s + hold_s
            if hold_end >= stop:
                raw.append((reading.t_s, stop, reading.value, reading.value, "held", None))
            else:
                raw.append((reading.t_s, hold_end, reading.value, reading.value, "held", None))
                gap(hold_end, stop, reading.value)
    pieces: list[_Piece] = []
    gaps: list[GapRecord] = []
    for t0, t1, high, low, state, kind in raw:
        lo = max(t0, t_start)
        hi = min(t1, t_end)
        if not hi > lo:
            continue
        pieces.append(_Piece(lo, hi, high, low, state))
        if kind is not None:
            gaps.append(
                GapRecord(
                    zone_id=zone["zone_id"],
                    quantity=quantity,
                    t0_s=lo,
                    t1_s=hi,
                    fill_kind=kind,
                    fill_value=high,
                    fill_value_low=low if quantity in ("temperature_k", "relative_humidity") else None,
                )
            )
    return pieces, gaps


def _sensor_for(zone: StorageZoneDoc, quantity: Quantity) -> SensorSpecDoc | None:
    found = [sensor for sensor in zone["sensors"] if sensor["quantity"] == quantity]
    if len(found) > 1:
        raise ConfigError(f"zone {zone['zone_id']!r} lists more than one sensor for {quantity!r}")
    return found[0] if found else None


def _series_record(series: SensorSeries, t_end: float) -> JSONValue:
    return {
        "series_id": series.series_id,
        "sensor_id": series.sensor_id,
        "zone_id": series.zone_id,
        "quantity": series.quantity,
        "unit": series.unit,
        "points": [to_json(p) for p in series.points if p.t_s <= t_end],
    }


def series_pin(series: SensorSeries, as_of_s: float) -> SeriesPin:
    """Sensor series pin of spec 1.4: SHA-256 of the canonical JSON series record with the
    points at or before ``as_of_s``, exactly as received."""
    return SeriesPin(series_id=series.series_id, sha256=canonical_sha256(_series_record(series, as_of_s)))


def _coverage(zone_id: str, quantity: Quantity, pieces: Sequence[_Piece]) -> QuantityCoverage:
    seconds: dict[str, list[float]] = {
        "measured": [],
        "held": [],
        "gap_filled": [],
        "declared": [],
        "not_applicable": [],
    }
    longest = 0.0
    for piece in pieces:
        duration = piece.t1 - piece.t0
        seconds[piece.state].append(duration)
        if piece.state == "gap_filled":
            longest = max(longest, duration)
    totals = {state: math.fsum(values) for state, values in seconds.items()}
    total = math.fsum(totals.values())
    denominator = total - totals["not_applicable"]
    covered = totals["measured"] + totals["held"] + totals["declared"]
    fraction = 1.0 if denominator <= 0.0 else min(1.0, max(0.0, covered / denominator))
    return QuantityCoverage(
        zone_id=zone_id,
        quantity=quantity,
        measured_s=totals["measured"],
        held_s=totals["held"],
        gap_s=totals["gap_filled"],
        declared_s=totals["declared"],
        not_applicable_s=totals["not_applicable"],
        longest_gap_s=longest,
        coverage_fraction=fraction,
    )


def _coverage_low(zone: StorageZoneDoc, coverage: Sequence[QuantityCoverage]) -> bool:
    gap_limit = float(zone["gap_limit_s"]["value"])
    minimum = float(zone["min_coverage_fraction"]["value"])
    for entry in coverage:
        if entry.quantity in _LIGHT_QUANTITIES and not zone["light_exposure"]:
            continue
        if entry.longest_gap_s > gap_limit or entry.coverage_fraction < minimum:
            return True
    return False


def build_zone_history(
    zone: StorageZoneDoc,
    series: Sequence[SensorSeries],
    t_start_s: float,
    t_end_s: float,
) -> ZoneHistory:
    """Build the aligned history of one zone over [t_start_s, t_end_s) (spec 3.4 to 3.6).

    Sensed zones: each quantity uses the series of the zone sensor that measures it
    (normalized with ``normalize_series``). A reading at t_j holds its value on
    [t_j, t_j + min(t_{j+1} - t_j, max_hold_s)) (zero-order hold). Time not covered by a
    hold is a gap, filled by the zone's gap policy (spec 3.5). Quantities the zone does not
    sense take declared conditions (``mu`` of the declared prior) or 0 for dose rate,
    illuminance and UV when nothing is declared (``not_applicable``). Segment boundaries are
    the union of all quantities' change points; adjacent segments with identical values and
    flags are merged. Illuminance and UV are forced to 0 when ``light_exposure`` is false.

    Declared zones: one segment per declared-condition change (normally one segment).

    Raises ``TimeWindowError`` if t_end_s <= t_start_s. Never fails on gaps.
    """
    if not (math.isfinite(t_start_s) and math.isfinite(t_end_s)) or not t_end_s > t_start_s:
        raise TimeWindowError(f"zone window [{t_start_s!r}, {t_end_s!r}) is empty or not finite")
    zone_id = zone["zone_id"]
    by_sensor: dict[str, list[SensorSeries]] = {}
    for item in series:
        if item.zone_id != zone_id:
            raise SensorDataError(
                f"series {item.series_id!r} belongs to zone {item.zone_id!r}, not {zone_id!r}"
            )
        if not any(sensor["sensor_id"] == item.sensor_id for sensor in zone["sensors"]):
            raise SensorDataError(
                f"series {item.series_id!r} names sensor {item.sensor_id!r}, unknown in {zone_id!r}"
            )
        by_sensor.setdefault(item.sensor_id, []).append(item)
    timelines: dict[Quantity, list[_Piece]] = {}
    gaps: list[GapRecord] = []
    pins: list[SeriesPin] = []
    for quantity in QUANTITIES:
        if quantity in _LIGHT_QUANTITIES and not zone["light_exposure"]:
            timelines[quantity] = [_Piece(t_start_s, t_end_s, 0.0, 0.0, "not_applicable")]
            continue
        sensor = _sensor_for(zone, quantity)
        if sensor is not None:
            normalized = [
                normalize_series(item, sensor)
                for item in sorted(by_sensor.get(sensor["sensor_id"], []), key=lambda s: s.series_id)
            ]
            points = sorted((p for item in normalized for p in item.points), key=lambda p: p.t_s)
            for first, second in itertools.pairwise(points):
                if not second.t_s > first.t_s:
                    raise SensorDataError(
                        f"sensor {sensor['sensor_id']!r} has duplicate timestamp {second.t_utc!r}"
                    )
            pins.extend(series_pin(item, t_end_s) for item in by_sensor.get(sensor["sensor_id"], []))
            pieces, quantity_gaps = _sensed_pieces(
                zone, quantity, points, float(sensor["max_hold_s"]["value"]), t_start_s, t_end_s
            )
            timelines[quantity] = pieces
            gaps.extend(quantity_gaps)
            continue
        prior = _declared_prior(zone, quantity)
        if prior is not None:
            value = _declared_value(prior)
            timelines[quantity] = [_Piece(t_start_s, t_end_s, value, value, "declared")]
        elif quantity in _OPTIONAL_QUANTITIES:
            timelines[quantity] = [_Piece(t_start_s, t_end_s, 0.0, 0.0, "not_applicable")]
        else:
            raise ConfigError(f"zone {zone_id!r} neither senses nor declares {quantity!r}")
    segments = _merge_timelines(zone_id, timelines, t_start_s, t_end_s)
    coverage = tuple(_coverage(zone_id, quantity, timelines[quantity]) for quantity in QUANTITIES)
    return ZoneHistory(
        zone_id=zone_id,
        t_start_s=t_start_s,
        t_end_s=t_end_s,
        segments=segments,
        gaps=tuple(sorted(gaps, key=lambda g: (g.t0_s, QUANTITIES.index(g.quantity)))),
        coverage=coverage,
        series_pins=tuple(sorted(pins, key=lambda pin: pin.series_id)),
        coverage_low=_coverage_low(zone, coverage),
    )


def _merge_timelines(
    zone_id: str, timelines: Mapping[Quantity, Sequence[_Piece]], t_start: float, t_end: float
) -> tuple[Segment, ...]:
    boundaries = sorted({t_start, t_end} | {piece.t0 for pieces in timelines.values() for piece in pieces})
    boundaries = [b for b in boundaries if t_start <= b <= t_end]
    cursor = dict.fromkeys(QUANTITIES, 0)
    out: list[Segment] = []
    for t0, t1 in itertools.pairwise(boundaries):
        current: dict[Quantity, _Piece] = {}
        for quantity in QUANTITIES:
            pieces = timelines[quantity]
            index = cursor[quantity]
            while pieces[index].t1 <= t0:
                index += 1
            cursor[quantity] = index
            current[quantity] = pieces[index]
        segment = Segment(
            t0_s=t0,
            t1_s=t1,
            zone_id=zone_id,
            package_id=None,
            temperature_k=current["temperature_k"].value,
            relative_humidity_percent=current["relative_humidity"].value,
            relative_humidity_low_percent=current["relative_humidity"].low,
            dose_rate_gy_per_s=current["dose_rate_gy_per_s"].value,
            illuminance_lux=current["illuminance_lux"].value,
            uv_w_per_m2=current["uv_w_per_m2"].value,
            gap_quantities=tuple(q for q in QUANTITIES if current[q].state == "gap_filled"),
            declared_quantities=tuple(q for q in QUANTITIES if current[q].state == "declared"),
        )
        if out and _same_conditions(out[-1], segment):
            out[-1] = dataclasses.replace(out[-1], t1_s=t1)
        else:
            out.append(segment)
    return tuple(out)


def _same_conditions(a: Segment, b: Segment) -> bool:
    return (
        a.zone_id == b.zone_id
        and a.package_id == b.package_id
        and a.temperature_k == b.temperature_k
        and a.relative_humidity_percent == b.relative_humidity_percent
        and a.relative_humidity_low_percent == b.relative_humidity_low_percent
        and a.dose_rate_gy_per_s == b.dose_rate_gy_per_s
        and a.illuminance_lux == b.illuminance_lux
        and a.uv_w_per_m2 == b.uv_w_per_m2
        and a.gap_quantities == b.gap_quantities
        and a.declared_quantities == b.declared_quantities
    )


# =========================================================================================
# Lot exposure (spec 3.7)
# =========================================================================================


def _seconds(t_utc: str, label: str) -> float:
    try:
        return iso_to_seconds(t_utc)
    except ValueError as error:
        raise TimeWindowError(f"{label}: bad timestamp {t_utc!r}: {error}") from error


def declared_segment(zone: StorageZoneDoc, t0_s: float, t1_s: float, package_id: str | None) -> Segment:
    """One segment [t0_s, t1_s) at the zone's declared conditions, for the forecast's
    ``declared_constant`` projection (spec 11.4). Temperature and RH come from the declared
    priors (their medians, as in build_zone_history) and raise ``ConfigReferenceError`` when
    either is not declared. Dose rate, illuminance and UV take the declared value, or 0 when
    nothing is declared (``not_applicable``, spec 3.4); light is 0 without light exposure."""
    if not (math.isfinite(t0_s) and math.isfinite(t1_s)) or not t1_s > t0_s:
        raise TimeWindowError(f"declared segment [{t0_s!r}, {t1_s!r}) is empty or not finite")
    zone_id = zone["zone_id"]
    values: dict[Quantity, float] = {}
    declared: list[Quantity] = []
    missing: list[str] = []
    for quantity in QUANTITIES:
        if quantity in _LIGHT_QUANTITIES and not zone["light_exposure"]:
            values[quantity] = 0.0
            continue
        prior = _declared_prior(zone, quantity)
        if prior is not None:
            values[quantity] = _declared_value(prior)
            declared.append(quantity)
        elif quantity in _OPTIONAL_QUANTITIES:
            values[quantity] = 0.0
        else:
            missing.append(f"/zones/{zone_id}/declared_conditions: {quantity} is not declared")
    if missing:
        raise ConfigReferenceError("storage-zones", missing)
    return Segment(
        t0_s=t0_s,
        t1_s=t1_s,
        zone_id=zone_id,
        package_id=package_id,
        temperature_k=values["temperature_k"],
        relative_humidity_percent=values["relative_humidity"],
        relative_humidity_low_percent=values["relative_humidity"],
        dose_rate_gy_per_s=values["dose_rate_gy_per_s"],
        illuminance_lux=values["illuminance_lux"],
        uv_w_per_m2=values["uv_w_per_m2"],
        gap_quantities=(),
        declared_quantities=tuple(declared),
    )


def custody_windows(lot: LotDoc, as_of_s: float) -> tuple[tuple[str, float, float], ...]:
    """Validated custody intervals clipped to [manufacture, as_of) as (zone_id, t0, t1)
    (spec 3.7). Raises ``TimeWindowError`` on overlaps, holes, a bad start or an open
    interval that is not last."""
    manufacture = _seconds(lot["manufactured_at_utc"], f"lot {lot['lot_id']!r}")
    custody = lot["custody"]
    if not custody:
        raise TimeWindowError(f"lot {lot['lot_id']!r} has no custody")
    windows: list[tuple[str, float, float]] = []
    expected = manufacture
    for index, interval in enumerate(custody):
        start = _seconds(interval["from_utc"], f"lot {lot['lot_id']!r} custody")
        if start != expected:
            what = "start at manufacture" if index == 0 else "start where the previous interval ends"
            raise TimeWindowError(f"lot {lot['lot_id']!r}: custody interval {index} must {what}")
        if interval["to_utc"] is None:
            if index != len(custody) - 1:
                raise TimeWindowError(f"lot {lot['lot_id']!r}: only the last custody interval may be open")
            end = math.inf
        else:
            end = _seconds(interval["to_utc"], f"lot {lot['lot_id']!r} custody")
            if not end > start:
                raise TimeWindowError(f"lot {lot['lot_id']!r}: custody interval {index} is empty or reversed")
        windows.append((interval["zone_id"], start, end))
        expected = end
    if expected < as_of_s:
        raise TimeWindowError(f"lot {lot['lot_id']!r}: custody ends before as_of")
    clipped = [(zone_id, t0, min(t1, as_of_s)) for zone_id, t0, t1 in windows if t0 < as_of_s]
    return tuple(clipped)


def package_windows(lot: LotDoc, as_of_s: float) -> tuple[tuple[str, float, float], ...]:
    """Package intervals clipped to [manufacture, as_of) as (package_id, t0, t1)."""
    manufacture = _seconds(lot["manufactured_at_utc"], f"lot {lot['lot_id']!r}")
    history = lot["package_history"]
    if not history:
        raise TimeWindowError(f"lot {lot['lot_id']!r} has no package history")
    starts = [_seconds(entry["from_utc"], f"lot {lot['lot_id']!r} package history") for entry in history]
    if starts[0] != manufacture:
        raise TimeWindowError(f"lot {lot['lot_id']!r}: the package history must start at manufacture")
    for first, second in itertools.pairwise(starts):
        if second < first:
            raise TimeWindowError(f"lot {lot['lot_id']!r}: the package history must be non-decreasing")
    out: list[tuple[str, float, float]] = []
    for index, entry in enumerate(history):
        t0 = starts[index]
        t1 = starts[index + 1] if index + 1 < len(history) else math.inf
        t1 = min(t1, as_of_s)
        if t1 > t0:
            out.append((entry["package_id"], t0, t1))
    return tuple(out)


def build_lot_exposure(
    lot: LotDoc,
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    series_by_zone: Mapping[str, Sequence[SensorSeries]],
    as_of_s: float,
) -> LotExposure:
    """Concatenate zone histories along the lot's custody from manufacture to ``as_of_s``
    and set the package of every segment from ``package_history`` (spec 3.7). Segments are
    split at custody and package changes.

    Raises ``TimeWindowError`` if as_of precedes manufacture, custody intervals overlap,
    leave a hole, do not start at manufacture, or the package history does not start at
    manufacture. An as_of equal to manufacture returns an exposure with no segments.
    """
    if not math.isfinite(as_of_s):
        raise TimeWindowError(f"as_of must be finite, got {as_of_s!r}")
    manufacture = _seconds(lot["manufactured_at_utc"], f"lot {lot['lot_id']!r}")
    if as_of_s < manufacture:
        raise TimeWindowError(f"as_of precedes the manufacture of lot {lot['lot_id']!r}")
    custody = custody_windows(lot, as_of_s)
    package_list = package_windows(lot, as_of_s)
    for package_id, _, _ in package_list:
        if package_id not in packages:
            raise InputError(f"lot {lot['lot_id']!r} names unknown package {package_id!r}")
    histories: list[ZoneHistory] = []
    segments: list[Segment] = []
    for zone_id, t0, t1 in custody:
        if zone_id not in zones:
            raise InputError(f"lot {lot['lot_id']!r} names unknown zone {zone_id!r}")
        history = build_zone_history(zones[zone_id], series_by_zone.get(zone_id, ()), t0, t1)
        histories.append(history)
        for segment in history.segments:
            for package_id, p0, p1 in package_list:
                lo = max(segment.t0_s, p0)
                hi = min(segment.t1_s, p1)
                if hi > lo:
                    segments.append(dataclasses.replace(segment, t0_s=lo, t1_s=hi, package_id=package_id))
    return LotExposure(
        lot_id=lot["lot_id"],
        t_start_s=manufacture,
        t_end_s=as_of_s,
        segments=tuple(segments),
        zone_histories=tuple(histories),
        coverage_low=any(history.coverage_low for history in histories),
    )


# =========================================================================================
# MKT and summaries (spec 3.8)
# =========================================================================================


def mean_kinetic_temperature(
    temperatures_k: Sequence[float],
    durations_s: Sequence[float],
    activation_energy_j_per_mol: float,
    gas_constant_j_per_mol_k: float = GAS_CONSTANT_J_PER_MOL_K,
) -> float:
    """Time-weighted mean kinetic temperature in K (USP <1079.2> form, spec 3.8):

        T_mk = (dH/R) / ( -ln( sum_i tau_i exp(-dH/(R T_i)) / sum_i tau_i ) )

    evaluated with a log-sum-exp so it never underflows. Equal durations reduce to the
    USP formula. Raises ``InputError`` on empty input, mismatched lengths, a non-positive
    temperature or a negative duration, or zero total duration. Display only.
    """
    if len(temperatures_k) != len(durations_s):
        raise InputError("temperatures and durations must have the same length")
    if not temperatures_k:
        raise InputError("MKT needs at least one temperature")
    if not (math.isfinite(activation_energy_j_per_mol) and activation_energy_j_per_mol > 0.0):
        raise InputError(f"MKT activation energy must be positive, got {activation_energy_j_per_mol!r}")
    if not (math.isfinite(gas_constant_j_per_mol_k) and gas_constant_j_per_mol_k > 0.0):
        raise InputError("the gas constant must be positive")
    ratio = activation_energy_j_per_mol / gas_constant_j_per_mol_k
    exponents: list[float] = []
    weights: list[float] = []
    for temperature, duration in zip(temperatures_k, durations_s, strict=True):
        if not (math.isfinite(temperature) and temperature > 0.0):
            raise InputError(f"MKT temperatures must be positive and finite, got {temperature!r}")
        if not (math.isfinite(duration) and duration >= 0.0):
            raise InputError(f"MKT durations must be finite and non-negative, got {duration!r}")
        if duration > 0.0:
            exponents.append(-ratio / temperature + math.log(duration))
            weights.append(duration)
    if not weights:
        raise InputError("MKT needs a positive total duration")
    peak = max(exponents)
    log_sum = peak + math.log(math.fsum(math.exp(e - peak) for e in exponents))
    return ratio / (-(log_sum - math.log(math.fsum(weights))))


def _clipped(segments: Sequence[Segment], t0: float, t1: float) -> list[tuple[Segment, float]]:
    out: list[tuple[Segment, float]] = []
    for segment in segments:
        duration = min(segment.t1_s, t1) - max(segment.t0_s, t0)
        if duration > 0.0:
            out.append((segment, duration))
    return out


def summarize_exposure(
    exposure: LotExposure, zone: StorageZoneDoc, settings: EngineSettingsDoc
) -> ExposureSummary:
    """Pharmacist-facing summary of a lot exposure (spec 3.8): MKT over the whole history
    and over each configured window ending at as_of (None when the window is longer than
    the history), mean, min and max temperature, time above each threshold of ``zone``,
    cumulative absorbed dose (Gy), visible (lux h) and UV (W h/m2) doses at the zone (before
    package transmission), coverage per quantity and the longest gap. Gap-filled segments
    are included with their filled values."""
    mkt_settings = settings["mean_kinetic_temperature"]
    activation = float(mkt_settings["activation_energy_j_per_mol"]["value"])
    segments = exposure.segments
    durations = [s.t1_s - s.t0_s for s in segments]
    temperatures = [s.temperature_k for s in segments]
    history = exposure.t_end_s - exposure.t_start_s
    total = math.fsum(durations)
    mkt = mean_kinetic_temperature(temperatures, durations, activation) if total > 0.0 else None
    windows: list[WindowMkt] = []
    for window in mkt_settings["windows"]:
        length = float(window["duration_s"]["value"])
        value: float | None = None
        if history >= length > 0.0:
            parts = _clipped(segments, exposure.t_end_s - length, exposure.t_end_s)
            if parts:
                value = mean_kinetic_temperature(
                    [s.temperature_k for s, _ in parts], [d for _, d in parts], activation
                )
        windows.append(WindowMkt(window_id=window["window_id"], duration_s=length, mkt_k=value))
    mean = (
        math.fsum(t * d for t, d in zip(temperatures, durations, strict=True)) / total
        if total > 0.0
        else None
    )
    time_above = tuple(
        TimeAbove(
            threshold_k=float(threshold["value"]),
            seconds=math.fsum(
                d for t, d in zip(temperatures, durations, strict=True) if t > threshold["value"]
            ),
        )
        for threshold in zone["time_above_thresholds_k"]
    )
    coverage = _aggregate_coverage(exposure)
    return ExposureSummary(
        t_start_utc=seconds_to_iso(exposure.t_start_s),
        t_end_utc=seconds_to_iso(exposure.t_end_s),
        duration_s=history,
        mkt_k=mkt,
        mkt_activation_energy_j_per_mol=activation,
        mkt_windows=tuple(windows),
        mean_temperature_k=mean,
        min_temperature_k=min(temperatures) if segments else None,
        max_temperature_k=max(temperatures) if segments else None,
        time_above=time_above,
        cumulative_absorbed_dose_gy=math.fsum(
            s.dose_rate_gy_per_s * d for s, d in zip(segments, durations, strict=True)
        ),
        visible_dose_lux_h=math.fsum(s.illuminance_lux * d for s, d in zip(segments, durations, strict=True))
        / SECONDS_PER_HOUR,
        uv_dose_w_h_per_m2=math.fsum(s.uv_w_per_m2 * d for s, d in zip(segments, durations, strict=True))
        / SECONDS_PER_HOUR,
        coverage=coverage,
        longest_gap_s=max((entry.longest_gap_s for entry in coverage), default=0.0),
    )


def _aggregate_coverage(exposure: LotExposure) -> tuple[QuantityCoverage, ...]:
    """Coverage per (zone, quantity) summed over the lot's zone histories."""
    grouped: dict[tuple[str, Quantity], list[QuantityCoverage]] = {}
    for history in exposure.zone_histories:
        for entry in history.coverage:
            grouped.setdefault((entry.zone_id, entry.quantity), []).append(entry)
    out: list[QuantityCoverage] = []
    for zone_id, quantity in sorted(grouped, key=lambda key: (key[0], QUANTITIES.index(key[1]))):
        entries = grouped[(zone_id, quantity)]
        measured = math.fsum(e.measured_s for e in entries)
        held = math.fsum(e.held_s for e in entries)
        gap = math.fsum(e.gap_s for e in entries)
        declared = math.fsum(e.declared_s for e in entries)
        not_applicable = math.fsum(e.not_applicable_s for e in entries)
        denominator = measured + held + gap + declared
        fraction = (
            1.0 if denominator <= 0.0 else min(1.0, max(0.0, (measured + held + declared) / denominator))
        )
        out.append(
            QuantityCoverage(
                zone_id=zone_id,
                quantity=quantity,
                measured_s=measured,
                held_s=held,
                gap_s=gap,
                declared_s=declared,
                not_applicable_s=not_applicable,
                longest_gap_s=max(e.longest_gap_s for e in entries),
                coverage_fraction=fraction,
            )
        )
    return tuple(out)
