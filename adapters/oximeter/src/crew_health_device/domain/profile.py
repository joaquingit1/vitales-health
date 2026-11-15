"""Immutable sourced profile configuration and semantic validation."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Literal, cast

from .errors import ProfileError
from .readings import ProtocolUnit


@dataclass(frozen=True, slots=True)
class SourceRef:
    path: str
    note: str


@dataclass(frozen=True, slots=True)
class ComponentRule:
    field: str
    indicator_code: str
    units: Mapping[ProtocolUnit, str]
    # None when no source gives a device range: the reading then carries `unchecked:range:<code>`, never a guess.
    range_min: Decimal | None
    range_max: Decimal | None
    saturation_low: Decimal | None
    saturation_high: Decimal | None
    max_rate_per_second: Decimal | None


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    profile_id: str
    version: str
    family: str
    device_label: str
    model: str | None
    calibration: str
    hardware_evidence: Literal["unmeasured", "measured"]
    evidence_ref: str | None
    provenance: tuple[SourceRef, ...]
    components: tuple[ComponentRule, ...]
    min_samples: int
    rejecting_status: frozenset[str]
    noted_status: frozenset[str]
    panels: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # `window`: a continuous device; a reading ends complete only when its window or frame budget is used up, and a
    # lost link or a silence before that fails it. `measurement`: a spot device that delivers a measurement and
    # then goes quiet or drops the link; that end is complete once a measurement arrived.
    completion: Literal["window", "measurement"] = "window"
    # The reported value is the last sample equal to the one before it: the reading "stops changing and displays
    # one steady number" (C08). A reading that never settles is refused not_steady.
    steady_reading: bool = False

    def rule_for_indicator(self, code: str) -> ComponentRule | None:
        return next((rule for rule in self.components if rule.indicator_code == code), None)

    def rules_for_capability(self, capability: str) -> tuple[ComponentRule, ...]:
        """A panel capability (blood_pressure) yields one rule per component indicator, in panel order; any
        other capability yields its own indicator's rule, or nothing when the profile cannot serve it."""
        panel = next((codes for code, codes in self.panels if code == capability), None)
        if panel is not None:
            return tuple(rule for code in panel if (rule := self.rule_for_indicator(code)) is not None)
        rule = self.rule_for_indicator(capability)
        return () if rule is None else (rule,)


