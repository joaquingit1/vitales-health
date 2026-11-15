"""Domain edges of the medicines dose engine, its alerts, units, eligibility and record codec (E32 3.5 to 3.7): the
rarely taken branches, each asserted by the refusal code, finding or value it must produce."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crew_health.medicines.application.codec import context_part, decimal_text, dumps, loads
from crew_health.medicines.domain.alerts import alerts_for
from crew_health.medicines.domain.eligibility import Confirmer, may_override
from crew_health.medicines.domain.engine import fresh, manual_findings, propose
from crew_health.medicines.domain.model import (
    Alert,
    DoseContext,
    DoseRequest,
    IngredientLine,
    Proposal,
    WeightObservation,
)
from crew_health.medicines.domain.units import Amount, UnitIncompatible, convert

from .harness import NOW, SUBJECT
from .kit import ask, assessed, complete_context, given, lot, part, past
from .test_engine import _weighted

APAP, HEADACHE = "acetaminophen_325_tab", "ICL63"
PMZ, NAUSEA = "promethazine_25_tab", "ICL97"
IBU = "ibuprofen_400_tab"


def _code(proposal: Proposal) -> str:
    assert proposal.refusal is not None, proposal
    return proposal.refusal.code


def _apap_rule(ctx: DoseContext, **changes: object) -> DoseContext:
    """apap_pain with its one ingredient rule changed (a mechanism probe, never a clinical claim)."""
    rule = ctx.treatments.rules["apap_pain"]
    ingredient = replace(rule.ingredients[0], **changes)  # type: ignore[arg-type]
    rules = {**ctx.treatments.rules, "apap_pain": replace(rule, ingredients=(ingredient,))}
    return replace(ctx, treatments=replace(ctx.treatments, rules=rules))


def _manual(ctx: DoseContext, tablets: int = 2) -> tuple[dict[str, Amount], tuple[str, ...]]:
    request = DoseRequest(SUBJECT, None, APAP, "lot-apap-001", True)
    per = ctx.formulary[APAP].per
    return manual_findings(ctx, request, lot("lot-apap-001", APAP), Amount(Decimal(tablets), per))


# ---- engine ----


@pytest.mark.trace("E32-3.5", "INV-M2")
def test_an_assessment_that_misses_an_ingredient_of_the_product_is_never_used() -> None:
    ctx = given()
    product = ctx.formulary[PMZ]
    covering = assessed("adjust", "0.85", "0.9", "0.95", "promethazine")
    assert fresh(covering, ctx, product) is covering
    foreign = assessed("adjust", "0.85", "0.9", "0.95", "acetaminophen")
    assert fresh(foreign, ctx, product) is None
    proposal = propose(ask(PMZ, NAUSEA), given(lot_views=(lot("lot-pmz-tab-001", PMZ, summary=foreign),)))
    assert proposal.outcome == "proposal"
    assert proposal.path == "labeled" and proposal.potency_status == "stale"
    assert proposal.assessment_run_id is None and proposal.intervals == {}


@pytest.mark.trace("E32-3.5", "T-73")
@pytest.mark.parametrize(
    ("older", "newer"),
    [
        # 2.9 % change: the two agree
        ((Decimal(70), timedelta(days=3)), (Decimal(72), timedelta(days=1))),
        # 20 % change, but outside the 7 day change window
        ((Decimal(60), timedelta(days=20)), (Decimal(72), timedelta(days=1))),
    ],
)
def test_a_second_weight_that_agrees_or_is_outside_the_change_window_does_not_block_the_dose(
    older: tuple[Decimal, timedelta], newer: tuple[Decimal, timedelta]
) -> None:
    weighted, _ = _weighted()
    weights = (
        WeightObservation("obs_old", older[0], NOW - older[1]),
        WeightObservation("obs_new", newer[0], NOW - newer[1]),
    )
    proposal = propose(ask(APAP, HEADACHE), replace(weighted, weights=weights))
    assert proposal.outcome == "proposal" and proposal.amount is not None
    assert [t.source for t in proposal.trace if t.step == "weight"] == ["record:obs_new"]
    # 10 mg/kg of 72 kg is 720 mg, capped at the 650 mg administration maximum (two 325 mg tablets)
    assert proposal.amount.value == 2


@pytest.mark.trace("E32-3.5", "INV-M1")
def test_the_labeled_path_refuses_a_target_no_whole_number_of_tablets_reaches() -> None:
    """500 mg of 325 mg tablets: two tablets deviate 30 % (the nearest), over the product's zero tolerance."""
    proposal = propose(ask(APAP, HEADACHE), _apap_rule(given(), dose=Amount(Decimal(500), "mg")))
    assert _code(proposal) == "no_deliverable_amount"
    assert proposal.refusal is not None and "deviates 0.3 from the target" in proposal.refusal.detail_text
    assert proposal.amount is None


