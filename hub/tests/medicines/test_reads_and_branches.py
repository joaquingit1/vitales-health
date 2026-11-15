"""Reads (treatment options, lots board, proposals, administrations, medicine names), a stored engine assessment
reaching the dose path, the potency gate switches, and unit conversion (E32 3.4 to 3.7, 4.3, 4.4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given as hypothesis_given
from hypothesis import strategies as hs

from crew_health.medicines.adapters.documents import load
from crew_health.medicines.application.ports import RunOutcome
from crew_health.medicines.application.use_cases import active_medicine_names
from crew_health.medicines.domain.documents import DocumentsInvalid
from crew_health.medicines.domain.engine import propose
from crew_health.medicines.domain.units import Amount, UnitIncompatible, convert, display, text

from .harness import (
    SESSIONS,
    SUBJECT,
    FakeEngine,
    Station,
    build_station,
    config_copy,
    fixture_config,
    headers,
)
from .kit import ask, assessed, complete_context, condition_ids, given, lot, part, switched
from .test_flow import CLIENT_TIME, _confirm, _context, _diagnose, _post, _propose

FIXTURE = Path(__file__).parent / "fixtures/engine-run.json"


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


@pytest.mark.trace("R-E32-01")
def test_treatment_options_list_the_reviewed_formulary_lines_with_usable_lots(st: Station) -> None:
    diagnosis = _diagnose(st, "ICL63")
    response = st.client.get(f"/v1/working-diagnoses/{diagnosis}/treatment-options", headers=headers())
    assert response.status_code == 200
    options = {o["product_id"]: o for o in response.json()["options"]}
    assert options["acetaminophen_325_tab"]["line"] == "first_choice"
    assert options["ibuprofen_400_tab"]["line"] == "alternative"
    assert options["acetaminophen_325_tab"]["usable_lot_ids"] == ["lot-apap-001", "lot-apap-002"]
    hidden = st.client.get(
        f"/v1/working-diagnoses/{diagnosis}/treatment-options", headers=headers("subject_cmo")
    )
    assert hidden.status_code == 404  # the subject is never the clinician for their own record


@pytest.mark.trace("T-72", "DD9", "R-E32-10")
def test_a_stored_engine_assessment_reaches_the_board_and_the_dose_path(tmp_path: Path) -> None:
    st = build_station(tmp_path, config_dir=fixture_config(tmp_path / "config-fixture-lots"))
    _context(st)
    scheduled = st.client.post("/v1/medicines/assessments", headers=headers("operator"))
    assert scheduled.status_code == 202 and scheduled.json()["state"] == "queued"
    run_id = scheduled.json()["job_id"]
    assert st.drain() == 1
    board = st.client.get("/v1/medicines/lots", headers=headers()).json()
    assert board["assessment"]["run_id"] == run_id and board["assessment"]["status"] == "completed"
    (pmz,) = [x for x in board["lots"] if x["lot_id"] == "lot-pmz-tab-001"]
    assert pmz["state"] == "adjust" and pmz["potency"][0]["ingredient_id"] == "promethazine"
    diagnosis = _diagnose(st, "ICL97")
    # E32 3.6 counts every increment at max(1, u) on either path: u = 1.0117 puts one 25 mg tablet at 25.29 mg,
    # over the 25 mg per-administration maximum (LBL03), so both paths refuse with the binding limit named.
    compensated = cast(dict[str, Any], _propose(st, diagnosis, "promethazine_25_tab"))
    assert compensated["outcome"] == "refusal"
    assert compensated["refusal"]["code"] == "per_administration_max_exceeded"
    labeled = _post(
        st,
        "/v1/dose-proposals",
        {
            "working_diagnosis_id": diagnosis,
            "product_id": "promethazine_25_tab",
            "path": "labeled",
            "client_time": CLIENT_TIME,
        },
    ).json()
    assert labeled["outcome"] == "refusal"
    assert labeled["refusal"]["code"] == "per_administration_max_exceeded"


@pytest.mark.trace("R-E32-04")
def test_a_failed_assessment_leaves_the_last_completed_one_in_use(st: Station) -> None:
    st.use_engine(FakeEngine(RunOutcome("failed", None, "engine_error", "ValueError: no")))
    assert st.drain() == 1
    assert st.client.get("/v1/medicines/lots", headers=headers()).json()["assessment"] is None


@pytest.mark.trace("T-75", "E32-4.3")
def test_proposals_administrations_and_names_follow_the_record_grant(st: Station) -> None:
    _context(
        st,
        other_medicines={
            "state": "documented",
            "items": [{"ingredient_id": "loratadine", "name": "Claritin"}],
        },
    )
    proposal = _propose(st, _diagnose(st))
    path = f"/v1/dose-proposals/{proposal['proposal_id']}"
    assert st.client.get(path, headers=headers("crew_b")).status_code == 404
    assert st.client.get(path, headers=headers("subject")).json()["proposal_id"] == proposal["proposal_id"]
    assert _confirm(st, proposal).status_code == 201
    listed = st.client.get(f"/v1/medication-administrations?subject={SUBJECT}", headers=headers("subject"))
    assert len(listed.json()["items"]) == 1
    names = active_medicine_names(st.deps, SESSIONS["cmo"], SUBJECT)
    assert names == ("Acetaminophen 325 mg tablet", "Claritin")
    from crew_health.kernel.errors import ProblemError

    with pytest.raises(ProblemError):
        active_medicine_names(st.deps, SESSIONS["crew_b"], SUBJECT)
    st.clock.at = st.clock.at + timedelta(days=2)  # out of the 24 hour window: no longer active
    assert active_medicine_names(st.deps, SESSIONS["cmo"], SUBJECT) == ("Claritin",)


@pytest.mark.trace("T-51")
@pytest.mark.parametrize(
    "switch",
    [
        "require_measured_assay",
        "require_degradant_profile_reviewed",
        "require_validated_engine",
    ],
)
def test_each_potency_policy_switch_can_close_the_compensated_path(switch: str) -> None:
    summary = assessed("adjust", "1.0", "1.0", "1.0", "promethazine")
    ctx = given(lot_views=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=summary),))
    assert propose(ask("promethazine_25_tab", "ICL97"), ctx).path == "compensated"
    closed = replace(ctx, policy=replace(ctx.policy, potency=switched(ctx.policy.potency, switch)))
    refused = propose(ask("promethazine_25_tab", "ICL97"), closed)
    assert refused.refusal is not None and refused.refusal.code == "adjustment_not_permitted"
    assert switch in refused.refusal.detail_text


@pytest.mark.trace("T-50", "INV-M5")
def test_an_allergy_class_and_an_ended_outside_medicine(st: Station) -> None:
    nsaid = complete_context(
        allergies=part("documented", [{"substance_kind": "allergy_class", "substance_id": "nsaid"}])
    )
    kinds = {a.kind for a in propose(ask("ibuprofen_400_tab", "ICL63"), given(context=nsaid)).alerts}
    assert "allergy_class" in kinds
    ended = complete_context(
        other_medicines=part(
            "documented", [{"ingredient_id": "zolpidem", "until": datetime(2028, 2, 1, tzinfo=UTC)}]
        )
    )
    kinds = {a.kind for a in propose(ask("promethazine_25_tab", "ICL97"), given(context=ended)).alerts}
    assert "interaction" not in kinds


def _authorization(product_id: str) -> dict[str, object]:
    return {
        "product_id": product_id,
        "authorized_by_role": "flight_surgeon",
        "authorized_at": "2028-01-01T00:00:00Z",
        "basis": "test",
        "source_ids": ["RT22"],
        "note": "",
    }


@pytest.mark.trace("E32-8.4", "M-L1", "M-L2", "M-L5", "M-L8", "M-L9", "M-SRC")
@pytest.mark.parametrize(
    ("edit", "check"),
    [
        ("product_missing", "M-L1"),
        ("unknown_source", "M-SRC"),
        ("unknown_rule", "M-L1"),
        ("product_not_in_formulary", "M-L1"),
        ("rule_for_another_product", "M-L1"),
        ("increment_unit", "M-L2"),
        ("unknown_unit", "M-SCHEMA"),  # the schema already pins the unit vocabulary
        ("per_kg_without_weight", "M-L5"),
        ("min_effective_above_cap", "M-L5"),
        ("period_below_cap", "M-L5"),
        ("unknown_kind", "M-L8"),
        ("missing_kind", "M-L8"),
        ("live_twice", "M-L9"),
        ("live_not_compensable", "M-L9"),
        ("caution_unknown", "M-L8"),
    ],
)
def test_more_invalid_documents_are_rejected(tmp_path: Path, edit: str, check: str) -> None:
    def apply(treatments: dict[str, object], policy: dict[str, object]) -> None:
        products = cast(list[dict[str, object]], treatments["products"])
        kinds = cast(list[dict[str, object]], treatments["alert_policies"])
        indications = cast(list[dict[str, Any]], treatments["indications"])
        apap = cast(dict[str, Any], cast(list[dict[str, Any]], treatments["dose_rules"])[0]["ingredients"][0])
        if edit == "unknown_source":
            indications[0]["citations"][0]["source_id"] = "NOT_A_SOURCE"
        elif edit == "unknown_rule":
            indications[0]["lines"][0]["rule_id"] = "not_a_rule"
        elif edit == "product_not_in_formulary":
            products.append({**products[0], "product_id": "ghost_tab"})
            indications[0]["lines"].append(
                {"line": "alternative", "product_id": "ghost_tab", "rule_id": "apap_pain"}
            )
        elif edit == "rule_for_another_product":
            indications[0]["lines"][1]["rule_id"] = "apap_pain"
        elif edit == "increment_unit":
            products[0]["deliverable_increment"] = {"value": 1, "ucum": "mL"}
        elif edit == "unknown_unit":
            apap["dose"] = {"value": 650, "ucum": "furlong"}
        elif edit == "per_kg_without_weight":
            apap["dose_per_kg"] = {"value": 10, "ucum": "mg"}
        elif edit == "min_effective_above_cap":
            apap["min_effective"] = {"value": 1000, "ucum": "mg"}
        elif edit == "period_below_cap":
            apap["max_per_period"][0]["limit"] = {"value": 100, "ucum": "mg"}
        elif edit == "product_missing":
            treatments["products"] = [p for p in products if p["product_id"] != "ibuprofen_400_tab"]
        elif edit == "unknown_kind":
            kinds.append({**kinds[0], "kind": "astrology"})
        elif edit == "missing_kind":
            treatments["alert_policies"] = kinds[1:]
        elif edit == "live_twice":
            policy["live_compensation_authorizations"] = [_authorization("promethazine_25_tab")] * 2
        elif edit == "live_not_compensable":
            policy["live_compensation_authorizations"] = [_authorization("promethazine_25mg_ml_inj")]
        elif edit == "caution_unknown":
            treatments["dose_rules"][1]["condition_cautions"] = ["ICL9999"]  # type: ignore[index]

    directory = config_copy(tmp_path, apply)
    with pytest.raises(DocumentsInvalid) as raised:
        load(directory, condition_ids())
    assert check in {f.check for f in raised.value.findings}, raised.value.findings


@pytest.mark.trace("AGENTS-8")
@hypothesis_given(hs.integers(min_value=0, max_value=10**9), hs.sampled_from(["kg", "g", "mg", "ug"]))
def test_mass_conversion_round_trips_exactly(value: int, unit: str) -> None:
    amount = Amount(Decimal(value), unit)
    assert convert(convert(amount, "ug"), unit) == amount


def test_units_never_cross_dimensions_and_display_in_ismp_style() -> None:
    with pytest.raises(UnitIncompatible):
        convert(Amount(Decimal(1), "mg"), "mL")
    with pytest.raises(UnitIncompatible):
        convert(Amount(Decimal(1), "furlong"), "mg")
    with pytest.raises(ValueError):
        Amount(Decimal("NaN"), "mg")
    assert text(Decimal("0.5")) == "0.5" and text(Decimal("3250.0")) == "3,250"
    assert display(Amount(Decimal(650), "mg")) == "650 mg"
