"""Loader checks for the sensor documents as pure rules (CONTRACTS 11 checks 4 and 12; E23 3.7, 8, 11).

Check 4 compares sensor-limits.json with environment-hazards.json. Check 12 (sensor half) ties each limit to its
sensor type. The catalog checks add structure, unit mapping and bound rules. Nothing here edits or defaults a
value: a finding is the only output, and a document with any finding is rejected whole.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import UnitRegistry
from crew_health.sensors.domain.channel import Level, SensorTypeFacts
from crew_health.sensors.domain.evaluation import LimitFacts

CHECK_4 = "4"
CHECK_12 = "12"
CATALOG = "sensor-catalog"
LIMITS = "sensor-limits"
UCUM_RESOLVER = Callable[[str, str], str | None]  # (sensor_type_id, wire spelling) -> UCUM code or None


@dataclass(frozen=True, slots=True)
class DomainFinding:
    check_id: str
    document: str
    pointer: str
    message: str


@dataclass(frozen=True, slots=True)
class HazardLevelFacts:
    level: Level
    value: Decimal | None
    unit: str | None
    window: str
    rule: str


@dataclass(frozen=True, slots=True)
class HazardLevels:
    hazard_id: str
    levels: tuple[HazardLevelFacts, ...]


@dataclass(frozen=True, slots=True)
class HazardLevelCopy:
    hazard_id: str
    index: int
    level: Level
    value: Decimal | None
    unit: str | None
    window: str


@dataclass(frozen=True, slots=True)
class LimitEntry:
    limit: LimitFacts
    copy: HazardLevelCopy
    value_from: str
    basis: str


@dataclass(frozen=True, slots=True)
class SensorBounds:
    """The section 11 hub settings the checks need. Operational bounds, never domain content."""

    aggregate_bucket_s: int
    window_max_buckets: int
    window_scan_max_readings: int


def _find(check: str, document: str, pointer: str, message: str) -> DomainFinding:
    return DomainFinding(check, document, pointer, message)


def _number_in(text: str, value: Decimal) -> bool:
    token = format(value.normalize(), "f")
    return re.search(r"(?<![0-9.,])" + re.escape(token) + r"(?![0-9]|[.,][0-9])", text) is not None


def _check_value_from(entry: LimitEntry, level: HazardLevelFacts, pointer: str) -> list[DomainFinding]:
    limit = entry.limit
    if entry.value_from == "level_value" and limit.value != level.value:
        return [_find(CHECK_4, LIMITS, pointer + "/value", "value differs from the hazard level value")]
    if entry.value_from == "rule_text" and (limit.value is None or not _number_in(level.rule, limit.value)):
        return [_find(CHECK_4, LIMITS, pointer + "/value", "value does not appear in the hazard level rule text")]
    if entry.value_from == "pending" and limit.value is not None:
        return [_find(CHECK_4, LIMITS, pointer + "/value", "pending limit must have a null value")]
    return []


def _check_copy(entry: LimitEntry, level: HazardLevelFacts, pointer: str) -> list[DomainFinding]:
    copy = entry.copy
    found: list[DomainFinding] = []
    for name, mine, theirs in (
        ("level", copy.level, level.level),
        ("value", copy.value, level.value),
        ("unit", copy.unit, level.unit),
        ("window", copy.window, level.window),
    ):
        if mine != theirs:
            found.append(_find(CHECK_4, LIMITS, f"{pointer}/hazard_level/{name}", "differs from environment-hazards.json"))
    return found


def check_limits_against_hazards(
    entries: Sequence[LimitEntry], hazards: Mapping[str, HazardLevels]
) -> tuple[DomainFinding, ...]:
    """CONTRACTS 11 check 4, including at most one card_mark per sensor type and environment."""
    found: list[DomainFinding] = []
    marks: dict[tuple[str, str], int] = {}
    for position, entry in enumerate(entries):
        pointer = f"/limits/{position}"
        hazard = hazards.get(entry.copy.hazard_id)
        if hazard is None or not 0 <= entry.copy.index < len(hazard.levels):
            found.append(_find(CHECK_4, LIMITS, pointer + "/hazard_level", "no such hazard level"))
            continue
        level = hazard.levels[entry.copy.index]
        found.extend(_check_copy(entry, level, pointer))
        found.extend(_check_value_from(entry, level, pointer))
        if entry.limit.card_mark:
            for environment in entry.limit.environments:
                key = (entry.limit.sensor_type_id, environment)
                marks[key] = marks.get(key, 0) + 1
    found.extend(
        _find(CHECK_4, LIMITS, "/limits", f"more than one card_mark for {sensor} in {environment}")
        for (sensor, environment), count in sorted(marks.items())
        if count > 1
    )
    return tuple(found)


def _check_status(entry: LimitEntry, pointer: str) -> list[DomainFinding]:
    limit = entry.limit
    pending = limit.status == "threshold_pending"
    found: list[DomainFinding] = []
    if pending != (limit.value is None) or pending != (entry.value_from == "pending"):
        found.append(_find(CHECK_12, LIMITS, pointer + "/status", "threshold_pending, null value and pending value_from must agree"))
    if entry.basis == "sourced" and not limit.source_ids:
        found.append(_find(CHECK_12, LIMITS, pointer + "/source_ids", "a sourced limit names its sources"))
    return found


def check_limits_against_catalog(
    entries: Sequence[LimitEntry], sensor_types: Mapping[str, SensorTypeFacts]
) -> tuple[DomainFinding, ...]:
    """CONTRACTS 11 check 12, sensor half: the aggregation exists and the unit is the catalog unit."""
    found: list[DomainFinding] = []
    for position, entry in enumerate(entries):
        pointer = f"/limits/{position}"
        sensor = sensor_types.get(entry.limit.sensor_type_id)
        if sensor is None:
            found.append(_find(CHECK_12, LIMITS, pointer + "/sensor_type_id", "unknown sensor type"))
            continue
        if sensor.aggregation(entry.limit.aggregation_id) is None:
            found.append(_find(CHECK_12, LIMITS, pointer + "/aggregation_id", "aggregation not on the sensor type"))
        if entry.limit.unit != sensor.unit:
            found.append(_find(CHECK_12, LIMITS, pointer + "/unit", "not the catalog unit"))
        if entry.copy.hazard_id != sensor.hazard_id:
            found.append(_find(CHECK_12, LIMITS, pointer + "/hazard_level/hazard_id", "not the hazard of the sensor type"))
        found.extend(_check_status(entry, pointer))
    return tuple(found)


def _check_units(
    sensor: SensorTypeFacts, pointer: str, units: UnitRegistry, resolve: UCUM_RESOLVER
) -> list[DomainFinding]:
    found: list[DomainFinding] = []
    canonical = resolve(sensor.id, sensor.unit)
    if canonical is None:
        return [_find(CHECK_12, CATALOG, pointer + "/unit", "no sourced unit mapping")]
    if not any(a.unit == sensor.unit and a.conversion == "identity" for a in sensor.accepted_units):
        found.append(_find(CHECK_12, CATALOG, pointer + "/accepted_units", "the canonical unit must be accepted with identity"))
    for index, accepted in enumerate(sensor.accepted_units):
        item = f"{pointer}/accepted_units/{index}"
        found.extend(_check_accepted(sensor, accepted.unit, accepted.conversion, accepted.factor, item, canonical, units, resolve))
    return found


def _check_accepted(
    sensor: SensorTypeFacts,
    unit: str,
    conversion: str,
    factor: Decimal | None,
    pointer: str,
    canonical: str,
    units: UnitRegistry,
    resolve: UCUM_RESOLVER,
) -> list[DomainFinding]:
    if (conversion == "scale") != (factor is not None) or (factor is not None and factor <= 0):
        return [_find(CHECK_12, CATALOG, pointer + "/factor", "scale needs a positive factor, others none")]
    if conversion == "identity" and unit != sensor.unit:
        return [_find(CHECK_12, CATALOG, pointer + "/unit", "identity applies only to the canonical unit")]
    code = resolve(sensor.id, unit)
    if code is None:
        return [_find(CHECK_12, CATALOG, pointer + "/unit", "no sourced unit mapping")]
    if conversion == "ppm_to_mmhg":
        return []  # a contextual conversion: dimensions differ by design, the pressure is the context
    try:
        if not units.commensurable(units.parse(code), units.parse(canonical)):
            return [_find(CHECK_12, CATALOG, pointer + "/unit", "dimension differs from the canonical unit")]
    except ProblemError:
        return [_find(CHECK_12, CATALOG, pointer + "/unit", "unit is not a known UCUM unit")]
    return []


def _check_aggregations(sensor: SensorTypeFacts, pointer: str, bounds: SensorBounds) -> list[DomainFinding]:
    found: list[DomainFinding] = []
    seen: set[str] = set()
    for index, spec in enumerate(sensor.aggregations):
        item = f"{pointer}/aggregations/{index}"
        if spec.id in seen:
            found.append(_find(CHECK_12, CATALOG, item + "/id", "duplicate aggregation id"))
        seen.add(spec.id)
        if (spec.kind == "latest") != (spec.window_seconds is None):
            found.append(_find(CHECK_12, CATALOG, item + "/window_seconds", "only latest has no window"))
        elif spec.window_seconds is not None:
            found.extend(_check_window(sensor, spec.kind, spec.window_seconds, item, bounds))
    if sensor.display_aggregation_id not in seen:
        found.append(_find(CHECK_12, CATALOG, pointer + "/display_aggregation_id", "not an aggregation of the type"))
    return found


def _check_window(
    sensor: SensorTypeFacts, kind: str, window: int, pointer: str, bounds: SensorBounds
) -> list[DomainFinding]:
    if kind in ("max", "sum"):
        return []  # scan bound, checked by check_scan_bound against every cadence
    if ceil(window / bounds.aggregate_bucket_s) > bounds.window_max_buckets:
        return [_find(CHECK_12, CATALOG, pointer + "/window_seconds", "window spans more buckets than sensors.window_max_buckets")]
    interval = sensor.expected_interval_seconds
    if interval is not None and ceil(bounds.aggregate_bucket_s / interval) > bounds.window_scan_max_readings:
        return [_find(CHECK_12, CATALOG, pointer + "/window_seconds", "an edge bucket would exceed the scan bound")]
    return []


def check_catalog(
    sensor_types: Sequence[SensorTypeFacts],
    bounds: SensorBounds,
    units: UnitRegistry,
    resolve: UCUM_RESOLVER,
) -> tuple[DomainFinding, ...]:
    """Structure, unit dimension and mapping, aggregation and bucket bounds, and provenance of the catalog."""
    found: list[DomainFinding] = []
    ids: set[str] = set()
    for position, sensor in enumerate(sensor_types):
        pointer = f"/sensor_types/{position}"
        if sensor.id in ids:
            found.append(_find(CHECK_12, CATALOG, pointer + "/id", "duplicate sensor type"))
        ids.add(sensor.id)
        found.extend(_check_units(sensor, pointer, units, resolve))
        found.extend(_check_aggregations(sensor, pointer, bounds))
        found.extend(_check_provenance(sensor, pointer))
    return tuple(found)


def _check_provenance(sensor: SensorTypeFacts, pointer: str) -> list[DomainFinding]:
    found: list[DomainFinding] = []
    if sensor.interval_basis == "sourced" and not sensor.interval_source_ids:
        found.append(_find(CHECK_12, CATALOG, pointer + "/interval_source_ids", "a sourced interval names its sources"))
    interval = sensor.expected_interval_seconds
    if interval is not None and sensor.stale_after_seconds < interval:
        found.append(_find(CHECK_12, CATALOG, pointer + "/stale_after_seconds", "stale before the channel is due to report"))
    return found


def check_scan_bound(
    sensor_types: Mapping[str, SensorTypeFacts],
    entries: Sequence[LimitEntry],
    fastest_interval: Callable[[str], int | None],
    scan_max: int,
) -> tuple[DomainFinding, ...]:
    """Activation guard (E23 3.7): a max or sum limit may not scan more than scan_max readings.

    `fastest_interval` gives the smallest of the catalog interval and every registry override. Without any
    interval the scan cannot be bounded, so the release is rejected.
    """
    found: list[DomainFinding] = []
    for position, entry in enumerate(entries):
        sensor = sensor_types.get(entry.limit.sensor_type_id)
        spec = None if sensor is None else sensor.aggregation(entry.limit.aggregation_id)
        if spec is None or spec.kind not in ("max", "sum") or spec.window_seconds is None:
            continue
        pointer = f"/limits/{position}/aggregation_id"
        interval = fastest_interval(entry.limit.sensor_type_id)
        if interval is None or interval < 1:
            found.append(_find(CHECK_12, LIMITS, pointer, "max or sum window needs a known reporting interval"))
        elif ceil(spec.window_seconds / interval) > scan_max:
            found.append(_find(CHECK_12, LIMITS, pointer, "window would scan more readings than sensors.window_scan_max_readings"))
    return tuple(found)
