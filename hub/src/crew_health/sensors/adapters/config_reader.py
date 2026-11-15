"""Validated sensor configuration (E23 8, 8.1; CONTRACTS 11).

This is the only place generated Pydantic models parse sensor documents. Each document is validated against its
generated schema, then mapped to the pure facts of the domain with exact Decimal numbers. A candidate with any
finding is rejected whole and the last good configuration stays active (AGENTS 3, flight tables).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_environment_hazards_schema import EnvironmentHazardsJson
from crew_health.contracts_gen.schemas.config_environment_profiles_schema import EnvironmentProfilesJson
from crew_health.contracts_gen.schemas.config_sensor_catalog_schema import SensorCatalogJson
from crew_health.contracts_gen.schemas.config_sensor_limits_schema import SensorLimitsJson
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import UnitMappingJson
from crew_health.kernel.config import CandidateRelease, Finding, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.domain.card import EffectFilter, HazardEffect, HazardFacts
from crew_health.sensors.domain.channel import (
    AcceptedUnit,
    AggregationSpec,
    DisplaySpec,
    SensorTypeFacts,
)
from crew_health.sensors.domain.config import SensorConfig
from crew_health.sensors.domain.evaluation import ClearFacts, LimitFacts
from crew_health.sensors.domain.loader_checks import (
    CATALOG,
    CHECK_4,
    CHECK_12,
    LIMITS,
    UCUM_RESOLVER,
    DomainFinding,
    HazardLevelCopy,
    HazardLevelFacts,
    HazardLevels,
    LimitEntry,
    SensorBounds,
    check_catalog,
    check_limits_against_catalog,
    check_limits_against_hazards,
    check_scan_bound,
)

Json = dict[str, Any]
_NAMES: dict[str, ConfigName] = {
    CATALOG: ConfigName.SENSOR_CATALOG,
    LIMITS: ConfigName.SENSOR_LIMITS,
}


def _reject_constant(name: str) -> object:
    raise ValueError("non finite JSON constant " + name)


def _json(raw: bytes) -> Json:
    """Decode with exact decimals. NaN and Infinity are refused; a corrupt document is CONFIG_INVALID."""
    try:
        value = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "document is not valid JSON") from error
    if not isinstance(value, dict):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "document is not an object")
    return cast(Json, value)


def _validated[T](model: Callable[[bytes], T], raw: bytes) -> None:
    try:
        model(raw)
    except ValidationError as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "document fails its schema") from error


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    raise ProblemError(ErrorCode.CONFIG_INVALID, "not a number")


def _opt(value: object) -> Decimal | None:
    return None if value is None else _dec(value)


def _pair(value: Sequence[object]) -> tuple[Decimal, Decimal]:
    return (_dec(value[0]), _dec(value[1]))


def _accepted(item: Json) -> AcceptedUnit:
    return AcceptedUnit(item["unit"], item["conversion"], _opt(item.get("factor")))


def _sensor_type(item: Json) -> SensorTypeFacts:
    display = item["display"]
    interval = item["expected_interval_seconds"]
    return SensorTypeFacts(
        id=item["id"],
        hazard_id=item["hazard_id"],
        name=item["name"],
        long_name=item["long_name"],
        body_region=item["body_region"],
        unit=item["unit"],
        unit_display=item["unit_display"],
        decimals=item["decimals"],
        trim_trailing_zeros=bool(item.get("trim_trailing_zeros", False)),
        accepted_units=tuple(_accepted(a) for a in item["accepted_units"]),
        aggregations=tuple(
            AggregationSpec(a["id"], a["kind"], a["window_seconds"], a["label"]) for a in item["aggregations"]
        ),
        display_aggregation_id=item["display_aggregation_id"],
        display=DisplaySpec(
            _pair(display["range"]),
            tuple((k, _pair(v)) for k, v in display["by_environment"].items()),
            display["basis"],
        ),
        expected_interval_seconds=None if interval is None else int(interval),
        interval_basis=item["interval_basis"],
        interval_source_ids=tuple(item["interval_source_ids"]),
        stale_after_seconds=item["stale_after_seconds"],
        stale_basis=item["stale_basis"],
        no_reading_label=item["no_reading_label"],
        manual_alternative_item_id=item["manual_alternative_item_id"],
        citation_text=item["citation_text"],
    )


def parse_catalog(raw: bytes) -> tuple[str, tuple[SensorTypeFacts, ...]]:
    _validated(SensorCatalogJson.model_validate_json, raw)
    document = _json(raw)
    return document["notice"], tuple(_sensor_type(i) for i in document["sensor_types"])


def _limit_entry(item: Json) -> LimitEntry:
    clear = item["clear"]
    copy = item["hazard_level"]
    limit = LimitFacts(
        limit_id=item["limit_id"],
        sensor_type_id=item["sensor_type_id"],
        environments=tuple(item["environments"]),
        role=item["role"],
        level=item["level"],
        comparator=item["comparator"],
        value=_opt(item["value"]),
        unit=item["unit"],
        aggregation_id=item["aggregation_id"],
        min_coverage=_dec(item["min_coverage"]),
        clear=ClearFacts(clear["comparator"], _opt(clear["value"]), clear["dwell_seconds"]),
        label=item["label"],
        card_mark=item["card_mark"],
        status=item["status"],
        source_ids=tuple(item["source_ids"]),
    )
    hazard_copy = HazardLevelCopy(
        copy["hazard_id"], copy["index"], copy["level"], _opt(copy["value"]), copy["unit"], copy["window"]
    )
    return LimitEntry(limit, hazard_copy, item["value_from"], item["basis"])


def parse_limits(raw: bytes) -> tuple[str, tuple[LimitEntry, ...]]:
    _validated(SensorLimitsJson.model_validate_json, raw)
    document = _json(raw)
    return document["notice"], tuple(_limit_entry(i) for i in document["limits"])


@dataclass(frozen=True, slots=True)
class HazardDocument:
    notice: str
    hazards: tuple[HazardFacts, ...]
    levels: tuple[HazardLevels, ...]
    earth_delay: tuple[tuple[str, str], ...]
    environment_hazard_ids: tuple[tuple[str, tuple[str, ...]], ...]


def _hazard(item: Json) -> HazardFacts:
    return HazardFacts(
        id=item["id"],
        name=item["name"],
        kind=item["kind"],
        effects=tuple(
            HazardEffect(e["effect"], e["body_region"], tuple(e["source_ids"])) for e in item["effects"]
        ),
        by_environment=tuple(item["by_environment"].items()),
        what_to_do=tuple(item["what_to_do"].items()),
        source_ids=tuple(item["source_ids"]),
    )


def _levels(item: Json) -> HazardLevels:
    return HazardLevels(
        item["id"],
        tuple(
            HazardLevelFacts(lv["level"], _opt(lv["value"]), lv["unit"], lv["window"], lv["rule"])
            for lv in item["levels"]
        ),
    )


def parse_hazards(raw: bytes) -> HazardDocument:
    _validated(EnvironmentHazardsJson.model_validate_json, raw)
    document = _json(raw)
    return HazardDocument(
        notice=document["notice"],
        hazards=tuple(_hazard(h) for h in document["hazards"]),
        levels=tuple(_levels(h) for h in document["hazards"]),
        earth_delay=tuple((e["id"], e["earth_delay"]) for e in document["environments"]),
        environment_hazard_ids=tuple((e["id"], tuple(e["hazard_ids"])) for e in document["environments"]),
    )


def parse_effect_filters(raw: bytes) -> tuple[EffectFilter, ...]:
    _validated(EnvironmentProfilesJson.model_validate_json, raw)
    document = _json(raw)
    return tuple(
        EffectFilter(f["hazard_id"], f["effect_index"], f["effect_starts_with"], tuple(f["only_in"]))
        for f in document["effect_filters"]
    )


ReadDocument = Callable[[ConfigName], bytes]


def load_sensor_config(read: ReadDocument) -> SensorConfig:
    """Parse and validate the four documents the sensors read. Any failure raises CONFIG_INVALID."""
    catalog_notice, sensor_types = parse_catalog(read(ConfigName.SENSOR_CATALOG))
    limits_notice, entries = parse_limits(read(ConfigName.SENSOR_LIMITS))
    hazards = parse_hazards(read(ConfigName.ENVIRONMENT_HAZARDS))
    filters = parse_effect_filters(read(ConfigName.ENVIRONMENT_PROFILES))
    return SensorConfig(
        catalog_notice,
        limits_notice,
        hazards.notice,
        sensor_types,
        entries,
        hazards.hazards,
        hazards.levels,
        hazards.earth_delay,
        hazards.environment_hazard_ids,
        filters,
        _canonical_ucum(read, sensor_types),
    )


def _canonical_ucum(
    read: ReadDocument, sensor_types: Sequence[SensorTypeFacts]
) -> tuple[tuple[str, str], ...]:
    """Each type's canonical unit as the UCUM code D12's unit mapping gives its catalog spelling. A document that
    is missing or unreadable maps nothing, so every reading of the type is rejected as INVALID_UNIT, never guessed."""
    try:
        document = UnitMappingJson.model_validate_json(read(ConfigName.UNIT_MAPPING))
    except ValidationError, KeyError:
        return ()
    table = {(m.indicator_code, m.wire_unit): m.ucum_unit for m in document.mappings}
    return tuple(sorted((t.id, table[(t.id, t.unit)]) for t in sensor_types if (t.id, t.unit) in table))


def config_from_release(release: ReleaseHandle) -> SensorConfig:
    return load_sensor_config(lambda name: release.document(name).raw)


def _findings(found: Sequence[DomainFinding]) -> tuple[Finding, ...]:
    return tuple(Finding(f.check_id, _NAMES.get(f.document), f.pointer, f.message) for f in found)


def _unreadable(check_id: str, document: ConfigName) -> tuple[Finding, ...]:
    return (Finding(check_id, document, "", "document is missing, corrupt or fails its schema"),)


def _candidate_bytes(candidate: CandidateRelease, name: ConfigName) -> bytes | None:
    view = candidate.documents.get(name)
    return None if view is None else view.raw


class Check4SensorLimits:
    """CONTRACTS 11 check 4: sensor-limits.json copies environment-hazards.json exactly."""

    check_id = CHECK_4

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        limits_raw = _candidate_bytes(candidate, ConfigName.SENSOR_LIMITS)
        hazards_raw = _candidate_bytes(candidate, ConfigName.ENVIRONMENT_HAZARDS)
        if limits_raw is None or hazards_raw is None:
            return _unreadable(self.check_id, ConfigName.SENSOR_LIMITS)
        try:
            _, entries = parse_limits(limits_raw)
            hazards = parse_hazards(hazards_raw)
        except ProblemError:
            return _unreadable(self.check_id, ConfigName.SENSOR_LIMITS)
        levels = {h.hazard_id: h for h in hazards.levels}
        return _findings(check_limits_against_hazards(entries, levels))


@dataclass(frozen=True, slots=True)
class Check12Sensors:
    """CONTRACTS 11 check 12, sensor half, plus unit mapping, dimension, window and bucket bounds."""

    bounds: SensorBounds
    units: UnitRegistry
    resolve: UCUM_RESOLVER
    check_id: str = CHECK_12 + ".sensor"  # the contributor id E01 requires (config loader)

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        catalog_raw = _candidate_bytes(candidate, ConfigName.SENSOR_CATALOG)
        limits_raw = _candidate_bytes(candidate, ConfigName.SENSOR_LIMITS)
        if catalog_raw is None or limits_raw is None:
            return _unreadable(self.check_id, ConfigName.SENSOR_CATALOG)
        try:
            _, sensor_types = parse_catalog(catalog_raw)
            _, entries = parse_limits(limits_raw)
        except ProblemError:
            return _unreadable(self.check_id, ConfigName.SENSOR_CATALOG)
        by_id = {s.id: s for s in sensor_types}
        found = [
            *check_catalog(sensor_types, self.bounds, self.units, self.resolve),
            *check_limits_against_catalog(entries, by_id),
        ]
        return _findings(found)


@dataclass(frozen=True, slots=True)
class ScanBoundGuard:
    """Activation guard (E23 3.7): no max or sum window may scan more than the bound at the fastest cadence.

    `registry_intervals` reads the channel registry overrides inside the activation unit of work.
    """

    scan_max: int
    registry_intervals: Callable[[UnitOfWork], Mapping[str, int]]
    check_id: str = CHECK_12 + ".scan_bound"

    def check(self, uow: UnitOfWork, candidate: CandidateRelease) -> tuple[Finding, ...]:
        catalog_raw = _candidate_bytes(candidate, ConfigName.SENSOR_CATALOG)
        limits_raw = _candidate_bytes(candidate, ConfigName.SENSOR_LIMITS)
        if catalog_raw is None or limits_raw is None:
            return _unreadable(self.check_id, ConfigName.SENSOR_CATALOG)
        try:
            _, sensor_types = parse_catalog(catalog_raw)
            _, entries = parse_limits(limits_raw)
        except ProblemError:
            return _unreadable(self.check_id, ConfigName.SENSOR_CATALOG)
        overrides = self.registry_intervals(uow)
        by_id = {s.id: s for s in sensor_types}

        def fastest(sensor_type_id: str) -> int | None:
            known = [overrides[sensor_type_id]] if sensor_type_id in overrides else []
            sensor = by_id.get(sensor_type_id)
            if sensor is not None and sensor.expected_interval_seconds is not None:
                known.append(sensor.expected_interval_seconds)
            return min(known) if known else None

        return _findings(check_scan_bound(by_id, entries, fastest, self.scan_max))


class SensorConfigHolder:
    """Holds the last good configuration. A rejected candidate changes nothing (AGENTS 3)."""

    def __init__(self, initial: SensorConfig | None = None) -> None:
        self._current = initial

    @property
    def current(self) -> SensorConfig | None:
        return self._current

    def adopt(
        self, candidate: CandidateRelease, checks: Sequence[Check4SensorLimits | Check12Sensors]
    ) -> tuple[Finding, ...]:
        """Run the checks, then parse. Only a candidate with no finding and no parse error replaces it."""
        findings = tuple(f for check in checks for f in check.check(candidate))
        if findings:
            return findings
        try:
            adopted = load_sensor_config(lambda name: candidate.documents[name].raw)
        except ProblemError, KeyError:
            return (Finding("config", None, "", "candidate does not parse; last good retained"),)
        self._current = adopted
        return ()