@pytest.mark.trace("E32-3.5", "INV-M1")
def test_one_tablet_over_the_per_administration_maximum_is_no_deliverable_amount() -> None:
    """A 200 mg target under a 300 mg maximum passes the target check, but one 325 mg tablet already exceeds it."""
    ctx = _apap_rule(
        given(),
        dose=Amount(Decimal(200), "mg"),
        max_per_administration=Amount(Decimal(300), "mg"),
    )
    proposal = propose(ask(APAP, HEADACHE), ctx)
    assert _code(proposal) == "no_deliverable_amount"
    assert proposal.refusal is not None
    assert "one increment is over the per-administration maximum" in proposal.refusal.detail_text


@pytest.mark.trace("E32-3.5", "decision-9")
def test_a_manual_administration_of_a_per_kilogram_rule_states_the_missing_weight() -> None:
    weighted, _ = _weighted()
    accounting, findings = _manual(weighted)
    assert "weight_missing" in findings
    assert accounting == {"acetaminophen": Amount(Decimal(650), "mg")}  # recorded, never refused


@pytest.mark.trace("E32-3.6", "INV-M1", "decision-9")
def test_a_manual_administration_counts_every_course_of_the_rule_against_a_course_limit() -> None:
    """A proposal's course is its diagnosis lineage; a manual one has none, so every use of the rule counts
    (stricter): 650 mg under another lineage leaves 350 mg of a 1,000 mg course limit, under the 650 mg given."""
    other_lineage = (past(30, "acetaminophen", 650, rule_id="apap_pain", lineage="medd_other"),)
    capped = _apap_rule(given(), max_cumulative=Amount(Decimal(1000), "mg"), cumulative_scope="course")
    capped = replace(capped, history=other_lineage)
    _, findings = _manual(capped)
    assert "cumulative_max_exceeded:acetaminophen" in findings
    proposed = propose(ask(APAP, HEADACHE, lineage="medd_1"), capped)
    assert proposed.outcome == "proposal" and proposed.amount is not None and proposed.amount.value == 2


@pytest.mark.trace("E32-3.5", "decision-9")
def test_a_manual_administration_without_a_product_rule_states_the_formulary_mismatch() -> None:
    base = given()
    products = {k: v for k, v in base.treatments.products.items() if k != APAP}
    accounting, findings = _manual(replace(base, treatments=replace(base.treatments, products=products)))
    assert "formulary_mismatch" in findings
    assert accounting == {"acetaminophen": Amount(Decimal(650), "mg")}


@pytest.mark.trace("E32-3.7", "INV-M5", "decision-9")
def test_a_manual_administration_names_only_the_allergies_that_match_the_product() -> None:
    allergies = part(
        "documented",
        [
            {"substance_kind": "ingredient", "substance_id": "amoxicillin"},
            {"substance_kind": "ingredient", "substance_id": "acetaminophen"},
        ],
    )
    _, findings = _manual(given(context=complete_context(allergies=allergies)))
    assert [f for f in findings if f.startswith("documented_allergy")] == ["documented_allergy:acetaminophen"]


# ---- alerts ----


@pytest.mark.trace("E32-3.7", "INV-M5")
def test_an_allergy_class_the_product_is_not_in_raises_no_allergy_alert() -> None:
    allergies = part("documented", [{"substance_kind": "allergy_class", "substance_id": "phenothiazine"}])
    proposal = propose(ask(IBU, HEADACHE), given(context=complete_context(allergies=allergies)))
    assert proposal.outcome == "proposal"
    kinds = {a.kind for a in proposal.alerts}
    assert not kinds & {"allergy_same_ingredient", "allergy_class", "cross_sensitivity"}


def _shortfall_with_floor(floor_mg: int) -> Alert:
    """Compensated promethazine at median 0.90, low 0.85: one tablet delivers 22.5 mg (low 21.25 mg) of 25 mg."""
    summary = assessed("adjust", "0.85", "0.9", "0.95", "promethazine")
    ctx = given(lot_views=(lot("lot-pmz-tab-001", PMZ, summary=summary),))
    rule = ctx.treatments.rules["pmz_nausea"]
    ingredient = replace(rule.ingredients[0], min_effective=Amount(Decimal(floor_mg), "mg"))
    rules = {**ctx.treatments.rules, "pmz_nausea": replace(rule, ingredients=(ingredient,))}
    proposal = propose(ask(PMZ, NAUSEA), replace(ctx, treatments=replace(ctx.treatments, rules=rules)))
    assert proposal.outcome == "proposal" and proposal.path == "compensated"
    (alert,) = [a for a in proposal.alerts if a.kind == "potency_shortfall"]
    return alert


