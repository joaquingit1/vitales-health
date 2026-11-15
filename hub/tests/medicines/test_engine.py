"""Dose engine properties and closed refusal codes (E32 3.5, 3.6; T-51, T-52, T-53). The Hypothesis properties check
INV-M1 against the label limits read by hand (LBL01: 650 mg a dose, 3250 mg in 24 hours), never against the
configuration the engine read."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given as hypothesis_given
from hypothesis import settings
from hypothesis import strategies as st

from crew_health.medicines.domain.engine import exclusions, propose
from crew_health.medicines.domain.model import (
    REFUSAL_CODES,
    DoseContext,
    DoseRequest,
    IngredientRule,
    Mode,
    WeightObservation,
    WeightPolicy,
)
from crew_health.medicines.domain.units import Amount

from .harness import NOW
from .kit import ask, assessed, complete_context, given, lot, lots, part, past, with_policy

APAP_MAX_DOSE_MG = Decimal(650)  # LBL01 "take 2 tablets" of 325 mg
APAP_MAX_24H_MG = Decimal(3250)  # LBL01 "not more than 10 tablets in 24 hours"
INTERVAL = timedelta(hours=4)  # LBL01, N18 p. 5-3


def line_amount(proposal: object) -> Decimal:
    amount = getattr(proposal, "amount", None)
    assert amount is not None
    return Decimal(amount.value)


@st.composite
def apap_histories(draw: st.DrawFn) -> list[tuple[float, int]]:
    count = draw(st.integers(min_value=0, max_value=10))
    return [
        (
            draw(st.floats(min_value=0.0, max_value=36.0, allow_nan=False)),
            draw(st.sampled_from([325, 650, 975])),
        )
        for _ in range(count)
    ]


@pytest.mark.trace("INV-M1", "T-52")
@settings(max_examples=300, deadline=None)
@hypothesis_given(
    history=apap_histories(),
    median=st.decimals(min_value=Decimal("0.80"), max_value=Decimal("1.10"), places=3),
    spread=st.decimals(min_value=Decimal("0"), max_value=Decimal("0.10"), places=3),
    adjust=st.booleans(),
    stock=st.integers(min_value=0, max_value=4),
)
def test_no_proposal_ever_exceeds_a_dose_or_the_24_hour_limit(
    history: list[tuple[float, int]], median: Decimal, spread: Decimal, adjust: bool, stock: int
) -> None:
    summary = (
        assessed("adjust", str(median - spread), str(median), str(median + spread), "acetaminophen")
        if adjust
        else None
    )
    views = (lot("lot-apap-001", "acetaminophen_325_tab", quantity=stock, summary=summary),)
    rows = [past(h, "acetaminophen", mg, rule_id="apap_pain", n=n) for n, (h, mg) in enumerate(history)]
    proposal = propose(ask("acetaminophen_325_tab", "ICL63"), given(history=rows, lot_views=views))
    if proposal.outcome != "proposal":
        assert proposal.refusal is not None and proposal.refusal.code in REFUSAL_CODES
        return
    assert proposal.amount is not None
    (line,) = proposal.ingredients
    upper = (
        max(Decimal(1), median + spread) if adjust else Decimal(1)
    )  # E32 3.6 Acc_i(a) = a * S_i * max(1, u_i)
    assert line.accounting.value == proposal.amount.value * Decimal(325) * upper
    used = sum((Decimal(mg) for h, mg in history if 0 <= h < 24), Decimal(0))
    assert line.accounting.value <= APAP_MAX_DOSE_MG * upper
    assert used + line.accounting.value <= APAP_MAX_24H_MG
    assert proposal.amount.value <= stock  # INV-M4: stock never below zero
    assert all(h >= INTERVAL.total_seconds() / 3600 for h, _ in history)  # INV-M3


@pytest.mark.trace("INV-M1", "T-52")
@settings(max_examples=200, deadline=None)
@hypothesis_given(
    median=st.decimals(min_value=Decimal("0.50"), max_value=Decimal("1.20"), places=3),
    high=st.decimals(min_value=Decimal("0"), max_value=Decimal("0.30"), places=3),
)
def test_a_compensated_proposal_counts_the_upper_potency_against_the_limits(
    median: Decimal, high: Decimal
) -> None:
    summary = assessed(
        "adjust", str(median - Decimal("0.05")), str(median), str(median + high), "promethazine"
    )
    views = (lot("lot-pmz-tab-001", "promethazine_25_tab", summary=summary),)
    rows = [past(10, "promethazine", 25, rule_id="pmz_nausea", n=n) for n in range(3)]
    ctx = with_policy(given(history=rows, lot_views=views), mode="compensate")
    products = dict(ctx.treatments.products)  # a loose tolerance so compensated amounts exist at all
    products["promethazine_25_tab"] = replace(
        products["promethazine_25_tab"], rounding_tolerance_fraction=Decimal(1)
    )
    ctx = replace(ctx, treatments=replace(ctx.treatments, products=products))
    proposal = propose(ask("promethazine_25_tab", "ICL97"), ctx)
    if median + high > Decimal(1):  # Acc passes 25 mg at max(1, u) > 1; 75 mg is already used
        assert proposal.outcome == "refusal"
    if proposal.outcome == "proposal":
        assert proposal.path == "compensated"
        (line,) = proposal.ingredients
        assert Decimal(75) + line.accounting.value <= Decimal(100)  # N18 p. 6-10: 100 mg in 24 hours
        assert line.accounting.value == line_amount(proposal) * Decimal(25) * max(Decimal(1), median + high)


def _refusal(proposal: object) -> str:
    refusal = getattr(proposal, "refusal", None)
    assert refusal is not None, "expected a refusal"
    return str(refusal.code)


def _weighted() -> tuple[DoseContext, IngredientRule]:
    """apap_pain rewritten per kilogram with sourced-looking bounds, only to reach the weight refusals."""
    base = given()
    rule = base.treatments.rules["apap_pain"]
    ing = replace(rule.ingredients[0], dose=None, dose_per_kg=Amount(Decimal(10), "mg"))
    rules = dict(base.treatments.rules)
    rules["apap_pain"] = replace(rule, ingredients=(ing,))
    weight = WeightPolicy(Decimal(40), Decimal(150), 86400 * 30, Decimal("0.1"), 86400 * 7)
    return replace(base, treatments=replace(base.treatments, rules=rules, weight=weight)), ing


@pytest.mark.trace("T-53")
def test_every_refusal_code_is_reachable_from_a_minimal_input() -> None:
    apap, headache = "acetaminophen_325_tab", "ICL63"
    seen: dict[str, object] = {}
    base = given()
    seen["no_working_diagnosis"] = propose(DoseRequest("person_a", None, apap, None, False), base)
    seen["rule_not_found"] = propose(ask("aspirin_325_tab", headache), base)
    seen["rule_not_reviewed_live"] = propose(ask(apap, headache), given(mode="LIVE"))
    products = dict(base.treatments.products)
    products.pop(apap)
    seen["formulary_mismatch"] = propose(
        ask(apap, headache), replace(base, treatments=replace(base.treatments, products=products))
    )
    seen["allergy_list_unknown"] = propose(
        ask(apap, headache), given(context=complete_context(allergies=None))
    )
    seen["patient_context_missing"] = propose(
        ask(apap, headache), given(context=complete_context(other_medicines=None))
    )
    weighted, _ = _weighted()
    seen["weight_missing"] = propose(ask(apap, headache), weighted)
    old = WeightObservation("obs_old", Decimal(70), NOW - timedelta(days=31))
    seen["weight_stale"] = propose(ask(apap, headache), replace(weighted, weights=(old,)))
    light = WeightObservation("obs_light", Decimal(20), NOW - timedelta(days=1))
    seen["weight_implausible"] = propose(ask(apap, headache), replace(weighted, weights=(light,)))
    jump = (
        WeightObservation("obs_1", Decimal(70), NOW - timedelta(days=3)),
        WeightObservation("obs_2", Decimal(80), NOW - timedelta(days=1)),
    )
    seen["weight_change_unconfirmed"] = propose(ask(apap, headache), replace(weighted, weights=jump))
    weighted_ok = replace(
        weighted, weights=(WeightObservation("obs_ok", Decimal(65), NOW - timedelta(days=1)),)
    )
    assert propose(ask(apap, headache), weighted_ok).outcome == "proposal"
    wrong_unit = dict(base.treatments.products)
    wrong_unit[apap] = replace(wrong_unit[apap], deliverable_increment=Amount(Decimal(1), "mL"))
    seen["unit_incompatible"] = propose(
        ask(apap, headache), replace(base, treatments=replace(base.treatments, products=wrong_unit))
    )
    seen["clock_discontinuity"] = propose(ask(apap, headache), given(clock_discontinuity=True))
    seen["min_interval_not_passed"] = propose(
        ask(apap, headache), given(history=[past(1, "acetaminophen", 650, rule_id="apap_pain")])
    )
    seen["course_ended"] = propose(
        ask(apap, headache), given(history=[past(241, "acetaminophen", 650, rule_id="apap_pain")])
    )
    expired = lot("lot-apap-001", apap, expiry=NOW - timedelta(days=1))
    seen["no_usable_lot"] = propose(ask(apap, headache), given(lot_views=(expired,)))
    adjust = lot("lot-apap-001", apap, summary=assessed("adjust", "0.9", "0.95", "1.0", "acetaminophen"))
    seen["potency_adjustment_disabled"] = propose(
        ask(apap, headache), with_policy(given(lot_views=(adjust,)), mode="information_only")
    )
    ineligible = lot(
        "lot-apap-001",
        apap,
        summary=assessed("adjust", "0.9", "0.95", "1.0", "acetaminophen", eligible=False),
    )
    seen["adjustment_not_permitted"] = propose(ask(apap, headache), given(lot_views=(ineligible,)))
    strong = lot(
        "lot-pmz-tab-001",
        "promethazine_25_tab",
        summary=assessed("adjust", "1.15", "1.2", "1.25", "promethazine"),
    )  # one 25 mg tablet already delivers 30 mg against a 25 mg target
    seen["no_deliverable_amount"] = propose(ask("promethazine_25_tab", "ICL97"), given(lot_views=(strong,)))
    rules = dict(base.treatments.rules)
    rule = rules["apap_pain"]
    small_cap = replace(rule.ingredients[0], max_per_administration=Amount(Decimal(325), "mg"))
    rules["apap_pain"] = replace(rule, ingredients=(small_cap,))
    seen["per_administration_max_exceeded"] = propose(
        ask(apap, headache), replace(base, treatments=replace(base.treatments, rules=rules))
    )
    full = [past(20 - 4 * n, "acetaminophen", 650, rule_id="apap_pain", n=n) for n in range(5)]
    seen["period_max_exceeded"] = propose(ask(apap, headache), given(history=full))
    cumulative = dict(base.treatments.rules)
    capped = replace(
        rule.ingredients[0], max_cumulative=Amount(Decimal(1000), "mg"), cumulative_scope="course"
    )
    cumulative["apap_pain"] = replace(rule, ingredients=(capped,))
    seen["cumulative_max_exceeded"] = propose(
        ask(apap, headache),
        replace(
            base,
            treatments=replace(base.treatments, rules=cumulative),
            history=(past(30, "acetaminophen", 650, rule_id="apap_pain"),),
        ),
    )
    catastrophic = replace(base.treatments, catastrophic={"acetaminophen": Amount(Decimal(325), "mg")})
    seen["catastrophic_limit"] = propose(ask(apap, headache), replace(base, treatments=catastrophic))
    seen["insufficient_stock"] = propose(
        ask(apap, headache), given(lot_views=(lot("lot-apap-001", apap, quantity=1),))
    )
    many = [past(23 - n * 0.01, "acetaminophen", 1, rule_id="apap_pain", n=n) for n in range(3)]
    seen["history_too_large"] = propose(ask(apap, headache), given(history=many, max_window_rows=2))
    codes = {code: _refusal(proposal) for code, proposal in seen.items()}
    assert codes == {code: code for code in seen}
    assert set(REFUSAL_CODES) == set(seen)


@pytest.mark.trace("T-51")
@pytest.mark.parametrize(
    ("state", "eligible", "mode", "labeled_only", "expected"),
    [
        (None, True, "SIM", False, "labeled"),
        ("in_specification", True, "SIM", False, "labeled"),
        ("adjust", True, "SIM", False, "compensated"),
        ("adjust", True, "SIM", True, "labeled_with_estimate"),
        ("adjust", True, "REPLAY", False, "compensated"),
        ("adjust", False, "SIM", False, "adjustment_not_permitted"),
    ],
)
def test_the_gate_table_picks_the_path(
    state: str | None, eligible: bool, mode: Mode, labeled_only: bool, expected: str
) -> None:
    summary = (
        None if state is None else assessed(state, "1.0", "1.0", "1.0", "promethazine", eligible=eligible)
    )
    views = (lot("lot-pmz-tab-001", "promethazine_25_tab", summary=summary),)
    request = replace(ask("promethazine_25_tab", "ICL97"), labeled_only=labeled_only)
    proposal = propose(request, given(lot_views=views, mode=mode))
    assert (proposal.path if proposal.outcome == "proposal" else _refusal(proposal)) == expected


@pytest.mark.trace("T-78", "INV-M2")
def test_live_without_an_authorization_is_never_compensated() -> None:
    base = given(mode="LIVE")
    rules = {k: replace(v, review_state="reviewed") for k, v in base.treatments.rules.items()}
    summary = assessed("adjust", "1.0", "1.0", "1.0", "promethazine")
    ctx = replace(
        base,
        treatments=replace(base.treatments, rules=rules),
        lots=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=summary),),
    )
    assert propose(ask("promethazine_25_tab", "ICL97"), ctx).path == "labeled_with_estimate"
    authorized = replace(
        ctx, policy=replace(ctx.policy, live_authorizations={"promethazine_25_tab": "2028-01-01"})
    )
    assert propose(ask("promethazine_25_tab", "ICL97"), authorized).path == "compensated"


@pytest.mark.trace("T-69", "INV-M4")
def test_lot_exclusions_are_status_inspection_expiry_do_not_use_and_empty() -> None:
    assert exclusions(lot("a", "p"), NOW) == ()
    assert "status" in " ".join(exclusions(lot("a", "p", status="quarantined"), NOW))
    assert exclusions(lot("a", "p", adverse=True), NOW)
    assert exclusions(lot("a", "p", expiry=NOW), NOW)
    assert exclusions(lot("a", "p", quantity=0), NOW)
    do_not_use = assessed("do_not_use", "0.5", "0.6", "0.7", "acetaminophen")
    assert exclusions(lot("a", "p", summary=do_not_use), NOW)


@pytest.mark.trace("T-52")
def test_a_stale_context_part_is_refused_like_a_missing_one() -> None:
    old = complete_context(allergies=part("none_known", age=timedelta(days=31)))
    assert (
        _refusal(propose(ask("acetaminophen_325_tab", "ICL63"), given(context=old))) == "allergy_list_unknown"
    )


@pytest.mark.trace("T-52")
def test_every_proposal_carries_its_trace_and_review_state() -> None:
    proposal = propose(ask("acetaminophen_325_tab", "ICL63"), given())
    steps = [t.step for t in proposal.trace]
    for step in ("rule", "context", "target", "max_per_administration", "max_per_period", "lot", "amount"):
        assert step in steps
    assert proposal.review_state == "draft"
    assert any(t.source.startswith("treatments.json@") for t in proposal.trace)


def test_lots_fixture_has_the_three_reviewed_products() -> None:
    assert {x.product_id for x in lots()} == {
        "acetaminophen_325_tab",
        "ibuprofen_400_tab",
        "promethazine_25_tab",
    }
