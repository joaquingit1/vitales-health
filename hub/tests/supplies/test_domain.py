"""Pure supplies rules: cover, admission, units, bounds, the what-if overlay and the release checks (E32 3.3, 8.3)."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health.kernel.units import Quantity, Unit
from crew_health.supplies.application.ports import StockLevel, StockUnitMismatch, with_added
from crew_health.supplies.domain.config_checks import release_findings
from crew_health.supplies.domain.cover import compute_row, cover_text_key, cover_uses
from crew_health.supplies.domain.errors import SupplyBelowZero, UnitMismatch
from crew_health.supplies.domain.ledger import StepUse, admit, admit_step, check_chain
from crew_health.supplies.domain.model import Head, Need, StockRow, SupplyDef, Threshold
from crew_health.supplies.domain.overlay import added_row
from crew_health.supplies.domain.quantity import (
    MAX_COVER,
    annotation_unit,
    decimal_text,
    floor_ratio,
    parse_decimal_text,
    within_bounds,
)

from .harness import example

RINSE = Unit("{rinse}")
WATER = SupplyDef("eye_rinse_water", "Clean water for an eye rinse", "rinse", RINSE, True)
amounts = st.decimals(min_value=Decimal("0.001"), max_value=Decimal(1000), places=3)


def q(value: object, unit: Unit = RINSE) -> Quantity:
    return Quantity(Decimal(str(value)), unit)


def row(packed: object | None) -> StockRow:
    return StockRow(
        "eye_rinse_water", None if packed is None else q(packed), "team default", None, "/stock/x"
    )


def need(per_use: object | None, step: str = "eye_rinse") -> Need:
    return Need(
        "plan_eye_injury", step, "eye_rinse_water", None if per_use is None else q(per_use), "/plans/0"
    )


@pytest.mark.trace("E32-T-06", "INV-S7")
@pytest.mark.parametrize("aboard", [True, False])
@pytest.mark.parametrize("consumable", [True, False])
@pytest.mark.parametrize("has_need", [True, False])
@pytest.mark.parametrize("uses", [None, 3])
def test_cover_text_key_takes_the_first_match_of_all_sixteen_combinations(
    aboard: bool, consumable: bool, has_need: bool, uses: int | None
) -> None:
    expected = (
        "not_aboard"
        if not aboard
        else "not_consumable"
        if not consumable
        else "not_used"
        if not has_need
        else "cover_unknown"
        if uses is None
        else "cover"
    )
    assert cover_text_key(aboard, consumable, has_need, uses) == expected


@pytest.mark.trace("E32-T-04", "INV-S2", "INV-S6", "INV-S9", "INV-S12")
@settings(max_examples=150, deadline=None)
@given(packed=st.decimals(min_value=0, max_value=200, places=2), uses=st.lists(amounts, max_size=25))
def test_admission_never_takes_a_set_quantity_below_zero_and_totals_are_exact_sums(
    packed: Decimal, uses: list[Decimal]
) -> None:
    head: Head | None = None
    chain: list[tuple[int, Quantity, Quantity]] = []
    accepted = Decimal(0)
    for amount in uses:
        try:
            result = admit(WATER, row(packed), head, q(amount))
        except SupplyBelowZero:
            assert packed - accepted - amount < 0
            continue
        accepted += amount
        assert result.used_after == q(accepted)
        head = Head(result.seq, result.used_after, "sup", None)
        chain.append((result.seq, result.quantity, result.used_after))
        assert packed - accepted >= 0
    if chain:
        assert check_chain(chain, None) == (len(chain), q(accepted))
    shown = compute_row(WATER, row(packed), head, [need(1)], None, "lunar_surface")
    left = packed - accepted
    assert shown.quantity_now == q(left)
    assert shown.aboard is (left > 0)  # INV-S4: none left is not aboard, so it has no cover
    assert shown.cover_uses == (int(left // 1) if left > 0 else None)


@pytest.mark.trace("E32-T-04", "INV-S6")
@given(now=st.decimals(min_value=0, max_value=10**6, places=4), per_use=amounts)
def test_cover_is_the_exact_floor_of_quantity_over_the_largest_per_use(
    now: Decimal, per_use: Decimal
) -> None:
    uses = cover_uses(WATER, True, q(now), q(per_use))
    assert uses is not None and uses * per_use <= now < (uses + 1) * per_use


@pytest.mark.trace("INV-S6", "INV-S9")
def test_null_stock_or_per_use_gives_null_cover_and_still_admits_the_use() -> None:
    assert compute_row(WATER, row(None), None, [need(1)], None, "x").cover_uses is None
    assert compute_row(WATER, row(6), None, [need(None)], None, "x").cover_text_key == "cover_unknown"
    assert admit(WATER, row(None), None, q(5)).used_after == q(5)
    with pytest.raises(SupplyBelowZero):
        admit(WATER, None, None, q(1))  # no stock row here at all


@pytest.mark.trace("E32-T-07", "INV-S10")
def test_step_admission_is_all_or_nothing_and_adds_two_needs_of_one_supply() -> None:
    towel_unit = Unit("{towel}")
    towels = SupplyDef("towels", "Towels", "towel", towel_unit, True)
    rows = {"eye_rinse_water": row(6), "towels": StockRow("towels", q(1, towel_unit), "b", None, "/p")}
    with pytest.raises(SupplyBelowZero):
        admit_step([StepUse(WATER, q(1)), StepUse(towels, q(2, towel_unit))], rows, {})
    done = admit_step([StepUse(WATER, q(1)), StepUse(WATER, q(2))], rows, {})
    assert [a.used_after for a in done] == [q(1), q(3)]


@pytest.mark.trace("E32-T-18", "INV-S13")
@given(value=amounts, word=st.sampled_from(["set", "towel", "dressing"]))
def test_mixed_units_never_coerce(value: Decimal, word: str) -> None:
    other = annotation_unit(word)
    assert other is not None and other != RINSE
    with pytest.raises(UnitMismatch):
        admit(WATER, row(6), None, Quantity(value, other))
    with pytest.raises(UnitMismatch):
        floor_ratio(q(6), Quantity(value, other))


@pytest.mark.trace("E32-T-22", "V92")
def test_quantity_bounds_and_a_cover_past_two_to_the_53_are_refused() -> None:
    assert within_bounds(Decimal("1.5"), maximum=Decimal(10), max_scale=1)
    assert not within_bounds(Decimal("1.55"), maximum=Decimal(10), max_scale=1)
    assert not within_bounds(Decimal(11), maximum=Decimal(10), max_scale=1)
    with pytest.raises(OverflowError):
        cover_uses(WATER, True, q(MAX_COVER + 1), q(1))
    assert cover_uses(WATER, True, q(MAX_COVER), q(1)) == MAX_COVER


@pytest.mark.trace("DD1")
@given(value=st.decimals(min_value=0, max_value=10**9, places=6))
def test_decimal_text_round_trips_without_exponent(value: Decimal) -> None:
    text = decimal_text(value)
    assert "E" not in text and "e" not in text
    assert parse_decimal_text(text) == value


@pytest.mark.trace("E32-T-23", "INV-S11")
@given(packed=st.one_of(st.none(), st.decimals(min_value=0, max_value=1000, places=2)), add=amounts)
def test_the_overlay_raises_packed_keeps_null_and_never_touches_used(
    packed: Decimal | None, add: Decimal
) -> None:
    before = row(packed)
    after = added_row(WATER, before, q(add), "team default")
    if packed is None:
        assert after.packed is None
    else:
        assert after.packed == q(packed + add)
    assert added_row(WATER, None, q(add), "team default").packed == q(add)
    level = {
        "eye_rinse_water": StockLevel("eye_rinse_water", True, None if packed is None else q(packed), RINSE)
    }
    grown = with_added(level, [("eye_rinse_water", q(add))])["eye_rinse_water"]
    assert grown.quantity_now is None if packed is None else grown.quantity_now == q(packed + add)
    with pytest.raises(StockUnitMismatch):
        with_added(level, [("eye_rinse_water", Quantity(add, Unit("{towel}")))])


def _docs() -> dict[str, object]:
    return {
        "kit": example("kit-map.example.json"),
        "plans": example("config/onboard-plans.example.json"),
        "rules": example("config/warning-rules.example.json"),
        "copy": example("config/ui-copy.example.json"),
        "skills": example("config/skills-catalog.example.json"),
    }


def _findings(docs: dict[str, object]) -> set[str]:
    issues = release_findings(docs["kit"], docs["plans"], docs["rules"], docs["copy"], docs["skills"])
    return {i.check_id for i in issues}


@pytest.mark.trace("E32-T-32", "E32-01")
def test_release_checks_pass_on_the_package_examples() -> None:
    assert _findings(_docs()) == set()


@pytest.mark.trace("E32-T-32", "E32-01")
def test_release_checks_fail_on_crafted_documents() -> None:
    base = _docs()
    duplicate = copy.deepcopy(base)
    kit = duplicate["kit"]
    assert isinstance(kit, dict)
    kit["supplies"].append(dict(kit["supplies"][0]))
    assert _findings(duplicate)
    unknown_need = copy.deepcopy(base)
    plans = unknown_need["plans"]
    assert isinstance(plans, dict)
    step = plans["plans"][0]["stages"][1]["steps"][0]
    step.setdefault("needs", {"instruments": [], "supplies": []})["supplies"].append(
        {"supply_id": "not_in_the_kit_map", "per_use": 1, "basis": "team default", "to_be_set_by": None}
    )
    assert _findings(unknown_need)
    bad_unit = copy.deepcopy(base)
    kit = bad_unit["kit"]
    assert isinstance(kit, dict)
    kit["supplies"][0]["unit"] = "two words"
    assert _findings(bad_unit)


@pytest.mark.trace("E32-T-32", "INV-S8")
def test_a_zero_low_threshold_is_valid_and_raises_nothing() -> None:
    docs = _docs()
    rules = docs["rules"]
    assert isinstance(rules, dict)
    for definition in rules["definitions"]:
        if definition.get("id") == "supply_cover_low":
            definition["condition"]["below_uses"] = 0
    assert _findings(docs) == set()
    threshold = Threshold(0, frozenset({"lunar_surface"}))
    shown = compute_row(WATER, row("0.5"), None, [need(1)], threshold, "lunar_surface")
    assert shown.cover_uses == 0 and not shown.low  # nothing is below zero uses