@pytest.mark.trace("E32-3.7", "HZ6", "T-74")
def test_the_potency_shortfall_compares_the_lower_end_with_a_sourced_minimum_effective_amount() -> None:
    under = _shortfall_with_floor(22)
    assert "lower end under the minimum effective amount" in under.text
    assert under.level == "acknowledge"
    at_or_above = _shortfall_with_floor(21)
    assert "lower end at or above the minimum effective amount" in at_or_above.text
    assert "promethazine: target 25 mg, expected 22.5 mg (low 21.25 mg), short 2.5 mg" in at_or_above.text


@pytest.mark.trace("E32-3.7", "HZ6")
def test_a_line_without_an_estimate_never_states_a_shortfall_even_when_capped() -> None:
    ctx = given()
    line = IngredientLine(
        ingredient_id="promethazine",
        target=Amount(Decimal(25), "mg"),
        accounting=Amount(Decimal(25), "mg"),
        expected_median=None,
        expected_low=None,
        remaining_allowance=Amount(Decimal(25), "mg"),
    )
    alerts = alerts_for(
        ctx,
        ctx.treatments.rules["pmz_nausea"],
        ctx.treatments.products[PMZ],
        ctx.formulary[PMZ],
        "compensated",
        capped=True,
        lines=(line,),
        min_effective={"promethazine": None},
        binding={"promethazine": "per_administration_max_exceeded"},
    )
    assert "potency_shortfall" not in {a.kind for a in alerts}
    assert "consult_ground_first" in {a.kind for a in alerts}  # the product's own alerts still stand


# ---- eligibility, units, codec ----


@pytest.mark.trace("E32-3.7", "Q14")
def test_an_override_needs_an_identified_officer_who_is_not_the_subject() -> None:
    alert = Alert(
        "allergy_same_ingredient:x",
        "allergy_same_ingredient",
        "override_required",
        "x",
        ("benefit_outweighs_risk",),
        ("crew_medical_officer",),
    )
    officer = Confirmer("person_cmo", "crew_medical_officer")
    assert may_override(officer, SUBJECT, alert, "benefit_outweighs_risk")
    assert not may_override(Confirmer(None, "crew_medical_officer"), SUBJECT, alert, "benefit_outweighs_risk")
    assert not may_override(
        Confirmer(SUBJECT, "crew_medical_officer"), SUBJECT, alert, "benefit_outweighs_risk"
    )
    assert not may_override(officer, SUBJECT, alert, "not_a_listed_reason")


@pytest.mark.trace("AGENTS-8", "E32-3.5")
def test_an_arbitrary_unit_converts_only_to_itself() -> None:
    assert convert(Amount(Decimal(2), "{tbl}"), "{tbl}") == Amount(Decimal(2), "{tbl}")
    with pytest.raises(UnitIncompatible):
        convert(Amount(Decimal(2), "{tbl}"), "{puff}")
    with pytest.raises(UnitIncompatible):
        convert(Amount(Decimal(2), "{tbl}"), "mg")


@pytest.mark.trace("AGENTS-5")
def test_the_codec_refuses_non_finite_numbers_floats_deep_nesting_and_non_objects() -> None:
    assert decimal_text(Decimal("-0.0")) == "0" and decimal_text(Decimal("1E+3")) == "1000"
    for bad in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="non-finite"):
            decimal_text(bad)
    with pytest.raises(TypeError, match="not JSON: float"):
        dumps({"value": 0.1})
    nested: object = 1
    for _ in range(17):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting"):
        dumps(nested)
    assert dumps({"b": [Decimal("1.50"), None], "a": True}) == '{"a":true,"b":[1.5,null]}'
    with pytest.raises(ValueError, match="object"):
        loads("[1, 2]")
    assert loads('{"v": 0.1}') == {"v": Decimal("0.1")}


@pytest.mark.trace("E32-3.7")
def test_a_stored_context_part_reads_its_times_back_as_instants() -> None:
    at = datetime(2028, 3, 1, 7, 0, tzinfo=UTC)
    body = {
        "state": "documented",
        "items": [{"ingredient_id": "zolpidem", "since": "2028-02-01T00:00:00Z", "until": None}],
    }
    parsed = context_part(body, "medc_1", at)
    (item,) = parsed.items
    assert item["since"] == datetime(2028, 2, 1, tzinfo=UTC) and item["until"] is None
    assert parsed.state == "documented" and parsed.statement_id == "medc_1" and parsed.recorded_at == at
