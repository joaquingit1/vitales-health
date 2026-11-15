"""Edges of the medicines commands: one open proposal per ingredient, revisions, diagnosis lineage, unknown targets,
manual administrations outside every rule, and the remaining gate switches (E32 3.5, 4.4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.medicines.domain.engine import manual_findings, propose
from crew_health.medicines.domain.model import DoseRequest
from crew_health.medicines.domain.units import Amount

from .harness import SUBJECT, Station, build_station, headers
from .kit import ask, assessed, complete_context, given, lot, part, past, switched
from .test_flow import CLIENT_TIME, _confirm, _context, _diagnose, _post, _propose


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


@pytest.mark.trace("T-54", "INV-M8")
def test_a_new_proposal_for_the_same_ingredient_supersedes_the_open_one(st: Station) -> None:
    diagnosis = _diagnose(st)
    first = _propose(st, diagnosis)
    second = _propose(st, diagnosis)
    shown = st.client.get(f"/v1/dose-proposals/{first['proposal_id']}", headers=headers()).json()
    assert shown["state"] == "superseded"
    assert _confirm(st, first).status_code == 409
    assert _confirm(st, second).status_code == 201


@pytest.mark.trace("T-54")
def test_a_stale_if_match_is_refused(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    path = f"/v1/dose-proposals/{proposal['proposal_id']}/confirm"
    body = {
        "lot_id": proposal["lot_id"],
        "overrides": [],
        "acknowledged_alert_ids": [],
        "client_time": CLIENT_TIME,
    }
    assert _post(st, path, body, **{"If-Match": '"7"'}).json()["code"] == "STALE_VERSION"
    assert (
        st.client.post(path, json=body, headers=headers(key="key-noifmatch")).json()["code"]
        == "VERSION_REQUIRED"
    )


@pytest.mark.trace("T-54", "C5")
def test_an_expired_proposal_reads_as_expired_before_any_write(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    st.clock.at = st.clock.at + timedelta(hours=1)
    shown = st.client.get(f"/v1/dose-proposals/{proposal['proposal_id']}", headers=headers()).json()
    assert shown["state"] == "expired"


@pytest.mark.trace("T-50")
def test_a_superseding_diagnosis_keeps_the_course_of_its_lineage(st: Station) -> None:
    first = _diagnose(st)
    revised = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {
            "condition_id": "ICL94",
            "subject_person_id": SUBJECT,
            "supersedes": first,
            "client_time": CLIENT_TIME,
        },
    )
    assert revised.status_code == 201 and revised.json()["lineage_id"] == first
    unknown = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {
            "condition_id": "ICL1",
            "subject_person_id": SUBJECT,
            "supersedes": "medd_none",
            "client_time": CLIENT_TIME,
        },
    )
    assert unknown.status_code == 404
    not_in_kit = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {"condition_id": "ICL99999", "subject_person_id": SUBJECT, "client_time": CLIENT_TIME},
    )
    assert not_in_kit.json()["field_errors"][0]["field"] == "/condition_id"


@pytest.mark.trace("AGENTS-5")
def test_unknown_targets_and_scopes_are_refused(st: Station) -> None:
    assert st.client.get("/v1/dose-proposals/medp_none", headers=headers()).status_code == 404
    assert (
        _post(
            st,
            "/v1/dose-proposals/medp_none/confirm",
            {"lot_id": "x", "overrides": [], "acknowledged_alert_ids": [], "client_time": CLIENT_TIME},
            **{"If-Match": '"1"'},
        ).status_code
        == 404
    )
    assert (
        _post(
            st,
            "/v1/medication-administrations/meda_none/corrections",
            {"reason_text": "x", "client_time": CLIENT_TIME},
        ).status_code
        == 404
    )
    assert (
        _post(
            st,
            "/v1/medicines/inspections",
            {"lot_id": "lot-none", "finding": "no_change", "client_time": CLIENT_TIME},
        ).status_code
        == 404
    )
    assert (
        _post(
            st,
            "/v1/medicines/lots/lot-none/movements",
            {"kind": "return", "quantity": {"value": 1, "ucum": "{tbl}"}, "client_time": CLIENT_TIME},
        ).status_code
        == 404
    )
    wrong_unit: dict[str, object] = {
        "kind": "return",
        "quantity": {"value": 1, "ucum": "mg"},
        "client_time": CLIENT_TIME,
    }
    assert _post(st, "/v1/medicines/lots/lot-apap-001/movements", wrong_unit).status_code == 422
    right_unit: dict[str, object] = {**wrong_unit, "quantity": {"value": 1, "ucum": "{tbl}"}}
    assert (
        _post(st, "/v1/medicines/lots/lot-apap-001/movements", right_unit, "subject").json()["code"]
        == "ROLE_NOT_ELIGIBLE"
    )
    assert st.client.get("/v1/medicines/lots", headers=headers("operator")).status_code == 200
    assert st.client.get("/v1/medicines/lots?environment_id=nowhere", headers=headers()).status_code == 400
    assert st.client.get("/v1/medication-administrations?subject=", headers=headers()).status_code == 400
    assert st.client.get("/v1/medicines/lots", headers=headers("nobody")).status_code == 401
    assert (
        _post(
            st,
            "/v1/dose-proposals",
            {"working_diagnosis_id": "medd_none", "product_id": "x", "client_time": CLIENT_TIME},
        ).status_code
        == 404
    )


@pytest.mark.trace("AGENTS-5")
def test_no_current_mission_means_no_medicines_command(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.mission.facts = None
    response = _post(
        station,
        "/v1/medicines/inspections",
        {"lot_id": "lot-apap-001", "finding": "no_change", "client_time": CLIENT_TIME},
    )
    assert response.json()["code"] == "CONTEXT_INAPPLICABLE"


@pytest.mark.trace("B06")
def test_a_manual_administration_of_a_product_without_a_rule_is_recorded_and_flagged(st: Station) -> None:
    _context(
        st,
        allergies={
            "state": "documented",
            "items": [{"substance_kind": "ingredient", "substance_id": "aspirin"}],
        },
    )
    response = _post(
        st,
        "/v1/medication-administrations",
        {
            "subject_person_id": SUBJECT,
            "product_id": "aspirin_325_tab",
            "lot_id": "lot-asa-001",
            "amount": {"value": 1, "ucum": "{tbl}"},
            "administered_at": "2028-03-01T07:30:00Z",
            "reason_text": "taken from the kit",
            "client_time": CLIENT_TIME,
        },
    )
    assert response.status_code == 201, response.text
    findings = response.json()["limit_findings"]
    assert "no_reviewed_rule_for_product" in findings and "documented_allergy:aspirin" in findings
    unknown = _post(
        st,
        "/v1/medication-administrations",
        {
            "subject_person_id": SUBJECT,
            "product_id": "not_a_product",
            "lot_id": "lot-asa-001",
            "amount": {"value": 1, "ucum": "{tbl}"},
            "administered_at": "2028-03-01T07:30:00Z",
            "reason_text": "x",
            "client_time": CLIENT_TIME,
        },
    )
    assert unknown.status_code == 422


@pytest.mark.trace("B06", "INV-M1")
def test_manual_findings_name_excluded_lots_and_count_the_upper_potency() -> None:
    summary = assessed("adjust", "0.95", "1.0", "1.05", "acetaminophen")
    expired = lot(
        "lot-apap-001", "acetaminophen_325_tab", expiry=datetime(2028, 1, 1, tzinfo=UTC), summary=summary
    )
    ctx = given(lot_views=(expired,))
    request = DoseRequest(SUBJECT, None, "acetaminophen_325_tab", "lot-apap-001", True)
    accounting, findings = manual_findings(ctx, request, expired, Amount(Decimal(2), "{tbl}"))
    assert accounting["acetaminophen"].value == Decimal(650) * Decimal("1.05")
    assert "lot_excluded:expired" in findings or any(f.startswith("lot_excluded:") for f in findings)
    assert "per_administration_max_exceeded:acetaminophen" in findings  # 682.5 mg counted against 650 mg


@pytest.mark.trace("T-51")
def test_information_only_skips_an_adjust_lot_for_an_in_specification_one() -> None:
    adjust = lot(
        "lot-apap-001",
        "acetaminophen_325_tab",
        summary=assessed("adjust", "0.9", "0.95", "1.0", "acetaminophen"),
    )
    plain = lot("lot-apap-002", "acetaminophen_325_tab", expiry=datetime(2029, 7, 1, tzinfo=UTC))
    ctx = given(lot_views=(adjust, plain))
    ctx = replace(
        ctx, policy=replace(ctx.policy, potency=replace(ctx.policy.potency, mode="information_only"))
    )
    proposal = propose(ask("acetaminophen_325_tab", "ICL63"), ctx)
    assert proposal.lot_id == "lot-apap-002" and proposal.path == "labeled"
    missing = propose(ask("acetaminophen_325_tab", "ICL63", lot_id="lot-ibu-001"), given())
    assert missing.refusal is not None and missing.refusal.code == "no_usable_lot"
    chosen = propose(
        ask("acetaminophen_325_tab", "ICL63", lot_id="lot-apap-001"),
        given(lot_views=(lot("lot-apap-001", "acetaminophen_325_tab", adverse=True),)),
    )
    assert chosen.refusal is not None and "excluded" in chosen.refusal.detail_text


@pytest.mark.trace("T-51")
@pytest.mark.parametrize(
    "switch",
    [
        "exclude_narrow_therapeutic_index",
        "exclude_high_alert",
        "require_measured_storage_history",
        "not_allowed",
    ],
)
def test_product_and_custody_switches_close_compensation(switch: str) -> None:
    summary = replace(
        assessed("adjust", "1.0", "1.0", "1.0", "promethazine"), custody_declared_or_gapped=True
    )
    ctx = given(lot_views=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=summary),))
    formulary = dict(ctx.formulary)
    products = dict(ctx.treatments.products)
    formulary["promethazine_25_tab"] = replace(
        formulary["promethazine_25_tab"],
        narrow_therapeutic_index=True,
        potency_adjustment_allowed=switch != "not_allowed",
    )
    products["promethazine_25_tab"] = replace(products["promethazine_25_tab"], high_alert=True)
    potency = ctx.policy.potency if switch == "not_allowed" else switched(ctx.policy.potency, switch)
    closed = replace(
        ctx,
        formulary=formulary,
        treatments=replace(ctx.treatments, products=products),
        policy=replace(ctx.policy, potency=potency),
    )
    refused = propose(ask("promethazine_25_tab", "ICL97"), closed)
    assert refused.refusal is not None and refused.refusal.code == "adjustment_not_permitted"


@pytest.mark.trace("T-52")
def test_a_stale_assessment_is_never_used() -> None:
    old = replace(
        assessed("adjust", "0.5", "0.6", "0.7", "promethazine"), as_of=datetime(2028, 2, 1, tzinfo=UTC)
    )
    proposal = propose(
        ask("promethazine_25_tab", "ICL97"),
        given(lot_views=(lot("lot-pmz-tab-001", "promethazine_25_tab", summary=old),)),
    )
    assert proposal.path == "labeled" and proposal.potency_status == "stale"


def test_unknown_context_part_and_history_helpers() -> None:
    unknown = complete_context(other_medicines=part("unknown"))
    refused = propose(ask("acetaminophen_325_tab", "ICL63"), given(context=unknown))
    assert refused.refusal is not None and refused.refusal.code == "patient_context_missing"
    assert past(1, "acetaminophen", 650, rule_id="apap_pain").accounting["acetaminophen"].ucum == "mg"
