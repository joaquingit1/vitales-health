"""T-E09-03 E09-03 V38 R14 R16: specialist structured findings keep units, method and interpretation origin.

Form definitions are built here from rows of docs/product/input-dictionary.csv (required attributes come from
the row's validation sentence). Units and values are synthetic goldens; no clinical content lives in product
code, and PLAN P3 still owns the real configuration home."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryIds
from crew_health.reports.domain.findings import (
    FieldEntry,
    FormDefinition,
    Provenance,
    SpecialistEntry,
    validate_entry,
)

from .reports_harness import (
    AT,
    OWNER,
    UNITS,
    dictionary_row,
    form_from_dictionary,
    numeric,
    required_attributes,
    text,
)

pytestmark = pytest.mark.trace("T-E09-03", "E09-03", "V38", "R14", "R16")

DAY = date(2026, 9, 30)
NUMERIC_FAMILIES = {
    "acuity": ("eye", "mm[Hg]"),  # synthetic unit for the golden, not a clinical claim
    "lipase": ("lab", "mmol/L"),
    "radiation_dose": ("exposure", "mGy"),
}
TEXT_FAMILIES = {"ecg_report": "cardiac", "imaging": "examination"}


def who() -> Provenance:
    return Provenance(OWNER, AT, MemoryIds(identifiers=tuple(str(i) for i in range(200))), "entry1")


def form(field_id: str, **kw: object) -> FormDefinition:
    if field_id in NUMERIC_FAMILIES:
        result = numeric(field_id, NUMERIC_FAMILIES[field_id][1])
    else:
        result = text(field_id)
    return form_from_dictionary(field_id, result=result, interpretation=text(field_id + "_reading"), **kw)  # type: ignore[arg-type]


def entry(definition: FormDefinition, **changes: object) -> SpecialistEntry:
    numeric_form = definition.fields[0].policy.kind == "numeric"
    value: object = Decimal("12.5") if numeric_form else "regular"
    unit = next(iter(definition.fields[0].policy.accepted_units), None)
    base = SpecialistEntry(
        definition.form_id,
        OWNER,
        DAY,
        tuple((name, "v") for name in sorted(definition.required_attributes)),
        (
            FieldEntry("result", value, unit, Decimal(1), Decimal(20), None)
            if definition.fields[0].reference_interval_required
            else FieldEntry("result", value, unit, None, None, None),
            FieldEntry("interpretation", "clinician reading", None, None, None, "clinician"),
        ),
        "device",
        "SIM",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize("field_id", [*NUMERIC_FAMILIES, *TEXT_FAMILIES])
def test_all_five_families_validate_with_units_attributes_and_separate_origins(field_id: str) -> None:
    definition = form(field_id)
    result, interpretation = validate_entry(definition, entry(definition), units=UNITS, who=who())
    row = dictionary_row(field_id)
    assert definition.family == row["group"] and definition.required_attributes
    assert dict(result.attributes).keys() == definition.required_attributes
    assert (result.role, result.origin) == ("result", "device")
    assert (interpretation.role, interpretation.origin) == ("interpretation", "clinician")
    assert isinstance(interpretation.value, str) and interpretation.unit is None
    assert result.observed_on == DAY and result.confirmer_id == OWNER
    if field_id in NUMERIC_FAMILIES:
        assert isinstance(result.value, Decimal) and result.unit == NUMERIC_FAMILIES[field_id][1]
    else:
        assert isinstance(result.value, str) and result.unit is None


def test_required_attributes_follow_the_dictionary_row_independently_checked() -> None:
    # Checked by hand against the two source sentences, not derived from the parser under test.
    assert required_attributes(dictionary_row("ecg_report")) == {
        "operator", "instrument", "date", "source", "interpretation_status",
    }  # fmt: skip
    assert required_attributes(dictionary_row("radiation_dose")) == {
        "time_interval", "instrument", "location", "actual_quantity",
    }  # fmt: skip


def test_radiation_quantities_are_kept_as_entered_and_never_converted() -> None:
    definition = form("radiation_dose")
    good = validate_entry(definition, entry(definition), units=UNITS, who=who())[0]
    assert (good.unit, good.value) == ("mGy", Decimal("12.5"))
    swapped = entry(definition, fields=(FieldEntry("result", Decimal(3), "uSv", None, None, None),))
    with pytest.raises(ProblemError) as error:
        validate_entry(definition, swapped, units=UNITS, who=who())
    assert error.value.field_errors == () and str(error.value) == "INVALID_UNIT"


def test_a_textual_finding_is_never_forced_into_a_number() -> None:
    definition = form("ecg_report")
    findings = validate_entry(definition, entry(definition), units=UNITS, who=who())
    assert all(not isinstance(f.value, Decimal) for f in findings)
    numeric_for_text = entry(definition, fields=(FieldEntry("result", Decimal(1), None, None, None, None),))
    with pytest.raises(ProblemError):
        validate_entry(definition, numeric_for_text, units=UNITS, who=who())


@pytest.mark.parametrize(
    "changes",
    [
        {"examined_on": None},
        {"form_id": "other"},
        {"attributes": ()},
        {"result_origin": "extraction"},
        {"fields": ()},
    ],
)
def test_missing_date_form_attributes_origin_or_fields_refuse_the_whole_entry(
    changes: dict[str, object],
) -> None:
    definition = form("radiation_dose")
    with pytest.raises(ProblemError):
        validate_entry(definition, entry(definition, **changes), units=UNITS, who=who())


def test_unknown_repeated_and_missing_required_fields_are_refused() -> None:
    definition = form("radiation_dose")
    good = entry(definition)
    only_interpretation = (good.fields[1],)
    repeated = (good.fields[0], good.fields[0])
    stranger = (FieldEntry("nope", "x", None, None, None, None),)
    for fields in (only_interpretation, repeated, stranger):
        with pytest.raises(ProblemError):
            validate_entry(definition, replace(good, fields=fields), units=UNITS, who=who())


def test_interpretation_origin_is_required_allowed_and_not_allowed_on_results() -> None:
    definition = form("radiation_dose")
    good = entry(definition)
    result = good.fields[0]
    for interpretation in (
        FieldEntry("interpretation", "x", None, None, None, None),
        FieldEntry("interpretation", "x", None, None, None, "extraction"),
    ):
        with pytest.raises(ProblemError):
            validate_entry(definition, replace(good, fields=(result, interpretation)), units=UNITS, who=who())
    with pytest.raises(ProblemError):
        validate_entry(
            definition,
            replace(good, fields=(replace(result, origin="clinician"), good.fields[1])),
            units=UNITS,
            who=who(),
        )
    numeric_interpretation = FieldEntry("interpretation", Decimal(3), None, None, None, "clinician")
    with pytest.raises(ProblemError):
        validate_entry(
            definition, replace(good, fields=(result, numeric_interpretation)), units=UNITS, who=who()
        )


def test_reference_interval_is_required_when_the_form_says_so_and_must_be_ordered() -> None:
    definition = form("lipase", reference_interval=True)
    ok = validate_entry(definition, entry(definition), units=UNITS, who=who())[0]
    assert ok.reference_interval == (Decimal(1), Decimal(20))
    good = entry(definition)
    for low, high in (
        (None, None),
        (Decimal(1), None),
        (Decimal(20), Decimal(1)),
        (Decimal("NaN"), Decimal(2)),
    ):
        item = FieldEntry("result", Decimal(5), "mmol/L", low, high, None)
        with pytest.raises(ProblemError):
            validate_entry(definition, replace(good, fields=(item, good.fields[1])), units=UNITS, who=who())


def test_definition_invariants() -> None:
    base = form("lipase")
    result, interpretation = base.fields
    unsourced = replace(result.policy, source_ref="")
    bad_changes: list[dict[str, object]] = [
        {"fields": ()},
        {"version": ""},
        {"source_ref": ""},
        {"fields": (result, result)},
        {"fields": (replace(interpretation, policy=numeric("x", "mmol/L")),)},
        {"fields": (replace(interpretation, reference_interval_required=True),)},
        {"fields": (replace(result, policy=unsourced),)},
    ]
    for changes in bad_changes:
        with pytest.raises(ProblemError):
            replace(base, **changes)  # type: ignore[arg-type]


def test_a_configuration_edit_changes_validation_with_no_code_change() -> None:
    """Rule 1: the same code accepts or refuses an entry as the definition (the document) changes."""
    definition = form("radiation_dose")
    subject = entry(definition)
    assert validate_entry(definition, subject, units=UNITS, who=who())
    stricter = replace(definition, required_attributes=definition.required_attributes | {"dose_rate"})
    with pytest.raises(ProblemError):
        validate_entry(stricter, subject, units=UNITS, who=who())
    other_unit = replace(
        definition,
        fields=(replace(definition.fields[0], policy=numeric("radiation_dose", "uSv")), definition.fields[1]),
    )
    with pytest.raises(ProblemError):
        validate_entry(other_unit, subject, units=UNITS, who=who())
    relabelled = replace(definition, interpretation_origins=frozenset({"device"}))
    with pytest.raises(ProblemError):
        validate_entry(relabelled, subject, units=UNITS, who=who())


@given(
    st.one_of(st.decimals(allow_nan=True, allow_infinity=True), st.integers(-5, 5)),
    st.sampled_from(["mGy", "uSv", "", "x"]),
)
def test_numeric_result_is_accepted_iff_finite_and_in_an_accepted_unit(
    value: Decimal | int, unit: str
) -> None:
    definition = form("radiation_dose")
    item = FieldEntry("result", value, unit or None, None, None, None)
    subject = replace(entry(definition), fields=(item,))
    finite = isinstance(value, int) or value.is_finite()
    try:
        findings = validate_entry(definition, subject, units=UNITS, who=who())
    except ProblemError:
        assert not (finite and unit == "mGy")
        return
    assert finite and unit == "mGy" and findings[0].unit == "mGy"


def test_attributes_must_be_named_unique_and_valued_and_an_empty_entry_is_refused() -> None:
    definition = form("radiation_dose")
    good = entry(definition)
    for attributes in (good.attributes + (good.attributes[0],), (("instrument", ""),) + good.attributes[1:]):
        with pytest.raises(ProblemError):
            validate_entry(definition, replace(good, attributes=attributes), units=UNITS, who=who())
    optional = replace(definition, fields=(replace(definition.fields[0], required=False),))
    with pytest.raises(ProblemError):
        validate_entry(optional, replace(good, fields=()), units=UNITS, who=who())
    assert "radiation_dose" in repr(good) and "12.5" not in repr(good)
