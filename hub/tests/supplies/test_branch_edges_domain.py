"""Edge cases of the pure supplies domain: exact quantities, admission, the activation guard, cover, the what-if
overlay, the release checks and the typed document readers. Each refusal is asserted by its check ID and pointer
(E32 3.3, 8.1, 8.3)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from crew_health.kernel.units import Quantity, Unit
from crew_health.supplies.domain.config_checks import release_findings
from crew_health.supplies.domain.cover import cover_uses, largest_per_use
from crew_health.supplies.domain.document import (
    read_kit_supplies,
    read_plans,
    read_threshold,
    read_words,
)
from crew_health.supplies.domain.errors import UnitMismatch
from crew_health.supplies.domain.guard import UsedTotal, guard_findings
from crew_health.supplies.domain.ledger import admit, check_chain
from crew_health.supplies.domain.model import Need, SupplyDef
from crew_health.supplies.domain.overlay import added_row
from crew_health.supplies.domain.quantity import decimal_text, floor_ratio, parse_decimal_text

TOWEL = Unit("{towel}")
TOWELS = SupplyDef("towels", "Towels", "towel", TOWEL, True)
STOCK = "/stock/lunar_surface/0"


def q(value: object, unit: Unit = TOWEL) -> Quantity:
    return Quantity(Decimal(str(value)), unit)


def need(per_use: object, unit: Unit = TOWEL) -> Need:
    return Need("plan_eye_injury", "eye_rinse", "towels", q(per_use, unit), "/plans/0")


def kit_doc() -> dict[str, Any]:
    """A kit map that reads with no issue: one consumable supply with a set packed quantity."""
    return {
        "version": "kit-1",
        "example": True,
        "supplies": [{"supply_id": "towels", "name": "Towels", "unit": "towel", "consumable": True}],
        "stock": {
            "lunar_surface": [
                {
                    "supply_id": "towels",
                    "quantity": Decimal(20),
                    "basis": "team default",
                    "to_be_set_by": "the flight surgeon",
                }
            ]
        },
        "candidate_supplies": [],
        "candidate_skills": [],
    }


def found(doc: object) -> set[tuple[str, str]]:
    return {(i.check_id, i.pointer) for i in read_kit_supplies(doc)[1]}


# ---- quantity ----


@pytest.mark.trace("E32 3", "DD1")
@pytest.mark.parametrize(("numerator", "denominator"), [("1", "0"), ("1", "-1"), ("-1", "1")])
def test_floor_ratio_refuses_a_negative_numerator_or_a_denominator_not_above_zero(
    numerator: str, denominator: str
) -> None:
    with pytest.raises(ValueError, match="non negative numerator"):
        floor_ratio(q(numerator), q(denominator))


@pytest.mark.trace("E32 3", "DD1", "INV-S6")
def test_floor_ratio_corrects_a_quotient_rounded_up_to_the_next_integer() -> None:
    # 40 nines after the point: at precision 34 the quotient rounds up to exactly 1, the exact floor is 0.
    just_below_one = q("0." + "9" * 40)
    assert floor_ratio(just_below_one, q(1)) == 0
    assert floor_ratio(q("2." + "9" * 40), q(1)) == 2


@pytest.mark.trace("E32 5.1", "rule 5")
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_decimal_text_never_writes_a_non_finite_value(value: Decimal) -> None:
    with pytest.raises(ValueError, match="non finite"):
        decimal_text(value)


@pytest.mark.trace("E32 5.1", "INV-S12")
@pytest.mark.parametrize("text", ["NaN", "Infinity", "1E+2", "1.50", "01", "-0"])
def test_a_stored_decimal_that_is_not_canonical_text_is_corrupt(text: str) -> None:
    with pytest.raises(ValueError, match="not canonical"):
        parse_decimal_text(text)
    assert parse_decimal_text("1.5") == Decimal("1.5")


# ---- admission and the ledger chain ----


@pytest.mark.trace("INV-S9", "rule 8")
def test_a_use_in_another_unit_is_refused_before_any_stock_is_read() -> None:
    with pytest.raises(UnitMismatch) as caught:
        admit(TOWELS, None, None, q(1, Unit("{rinse}")))
    assert (caught.value.left, caught.value.right) == ("{rinse}", "{towel}")


@pytest.mark.trace("INV-S9", "rule 5")
@pytest.mark.parametrize("amount", ["0", "-1"])
def test_a_use_of_nothing_or_a_negative_amount_is_never_admitted(amount: str) -> None:
    doc = kit_doc()
    kit, _ = read_kit_supplies(doc)
    with pytest.raises(ValueError, match="positive"):
        admit(TOWELS, kit.stock["lunar_surface"]["towels"], None, q(amount))


@pytest.mark.trace("INV-S12", "E32 5.1")
def test_an_empty_chain_with_no_previous_total_is_broken() -> None:
    with pytest.raises(ValueError, match="empty chain"):
        check_chain([], None)
    assert check_chain([], (3, q(4))) == (3, q(4))  # a verified prefix with no new rows keeps its total


# ---- the activation guard ----


@pytest.mark.trace("S-A2", "S-A3", "E32-T-17")
def test_the_guard_skips_zero_totals_reports_a_removed_used_supply_and_leaves_unset_packed_alone() -> None:
    doc = kit_doc()
    doc["stock"]["lunar_surface"][0]["quantity"] = None  # not set: nothing to compare `used` against
    kit, issues = read_kit_supplies(doc)
    assert issues == ()
    totals = [
        # more than any packed number, but packed is unset: nothing to compare against
        UsedTotal("lunar_surface", "SIM", "towels", q(50)),
        UsedTotal("lunar_surface", "SIM", "dressings", q(0, Unit("{dressing}"))),  # no use: removal is fine
        UsedTotal("lunar_surface", "LIVE", "oxygen_mask", q(1, Unit("{mask}"))),  # used, then removed
    ]
    found_issues = guard_findings(kit, {"towels": "{towel}"}, totals)
    assert [(i.check_id, i.pointer, i.message) for i in found_issues] == [
        ("S-A2", "/supplies", "oxygen_mask: used supply removed")
    ]


# ---- cover ----


@pytest.mark.trace("INV-S6", "rule 8")
def test_the_largest_per_use_keeps_the_first_largest_and_refuses_another_unit() -> None:
    assert largest_per_use(TOWELS, [need(2), need(1), need(2)]) == q(2)
    with pytest.raises(UnitMismatch):
        largest_per_use(TOWELS, [need(1), need(1, Unit("{rinse}"))])


@pytest.mark.trace("INV-S6", "INV-S9")
def test_a_negative_quantity_now_never_reaches_a_cover_number() -> None:
    with pytest.raises(ValueError, match="negative quantity_now"):
        cover_uses(TOWELS, True, q(-1), q(1))


# ---- the what-if overlay ----


@pytest.mark.trace("INV-S11")
@pytest.mark.parametrize("amount", ["0", "-1"])
def test_the_what_if_overlay_never_lowers_stock(amount: str) -> None:
    with pytest.raises(ValueError, match="above zero"):
        added_row(TOWELS, None, q(amount), "team default")


# ---- release checks S-L1 to S-L7 ----


@pytest.mark.trace("S-L7", "S-L2", "E32 8.3")
def test_absent_documents_are_checked_only_where_the_kit_map_needs_them() -> None:
    doc = kit_doc()
    doc["candidate_skills"] = ["cpr"]
    findings = {(i.check_id, i.document, i.pointer) for i in release_findings(doc, None, None, None, None)}
    assert findings == {
        ("S-L7", "ui-copy", "/supply_words"),
        ("S-L2", "kit-map", "/candidate_skills"),
    }
    empty = kit_doc()
    empty["supplies"], empty["stock"] = [], {}
    assert release_findings(empty, None, None, None, None) == ()


@pytest.mark.trace("S-L2", "E32 8.3")
@pytest.mark.parametrize("catalog", [["cpr"], "skills", {"skills": "cpr"}])
def test_a_skills_catalog_that_lists_no_skills_leaves_every_candidate_skill_unlisted(catalog: object) -> None:
    doc = kit_doc()
    doc["candidate_skills"] = ["cpr", "pulse_check_wrist"]
    words = {
        "supply_words": {
            "cover": "cover for {n} uses",
            "cover_unknown": "cover not set",
            "not_used": "no plan uses it",
            "not_aboard": "Not aboard",
            "not_consumable": "kept, not used up",
        }
    }
    findings = [(i.check_id, i.pointer) for i in release_findings(doc, None, None, words, catalog)]
    assert findings == [("S-L2", "/candidate_skills/0"), ("S-L2", "/candidate_skills/1")]


@pytest.mark.trace("S-L5", "E32 8.3")
def test_a_drill_that_needs_a_supply_the_kit_map_does_not_list_is_reported() -> None:
    catalog = {
        "skills": [
            {"skill_id": "cpr", "drill_scenarios": [{"needs_supplies": ["towels", "cpr_manikin"]}]},
            "not a skill",
        ]
    }
    findings = [
        (i.check_id, i.document, i.pointer) for i in release_findings(kit_doc(), None, None, None, catalog)
    ]
    assert ("S-L5", "skills-catalog", "/skills/0/drill_scenarios/0/needs_supplies/1") in findings
    assert not any(pointer.endswith("needs_supplies/0") for _, _, pointer in findings)  # towels is listed


# ---- typed document readers ----


@pytest.mark.trace("E32 8.1", "S-DOC")
def test_a_kit_map_that_is_not_an_object_is_refused_at_the_root() -> None:
    kit, issues = read_kit_supplies(["not", "an", "object"])
    assert ("S-DOC", "") in {(i.check_id, i.pointer) for i in issues}
    assert kit.supplies == () and kit.stock == {}


@pytest.mark.trace("E32 8.1", "S-DOC", "fail closed")
def test_supply_fields_of_the_wrong_type_are_reported_and_never_read_as_consumable() -> None:
    doc = kit_doc()
    doc["supplies"][0]["consumable"] = "yes"
    doc["supplies"].append({"supply_id": 7, "name": "Seven", "unit": "x", "consumable": True})
    kit, issues = read_kit_supplies(doc)
    assert {(i.check_id, i.pointer) for i in issues} == {
        ("S-DOC", "/supplies/0/consumable"),
        ("S-DOC", "/supplies/1/supply_id"),
    }
    assert kit.supplies[0].consumable is False  # an unreadable flag never makes a supply countable


@pytest.mark.trace("E32 8.1", "S-DOC", "rule 5")
@pytest.mark.parametrize(
    ("quantity", "message"),
    [
        ("20", "expected a number"),
        (True, "expected a number"),
        (Decimal("Infinity"), "expected a finite number"),
        (Decimal("NaN"), "expected a finite number"),
        (Decimal(-1), "packed quantity below zero"),
    ],
)
def test_a_packed_quantity_that_is_not_a_finite_non_negative_number_is_never_packed(
    quantity: object, message: str
) -> None:
    doc = kit_doc()
    doc["stock"]["lunar_surface"][0]["quantity"] = quantity
    kit, issues = read_kit_supplies(doc)
    assert [(i.check_id, i.pointer, i.message) for i in issues] == [("S-DOC", STOCK + "/quantity", message)]
    assert kit.stock["lunar_surface"]["towels"].packed is None


@pytest.mark.trace("INV-S3", "S-L3")
def test_a_null_packed_quantity_with_who_sets_it_is_valid_and_stays_unset() -> None:
    doc = kit_doc()
    doc["stock"]["lunar_surface"][0]["quantity"] = None
    kit, issues = read_kit_supplies(doc)
    assert issues == ()
    assert kit.stock["lunar_surface"]["towels"].packed is None


@pytest.mark.trace("S-L3", "S-L4", "S-DOC")
@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("basis", "guess", {("S-DOC", STOCK + "/basis")}),
        ("basis", "sourced", {("S-L4", STOCK + "/basis")}),
        ("to_be_set_by", 5, {("S-DOC", STOCK + "/to_be_set_by"), ("S-L3", STOCK + "/to_be_set_by")}),
        ("to_be_set_by", None, {("S-L3", STOCK + "/to_be_set_by")}),
    ],
)
def test_stock_row_provenance_is_checked(field: str, value: object, expected: set[tuple[str, str]]) -> None:
    doc = kit_doc()
    doc["stock"]["lunar_surface"][0][field] = value
    assert found(doc) == expected


@pytest.mark.trace("S-L1", "S-L2")
def test_a_second_stock_row_and_an_unlisted_candidate_supply_are_reported() -> None:
    doc = kit_doc()
    second = dict(doc["stock"]["lunar_surface"][0], quantity=Decimal(99))
    doc["stock"]["lunar_surface"].append(second)
    doc["candidate_supplies"] = ["oxygen_mask"]
    kit, issues = read_kit_supplies(doc)
    assert {(i.check_id, i.pointer) for i in issues} == {
        ("S-L1", "/stock/lunar_surface/1/supply_id"),
        ("S-L2", "/candidate_supplies/0"),
    }
    assert kit.stock["lunar_surface"]["towels"].packed == q(20)  # the first row stands, the second is refused


@pytest.mark.trace("E32 8.1", "S-DOC", "INV-S6")
@pytest.mark.parametrize("per_use", [Decimal(0), Decimal(-1)])
def test_a_per_use_amount_not_above_zero_is_reported_and_never_divides_cover(per_use: Decimal) -> None:
    doc = {
        "plans": [
            {
                "plan_id": "plan_eye_injury",
                "environments": ["lunar_surface"],
                "stages": [
                    {
                        "steps": [
                            {
                                "step_id": "eye_rinse",
                                "needs": {"supplies": [{"supply_id": "towels", "per_use": per_use}]},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    plans, issues = read_plans(doc)
    pointer = "/plans/0/stages/0/steps/0/needs/supplies/0"
    assert [(i.check_id, i.pointer) for i in issues] == [("S-DOC", pointer + "/per_use")]
    assert plans[0].steps[0].needs == (("towels", None, pointer),)


def _rules(*below: object) -> dict[str, object]:
    return {
        "definitions": [
            {"id": "supply_cover_low", "condition": {"below_uses": b}, "environments": ["lunar_surface"]}
            for b in below
        ]
    }


@pytest.mark.trace("S-L6", "INV-S8")
@pytest.mark.parametrize("below", [True, -1, "3", Decimal("2.5")])
def test_a_below_uses_that_is_not_a_whole_number_of_at_least_zero_is_reported_and_inactive(
    below: object,
) -> None:
    threshold, issues = read_threshold(_rules(below))
    assert [(i.check_id, i.pointer) for i in issues] == [("S-L6", "/definitions/0/condition/below_uses")]
    assert threshold is not None and threshold.below_uses is None  # never low on an unreadable number


@pytest.mark.trace("S-L6")
@pytest.mark.parametrize("below", [(), (2, 3)])
def test_the_cover_threshold_is_defined_exactly_once(below: tuple[object, ...]) -> None:
    threshold, issues = read_threshold(_rules(*below))
    assert threshold is None
    assert [(i.check_id, i.pointer) for i in issues] == [("S-L6", "/definitions")]


@pytest.mark.trace("S-L7", "E32 8.1")
def test_missing_supply_words_give_no_words_and_one_issue_per_missing_key() -> None:
    words, issues = read_words({"supply_words": {"cover": "cover for {n} uses", "not_used": ""}})
    assert words is None
    assert [i.pointer for i in issues] == [
        "/supply_words/cover_unknown",
        "/supply_words/not_used",
        "/supply_words/not_aboard",
        "/supply_words/not_consumable",
    ]
