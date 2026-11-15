"""Golden dose cases (AGENTS rule 1, T-50): every expected value below was read from the cited source by hand and is
written here as a literal, never derived from config/treatments.json. Sources, with the pages checked
(docs/build/E32/research/dosing-safety.md section 1.2):

- Acetaminophen 325 mg tablet. NASA N18 (Shuttle Medical Checklist JSC-48031) p. 5-3 gives Tylenol for pain "every
  4-6 hr"; NASA gives no daily maximum or course length, so those come from the FDA label LBL01 (DailyMed set ID
  1622f694-4d63-4c56-8737-fae31f0ecfb7): "take 2 tablets every 4 to 6 hours", "do not take more than 10 tablets in 24
  hours", "do not use for more than 10 days for pain".
- Ibuprofen 400 mg tablet. N18 p. 5-3 "every 4-6 hr" agrees; LBL02 (DailyMed 793e4b36-8208-4fa3-9f4f-9e9e72648280):
  400 mg every 4 to 6 hours, "do not exceed 3200 mg" in a day.
- Promethazine 25 mg tablet. N18 p. 6-10 limits Phenergan to 100 mg in 24 hours; N18 pp. 4-38 and 4-39 repeat every
  4 hours; LBL03 (DailyMed e16238dc-9292-49ac-b80f-8afeccb88bc5) 25 mg.
- Interactions: N18 p. 6-2 (promethazine with zolpidem and other sedatives). Aspirin and NSAID cross-sensitivity:
  LBL02 Warnings ("aspirin-sensitive asthma"). Ibuprofen with epistaxis: LBL02 Warnings (bleeding).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from crew_health.medicines.domain.engine import propose

from .harness import NOW
from .kit import ask, assessed, complete_context, given, lot, part, past

# Hand-read source values (mg), never from configuration.
APAP_TABLET_MG = Decimal(325)  # LBL01 product strength
APAP_LABEL_DOSE_TABLETS = Decimal(2)  # LBL01 "take 2 tablets"
APAP_MAX_24H_MG = 10 * APAP_TABLET_MG  # LBL01 "not more than 10 tablets in 24 hours" = 3250 mg
APAP_INTERVAL_H = 4  # LBL01 and N18 p. 5-3 "every 4-6 hr", shortest end
APAP_COURSE_DAYS = 10  # LBL01 "more than 10 days for pain"
IBU_DOSE_MG = Decimal(400)  # LBL02, one 400 mg tablet
IBU_MAX_24H_MG = Decimal(3200)  # LBL02
PMZ_DOSE_MG = Decimal(25)  # LBL03
PMZ_MAX_24H_MG = Decimal(100)  # N18 p. 6-10

HEADACHE, SPACE_HEADACHE, MOTION_SICKNESS, EPISTAXIS = "ICL63", "ICL94", "ICL97", "ICL38"


def _apap_doses(hours_ago: list[float]) -> list[object]:
    dose = APAP_LABEL_DOSE_TABLETS * APAP_TABLET_MG
    return [past(h, "acetaminophen", dose, rule_id="apap_pain", n=n) for n, h in enumerate(hours_ago)]


@pytest.mark.trace("T-50", "R-E32-02", "INV-M1")
def test_acetaminophen_headache_is_two_325_mg_tablets_every_four_hours() -> None:
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given())
    assert proposal.outcome == "proposal"
    assert proposal.amount is not None
    assert (proposal.amount.value, proposal.amount.ucum) == (APAP_LABEL_DOSE_TABLETS, "{tbl}")
    (line,) = proposal.ingredients
    assert line.accounting.value == APAP_LABEL_DOSE_TABLETS * APAP_TABLET_MG == Decimal(650)
    assert line.accounting.ucum == "mg"
    assert proposal.next_dose_earliest_at == NOW + timedelta(hours=APAP_INTERVAL_H)
    assert proposal.path == "labeled"
    assert proposal.lot_id == "lot-apap-001"  # earliest labeled expiry, then lot id


@pytest.mark.trace("T-50", "INV-M1")
def test_acetaminophen_after_four_doses_in_24_hours_still_fits_a_fifth() -> None:
    history = _apap_doses([20, 16, 12, 8])  # 2600 mg; 3250 - 2600 = 650 mg left
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=history))  # type: ignore[arg-type]
    assert proposal.outcome == "proposal"
    assert proposal.ingredients[0].remaining_allowance.value == APAP_MAX_24H_MG - 4 * Decimal(650)


@pytest.mark.trace("T-50", "INV-M1")
def test_acetaminophen_after_five_doses_in_24_hours_is_refused_at_the_daily_maximum() -> None:
    history = _apap_doses([20, 16, 12, 8, 4])  # 3250 mg, the LBL01 maximum
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=history))  # type: ignore[arg-type]
    assert proposal.outcome == "refusal"
    assert proposal.refusal is not None and proposal.refusal.code == "period_max_exceeded"


@pytest.mark.trace("T-50", "INV-M1")
def test_a_dose_exactly_24_hours_old_has_left_the_window() -> None:
    history = _apap_doses([24, 20, 16, 12, 8])  # the first is at now - 24 h: outside (now - 24 h, now]
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=history))  # type: ignore[arg-type]
    assert proposal.outcome == "proposal"


@pytest.mark.trace("T-50", "INV-M1")
def test_acetaminophen_with_half_a_dose_left_is_refused_never_halved() -> None:
    history = [
        *_apap_doses([20, 16, 12, 8]),
        past(6, "acetaminophen", APAP_TABLET_MG, rule_id="apap_pain", n=9),
    ]
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=history))  # type: ignore[arg-type]
    assert proposal.outcome == "refusal"  # 2925 mg used; the 650 mg label amount does not fit in 325 mg
    assert proposal.refusal is not None and proposal.refusal.code == "period_max_exceeded"


@pytest.mark.trace("T-50", "INV-M3")
def test_acetaminophen_before_four_hours_is_refused_with_the_next_time() -> None:
    history = _apap_doses([3.5])
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=history))  # type: ignore[arg-type]
    assert proposal.refusal is not None and proposal.refusal.code == "min_interval_not_passed"
    assert proposal.next_dose_earliest_at == NOW - timedelta(hours=3.5) + timedelta(hours=APAP_INTERVAL_H)


@pytest.mark.trace("T-50")
def test_acetaminophen_course_ends_after_ten_days_of_the_same_diagnosis() -> None:
    days = APAP_COURSE_DAYS * 24
    ended = propose(ask("acetaminophen_325_tab", HEADACHE), given(history=_apap_doses([days])))  # type: ignore[arg-type]
    assert ended.refusal is not None and ended.refusal.code == "course_ended"
    running = propose(
        ask("acetaminophen_325_tab", HEADACHE),
        given(history=_apap_doses([days - 1])),  # type: ignore[arg-type]
    )
    assert running.outcome == "proposal"
    other = propose(
        ask("acetaminophen_325_tab", HEADACHE, lineage="medd_other"),
        given(history=_apap_doses([days])),  # type: ignore[arg-type]
    )
    assert other.outcome == "proposal"  # a new working diagnosis starts a new course


@pytest.mark.trace("T-50", "INV-M1")
def test_ibuprofen_is_one_400_mg_tablet_and_stops_at_3200_mg_a_day() -> None:
    first = propose(ask("ibuprofen_400_tab", HEADACHE), given())
    assert first.amount is not None and first.amount.value == 1
    assert first.ingredients[0].accounting.value == IBU_DOSE_MG
    # Four-hourly label doses reach at most 6 x 400 mg in 24 h; the daily maximum binds when manual two-tablet
    # administrations were recorded: 3 x 800 mg leaves 800 mg, 4 x 800 mg reaches 3200 mg.
    three = [past(20 - 4 * n, "ibuprofen", 2 * IBU_DOSE_MG, rule_id="ibu_pain", n=n) for n in range(3)]
    assert propose(ask("ibuprofen_400_tab", HEADACHE), given(history=three)).outcome == "proposal"
    four = [past(20 - 4 * n, "ibuprofen", 2 * IBU_DOSE_MG, rule_id="ibu_pain", n=n) for n in range(4)]
    assert 4 * 2 * IBU_DOSE_MG == IBU_MAX_24H_MG
    refused = propose(ask("ibuprofen_400_tab", HEADACHE), given(history=four))
    assert refused.refusal is not None and refused.refusal.code == "period_max_exceeded"


@pytest.mark.trace("T-50", "INV-M1")
def test_promethazine_is_25_mg_and_stops_at_100_mg_a_day() -> None:
    first = propose(ask("promethazine_25_tab", MOTION_SICKNESS), given())
    assert first.amount is not None and first.amount.value == 1
    assert first.ingredients[0].accounting.value == PMZ_DOSE_MG
    three = [past(20 - 5 * n, "promethazine", PMZ_DOSE_MG, rule_id="pmz_nausea", n=n) for n in range(3)]
    assert propose(ask("promethazine_25_tab", MOTION_SICKNESS), given(history=three)).outcome == "proposal"
    four = [past(20 - 5 * n, "promethazine", PMZ_DOSE_MG, rule_id="pmz_nausea", n=n) for n in range(4)]
    assert 4 * PMZ_DOSE_MG == PMZ_MAX_24H_MG
    refused = propose(ask("promethazine_25_tab", MOTION_SICKNESS), given(history=four))
    assert refused.refusal is not None and refused.refusal.code == "period_max_exceeded"


@pytest.mark.trace("T-50", "INV-M5")
def test_aspirin_allergy_raises_a_cross_sensitivity_override_on_ibuprofen() -> None:
    allergic = complete_context(
        allergies=part("documented", [{"substance_kind": "ingredient", "substance_id": "aspirin"}])
    )
    proposal = propose(ask("ibuprofen_400_tab", HEADACHE), given(context=allergic))
    kinds = {a.kind: a for a in proposal.alerts}
    assert kinds["cross_sensitivity"].level == "override_required"
    assert "crew_medical_officer" in kinds["cross_sensitivity"].override_roles
    apap = propose(ask("acetaminophen_325_tab", HEADACHE), given(context=allergic))
    assert not {a.kind for a in apap.alerts} & {
        "cross_sensitivity",
        "allergy_class",
        "allergy_same_ingredient",
    }


@pytest.mark.trace("T-50", "INV-M5")
def test_documented_acetaminophen_allergy_needs_an_override() -> None:
    allergic = complete_context(
        allergies=part("documented", [{"substance_kind": "ingredient", "substance_id": "acetaminophen"}])
    )
    proposal = propose(ask("acetaminophen_325_tab", HEADACHE), given(context=allergic))
    assert [a.kind for a in proposal.alerts if a.level == "override_required"] == ["allergy_same_ingredient"]


@pytest.mark.trace("T-50", "INV-M5")
def test_promethazine_with_zolpidem_raises_the_n18_interaction() -> None:
    taking = complete_context(
        other_medicines=part("documented", [{"ingredient_id": "zolpidem", "name": "Ambien"}])
    )
    proposal = propose(ask("promethazine_25_tab", MOTION_SICKNESS), given(context=taking))
    assert [a.alert_id for a in proposal.alerts if a.kind == "interaction"] == ["interaction:zolpidem"]


@pytest.mark.trace("T-50", "INV-M5")
def test_ibuprofen_during_epistaxis_raises_a_condition_caution_and_hepatic_impairment_one_on_acetaminophen() -> (
    None
):
    bleeding = complete_context(conditions=part("documented", [{"condition_id": EPISTAXIS}]))
    ibu = propose(ask("ibuprofen_400_tab", HEADACHE), given(context=bleeding))
    assert "condition_caution" in {a.kind for a in ibu.alerts}
    liver = complete_context(hepatic=part("impaired"))
    apap = propose(ask("acetaminophen_325_tab", SPACE_HEADACHE), given(context=liver))
    assert "organ_caution" in {a.kind for a in apap.alerts}


@pytest.mark.trace("T-50", "INV-M5")
def test_acetaminophen_already_taken_outside_the_hub_is_flagged() -> None:
    taking = complete_context(
        other_medicines=part("documented", [{"ingredient_id": "acetaminophen", "name": "cold remedy"}])
    )
    kinds = {
        a.kind: a.level for a in propose(ask("acetaminophen_325_tab", HEADACHE), given(context=taking)).alerts
    }
    assert kinds["duplicate_ingredient"] == "acknowledge"
    assert kinds["outside_medicine_overlap"] == "override_required"


@pytest.mark.trace("T-52", "INV-M1", "E32 3.6")
def test_an_upper_potency_estimate_above_label_counts_against_every_limit() -> None:
    """E32 3.6 Acc = a * S * max(1, u). Hand-computed: with 75 mg of promethazine recorded in the last 24 hours the
    N18 p. 6-10 100 mg limit leaves 25 mg; one 25 mg tablet (LBL03) at u = 1.10 counts 27.5 mg and is refused, at
    u = 1.00 it counts exactly 25 mg and is the boundary that passes."""
    rows = [past(10, "promethazine", 25, rule_id="pmz_nausea", n=n) for n in range(3)]
    above = assessed("in_specification", "1.0", "1.05", "1.10", "promethazine")
    refused = propose(
        ask("promethazine_25_tab", MOTION_SICKNESS),
        given(history=rows, lot_views=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=above),)),
    )
    assert refused.outcome == "refusal" and refused.refusal is not None
    assert refused.refusal.code in {"period_max_exceeded", "per_administration_max_exceeded"}
    at_label = assessed("in_specification", "0.95", "0.98", "1.00", "promethazine")
    ok = propose(
        ask("promethazine_25_tab", MOTION_SICKNESS),
        given(history=rows, lot_views=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=at_label),)),
    )
    assert ok.outcome == "proposal" and ok.amount is not None and ok.amount.value == 1
    assert ok.ingredients[0].accounting.value == PMZ_DOSE_MG
    assert PMZ_DOSE_MG * 3 + ok.ingredients[0].accounting.value == PMZ_MAX_24H_MG