def _mapping(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProfileError(pointer, "expected_object")
    return cast(Mapping[str, object], value)


def _required(document: Mapping[str, object], key: str, pointer: str = "") -> object:
    if key not in document:
        raise ProfileError(f"{pointer}/{key}", "required")
    return document[key]


def _text(value: object, pointer: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(pointer, "expected_nonempty_string")
    return value


def _optional_text(value: object, pointer: str) -> str | None:
    return None if value is None else _text(value, pointer)


def _integer(value: object, pointer: str) -> int:
    if type(value) is not int or value < 1:
        raise ProfileError(pointer, "expected_positive_integer")
    return value


def _decimal(value: object, pointer: str) -> Decimal:
    if not isinstance(value, str):
        raise ProfileError(pointer, "decimal_must_be_string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ProfileError(pointer, "invalid_decimal") from error
    if not result.is_finite():
        raise ProfileError(pointer, "non_finite_decimal")
    return result


def _optional_decimal(value: object, pointer: str) -> Decimal | None:
    return None if value is None else _decimal(value, pointer)


def _list(value: object, pointer: str) -> list[object]:
    if not isinstance(value, list):
        raise ProfileError(pointer, "expected_array")
    return cast(list[object], value)


def _rule(value: object, pointer: str) -> ComponentRule:
    document = _mapping(value, pointer)
    field = _text(_required(document, "field", pointer), pointer + "/field")
    indicator = _text(_required(document, "indicator_code", pointer), pointer + "/indicator_code")
    units_document = _mapping(_required(document, "units", pointer), pointer + "/units")
    units = {key: _text(value, pointer + "/units/" + key) for key, value in units_document.items()}
    if not units:
        raise ProfileError(pointer + "/units", "empty_units")
    minimum = _optional_decimal(_required(document, "range_min", pointer), pointer + "/range_min")
    maximum = _optional_decimal(_required(document, "range_max", pointer), pointer + "/range_max")
    low = _optional_decimal(_required(document, "saturation_low", pointer), pointer + "/saturation_low")
    high = _optional_decimal(_required(document, "saturation_high", pointer), pointer + "/saturation_high")
    rate = _optional_decimal(
        _required(document, "max_rate_per_second", pointer), pointer + "/max_rate_per_second"
    )
    if (minimum is None) != (maximum is None):
        raise ProfileError(pointer + "/range_max", "range_bounds_paired")
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise ProfileError(pointer + "/range_max", "invalid_range")
    for key, bound in (("saturation_low", low), ("saturation_high", high)):
        if (
            bound is not None
            and minimum is not None
            and maximum is not None
            and not minimum <= bound <= maximum
        ):
            raise ProfileError(pointer + "/" + key, "saturation_outside_range")
    if low is not None and high is not None and low >= high:
        raise ProfileError(pointer + "/saturation_high", "invalid_saturation_order")
    if rate is not None and rate <= 0:
        raise ProfileError(pointer + "/max_rate_per_second", "expected_positive_decimal")
    return ComponentRule(field, indicator, MappingProxyType(units), minimum, maximum, low, high, rate)


def _statuses(document: Mapping[str, object], key: str) -> frozenset[str]:
    return frozenset(_text(item, "/" + key) for item in _list(_required(document, key), "/" + key))


def _panels(
    document: Mapping[str, object], components: tuple[ComponentRule, ...]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    indicators = {rule.indicator_code for rule in components}
    result: list[tuple[str, tuple[str, ...]]] = []
    for index, value in enumerate(_list(document.get("panels", []), "/panels")):
        pointer = f"/panels/{index}"
        panel = _mapping(value, pointer)
        code = _text(_required(panel, "capability_code", pointer), pointer + "/capability_code")
        codes = tuple(
            _text(item, f"{pointer}/indicator_codes/{n}")
            for n, item in enumerate(
                _list(_required(panel, "indicator_codes", pointer), pointer + "/indicator_codes")
            )
        )
        if len(codes) < 2 or len(set(codes)) != len(codes):
            raise ProfileError(pointer + "/indicator_codes", "invalid_panel")
        if not set(codes) <= indicators:
            raise ProfileError(pointer + "/indicator_codes", "panel_indicator_not_in_components")
        if code in indicators or any(code == other for other, _ in result):
            raise ProfileError(pointer + "/capability_code", "duplicate_capability")
        result.append((code, codes))
    return tuple(result)


def parse_profile(document: Mapping[str, object]) -> DeviceProfile:
    components = tuple(
        _rule(value, f"/components/{index}")
        for index, value in enumerate(_list(_required(document, "components"), "/components"))
    )
    if not components:
        raise ProfileError("/components", "empty_components")
    if len({rule.field for rule in components}) != len(components):
        raise ProfileError("/components", "duplicate_field")
    if len({rule.indicator_code for rule in components}) != len(components):
        raise ProfileError("/components", "duplicate_indicator")
    provenance = tuple(
        _source(item, f"/provenance/{index}")
        for index, item in enumerate(_list(_required(document, "provenance"), "/provenance"))
    )
    if not provenance:
        raise ProfileError("/provenance", "empty_provenance")
    rejecting, noted = _statuses(document, "rejecting_status"), _statuses(document, "noted_status")
    if rejecting & noted:
        raise ProfileError("/noted_status", "overlapping_status")
    evidence = _required(document, "hardware_evidence")
    if evidence not in ("measured", "unmeasured"):
        raise ProfileError("/hardware_evidence", "invalid_evidence")
    evidence_ref = _optional_text(_required(document, "evidence_ref"), "/evidence_ref")
    if evidence == "measured" and evidence_ref is None:
        raise ProfileError("/evidence_ref", "required_when_measured")
    return DeviceProfile(
        _text(_required(document, "profile_id"), "/profile_id"),
        _text(_required(document, "version"), "/version"),
        _text(_required(document, "family"), "/family"),
        _text(_required(document, "device_label"), "/device_label"),
        _optional_text(_required(document, "model"), "/model"),
        _text(_required(document, "calibration"), "/calibration"),
        evidence,
        evidence_ref,
        provenance,
        components,
        _integer(_required(document, "min_samples"), "/min_samples"),
        rejecting,
        noted,
        _panels(document, components),
        _completion(document.get("completion", "window")),
        _flag(document.get("steady_reading", False), "/steady_reading"),
    )


def _completion(value: object) -> Literal["window", "measurement"]:
    if value == "window":
        return "window"
    if value == "measurement":
        return "measurement"
    raise ProfileError("/completion", "invalid_completion")


def _flag(value: object, pointer: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileError(pointer, "expected_boolean")
    return value


def _source(value: object, pointer: str) -> SourceRef:
    document = _mapping(value, pointer)
    return SourceRef(
        _text(_required(document, "path", pointer), pointer + "/path"),
        _text(_required(document, "note", pointer), pointer + "/note"),
    )
