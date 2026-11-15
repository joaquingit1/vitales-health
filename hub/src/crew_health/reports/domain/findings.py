"""E09-03 specialist structured findings and the shared value/unit check (CONTRACTS 2; input-dictionary.csv
ecg_report and exposure rows).

Mechanism only: the form definitions (fields, units, required attributes, allowed origins) are injected
(PLAN P3 settles their configuration home); no clinical form content is written in product code. Units travel
with values and are validated through the kernel UnitRegistry. A textual finding is never forced into a number;
an interpretation is its own text finding with its own origin, separate from the measured result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as Day
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.units import Unit, UnitRegistry
from crew_health.reports.domain.errors import invalid_unit, invalid_value
from crew_health.reports.domain.model import (
    ConfirmedFinding,
    FindingOrigin,
    FindingValue,
    OriginMode,
    finite_number,
    require_config_id,
)

type ValueKind = Literal["numeric", "text"]


@dataclass(frozen=True, slots=True)
class FindingPolicy:
    """What an indicator code accepts. Sourced from the indicator catalog and unit mapping (P1/P3)."""

    code: str
    kind: ValueKind
    accepted_units: frozenset[str]
    source_ref: str

    def validate(self) -> None:
        require_config_id(self.code)
        if not self.source_ref or (self.kind == "numeric") != bool(self.accepted_units):
            raise invalid_value("policy_invalid")


def check_unit(unit: str | None, policy: FindingPolicy, units: UnitRegistry) -> Unit | None:
    """The unit is parsed by the registry and must be one the policy accepts. Text takes no unit."""
    if policy.kind == "text":
        if unit is not None:
            raise invalid_unit("text_has_unit")
        return None
    if not unit:
        raise invalid_unit("unit_required")
    try:
        parsed = units.parse(unit)
    except ProblemError as error:
        raise invalid_unit("unit_unknown") from error
    if parsed.code not in policy.accepted_units:
        raise invalid_unit("unit_not_accepted")
    return parsed


def check_value(value: object, policy: FindingPolicy) -> FindingValue:
    if policy.kind == "numeric":
        return finite_number(value)
    if not isinstance(value, str) or not value.strip():
        raise invalid_value("text_required")
    return value


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    field_id: str
    policy: FindingPolicy
    role: Literal["result", "interpretation"]  # internal enum
    required: bool
    reference_interval_required: bool


@dataclass(frozen=True, slots=True)
class FormDefinition:
    form_id: str
    family: str
    version: str
    source_ref: str
    fields: tuple[FieldDefinition, ...]
    required_attributes: frozenset[str]
    interpretation_origins: frozenset[FindingOrigin]

    def __post_init__(self) -> None:
        require_id(self.form_id)
        ids = [f.field_id for f in self.fields]
        if not self.family or not self.version or not self.source_ref or not ids or len(set(ids)) != len(ids):
            raise invalid_value("form_invalid")
        for field in self.fields:
            field.policy.validate()
            if field.role == "interpretation" and field.policy.kind != "text":
                raise invalid_value("interpretation_text")
            if field.reference_interval_required and field.policy.kind != "numeric":
                raise invalid_value("form_invalid")


@dataclass(frozen=True, slots=True)
class FieldEntry:
    field_id: str
    value: object
    unit: str | None
    reference_low: Decimal | None
    reference_high: Decimal | None
    origin: FindingOrigin | None  # interpretation fields only


@dataclass(frozen=True, slots=True, repr=False)
class SpecialistEntry:
    form_id: str
    patient_id: str
    examined_on: Day | None
    attributes: tuple[tuple[str, str], ...]
    fields: tuple[FieldEntry, ...]
    result_origin: FindingOrigin
    origin_mode: OriginMode

    def __repr__(self) -> str:
        return f"SpecialistEntry({self.form_id})"


@dataclass(frozen=True, slots=True)
class Provenance:
    confirmer_id: str
    confirmed_at: datetime
    ids: IdFactory
    entry_key: str


def _interval(entry: FieldEntry, definition: FieldDefinition) -> tuple[Decimal, Decimal] | None:
    low, high = entry.reference_low, entry.reference_high
    if low is None and high is None:
        if definition.reference_interval_required:
            raise invalid_value("reference_interval_required", field=definition.field_id)
        return None
    if low is None or high is None:
        raise invalid_value("reference_interval_partial", field=definition.field_id)
    finite_number(low)
    finite_number(high)
    if low > high:
        raise invalid_value("reference_interval_order", field=definition.field_id)
    return low, high


def _attributes(form: FormDefinition, entry: SpecialistEntry) -> tuple[tuple[str, str], ...]:
    given = dict(entry.attributes)
    if len(given) != len(entry.attributes) or any(not k or not v for k, v in given.items()):
        raise invalid_value("attribute_invalid")
    if not form.required_attributes <= given.keys():
        raise invalid_value("attribute_missing")
    return tuple(sorted(given.items()))


def _field_finding(
    form: FormDefinition,
    entry: SpecialistEntry,
    definition: FieldDefinition,
    item: FieldEntry,
    units: UnitRegistry,
    who: Provenance,
) -> ConfirmedFinding:
    policy = definition.policy
    unit = check_unit(item.unit, policy, units)
    value = check_value(item.value, policy)
    interpretation = definition.role == "interpretation"
    if interpretation:
        if item.origin is None or item.origin not in form.interpretation_origins:
            raise invalid_value("interpretation_origin", field=definition.field_id)
        origin: FindingOrigin = item.origin
    else:
        if item.origin is not None:
            raise invalid_value("origin_on_result", field=definition.field_id)
        origin = entry.result_origin
    return ConfirmedFinding(
        who.ids.derived("fnd", who.entry_key, item.field_id),
        None,
        None,
        entry.patient_id,
        policy.code,
        value,
        None if unit is None else unit.code,
        entry.examined_on,
        None,
        origin,
        "interpretation" if interpretation else "result",
        entry.origin_mode,
        who.confirmer_id,
        who.confirmed_at,
        None,
        None,
        _attributes(form, entry),
        None if interpretation else _interval(item, definition),
        None,
    )


def validate_entry(
    form: FormDefinition, entry: SpecialistEntry, *, units: UnitRegistry, who: Provenance
) -> tuple[ConfirmedFinding, ...]:
    """All-or-nothing: the first failure raises and no finding is returned."""
    if entry.form_id != form.form_id or entry.examined_on is None:
        raise invalid_value("form_or_date")
    if entry.result_origin == "extraction":
        raise invalid_value("origin_extraction_manual")
    definitions: Mapping[str, FieldDefinition] = {f.field_id: f for f in form.fields}
    given = [item.field_id for item in entry.fields]
    if len(set(given)) != len(given) or not set(given) <= definitions.keys():
        raise invalid_value("field_unknown_or_repeated")
    if any(f.required and f.field_id not in given for f in form.fields):
        raise invalid_value("field_missing")
    if not given:
        raise invalid_value("field_missing")
    return tuple(
        _field_finding(form, entry, definitions[item.field_id], item, units, who) for item in entry.fields
    )
