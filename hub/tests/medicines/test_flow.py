"""The medicines HTTP flow over E01's real SQLite station (E32 4.4, 6.2): context, working diagnosis, proposal,
confirmation in one transaction, manual administration, correction, lot movements and the refusals around them."""

from __future__ import annotations

import ast
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from httpx import Response
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .harness import (
    CMO,
    CREW_B,
    NOW,
    REPO,
    SCHEMAS,
    SUBJECT,
    Station,
    build_station,
    config_copy,
    headers,
    lunar,
)

CLIENT_TIME = "2028-03-01T07:59:00Z"
_counter = iter(range(10**6))


def _key() -> str:
    return f"key-{next(_counter):08d}"


def _schema(name: str) -> Draft202012Validator:
    resources = []
    for path in sorted(SCHEMAS.glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        resources.append((path.name, resource))
        if isinstance(schema, dict) and "$id" in schema:
            resources.append((str(schema["$id"]), resource))
    registry: Registry = Registry().with_resources(resources)
    return Draft202012Validator(json.loads((SCHEMAS / name).read_text(encoding="utf-8")), registry=registry)


def _valid(name: str, body: object) -> None:
    assert sorted(_schema(name).iter_errors(body), key=str) == []


def _post(
    st: Station, path: str, body: dict[str, object], token: str = "cmo", key: str | None = None, **extra: str
) -> Response:
    response: Response = st.client.post(path, json=body, headers=headers(token, key or _key(), **extra))
    return response


def _context(st: Station, subject: str = SUBJECT, **parts: dict[str, object]) -> None:
    states: dict[str, dict[str, object]] = {
        "allergies": {"state": "none_known"},
        "other_medicines": {"state": "none"},
        "renal": {"state": "normal"},
        "hepatic": {"state": "normal"},
        "conditions": {"state": "none"},
    }
    states.update(parts)
    for name, value in states.items():
        response = _post(
            st,
            f"/v1/people/{subject}/medication-context",
            {"part": name, "client_time": CLIENT_TIME, **value},
        )
        assert response.status_code == 201, response.text


def _diagnose(st: Station, condition: str = "ICL63", subject: str = SUBJECT) -> str:
    response = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {"condition_id": condition, "subject_person_id": subject, "client_time": CLIENT_TIME},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["working_diagnosis_id"])


def _propose(st: Station, diagnosis: str, product: str = "acetaminophen_325_tab") -> dict[str, object]:
    response = _post(
        st,
        "/v1/dose-proposals",
        {"working_diagnosis_id": diagnosis, "product_id": product, "client_time": CLIENT_TIME},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    _valid("dose-proposal.schema.json", body)
    return dict(body)


def _confirm(
    st: Station, proposal: dict[str, object], token: str = "cmo", key: str | None = None, **body: object
) -> Response:
    payload = {
        "lot_id": proposal["lot_id"],
        "overrides": [],
        "acknowledged_alert_ids": [],
        "client_time": CLIENT_TIME,
    }
    payload.update(body)
    return _post(
        st,
        f"/v1/dose-proposals/{proposal['proposal_id']}/confirm",
        payload,
        token,
        key,
        **{"If-Match": '"1"'},
    )


def _stock(st: Station, lot_id: str) -> Decimal:
    response = st.client.get("/v1/medicines/lots", headers=headers())
    _valid("medicine-lots-view.schema.json", response.json())
    (row,) = [x for x in response.json()["lots"] if x["lot_id"] == lot_id]
    return Decimal(str(row["quantity_now"]["value"]))


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


@pytest.mark.trace("T-54", "INV-M4", "R-E32-02")
def test_a_confirmed_proposal_writes_one_administration_and_draws_the_lot_once(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    assert proposal["outcome"] == "proposal" and proposal["amount"] == {"value": 2, "ucum": "{tbl}"}
    key = _key()
    first = _confirm(st, proposal, key=key)
    assert first.status_code == 201, first.text
    _valid("medication-administration.schema.json", first.json())
    assert first.json()["outside_engine"] is False
    again = _confirm(st, proposal, key=key)
    assert again.status_code == 201 and again.json()["administration_id"] == first.json()["administration_id"]
    assert _stock(st, "lot-apap-001") == 28
    assert st.rows("SELECT count(*) FROM med_movement") == [(1,)]
    second = _confirm(st, proposal)
    assert second.status_code == 409 and second.json()["code"] == "INVALID_TRANSITION"
    assert (
        st.client.get(f"/v1/dose-proposals/{proposal['proposal_id']}", headers=headers()).json()["state"]
        == "confirmed"
    )


@pytest.mark.trace("T-54")
def test_the_same_key_with_another_body_is_never_a_replay(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    key = _key()
    assert _confirm(st, proposal, key=key).status_code == 201
    other = _confirm(st, proposal, key=key, acknowledged_alert_ids=["x"])
    assert other.status_code in {409, 422} and other.json()["code"] in {
        "DUPLICATE_COMMAND",
        "IDENTITY_MISMATCH",
    }


@pytest.mark.trace("T-58")
def test_confirming_with_another_lot_is_refused_and_writes_nothing(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    response = _confirm(st, proposal, lot_id="lot-apap-002")
    assert response.status_code == 422 and response.json()["field_errors"][0]["field"] == "/lot_id"
    assert st.rows("SELECT count(*) FROM med_movement") == [(0,)]
    assert st.rows("SELECT count(*) FROM med_proposal_terminal") == [(0,)]


@pytest.mark.trace("T-61", "T-62", "INV-M6")
def test_who_may_confirm(st: Station) -> None:
    apap = _propose(st, _diagnose(st))
    assert _confirm(st, apap, token="subject_cmo").json()["code"] == "ROLE_NOT_ELIGIBLE"  # never the subject
    assert _confirm(st, apap, token="crew_b").json()["code"] == "ROLE_NOT_ELIGIBLE"  # not acting
    st.acting.pairs.add((CREW_B, SUBJECT))
    pmz = _propose(st, _diagnose(st, "ICL97"), "promethazine_25_tab")
    assert _confirm(st, pmz, token="crew_b").json()["code"] == "ROLE_NOT_ELIGIBLE"  # pmz is CMO only
    confirmed = _confirm(st, apap, token="crew_b")  # apap: any trained crew, the acting officer
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["confirmed_by"] == CREW_B


@pytest.mark.trace("T-54", "INV-M8")
def test_a_changed_context_supersedes_the_proposal_and_the_supersede_is_kept(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    adverse = _post(
        st,
        "/v1/medicines/inspections",
        {"lot_id": "lot-apap-001", "finding": "discoloration", "client_time": CLIENT_TIME},
    )
    assert adverse.status_code == 201
    response = _confirm(st, proposal)
    assert response.status_code == 409 and response.json()["current_state"]["state"] == "superseded"
    assert st.rows("SELECT state FROM med_proposal_terminal") == [("superseded",)]
    assert st.rows("SELECT count(*) FROM med_movement") == [(0,)]
    fresh = _propose(st, _diagnose(st))
    assert fresh["lot_id"] == "lot-apap-002"  # T-69: the adverse lot is excluded


@pytest.mark.trace("T-54", "C5")
def test_an_expired_or_restarted_proposal_is_never_confirmed(tmp_path: Path) -> None:
    st = build_station(tmp_path)
    _context(st)
    proposal = _propose(st, _diagnose(st))
    st.clock.at = NOW + timedelta(minutes=16)
    response = _confirm(st, proposal, client_time="2028-03-01T08:16:00Z")
    assert response.status_code == 409 and response.json()["current_state"]["state"] == "expired"
    st.clock.at = NOW
    other = _propose(st, _diagnose(st))
    st.clock.epoch = "epoch_2"  # the same database under a new process: the old epoch's proposals expire
    restarted = build_station(tmp_path, clock=st.clock)
    assert _confirm(restarted, other).json()["current_state"]["state"] == "expired"


@pytest.mark.trace("T-60", "INV-M5")
def test_an_override_alert_needs_the_officer_and_a_listed_reason(tmp_path: Path) -> None:
    st = build_station(tmp_path)
    _context(
        st,
        allergies={
            "state": "documented",
            "items": [{"substance_kind": "ingredient", "substance_id": "aspirin"}],
        },
    )
    proposal = _propose(st, _diagnose(st), "ibuprofen_400_tab")
    alerts = cast(list[dict[str, str]], proposal["alerts"])
    (alert,) = [a for a in alerts if a["level"] == "override_required"]
    missing = _confirm(st, proposal)
    assert missing.status_code == 422 and missing.json()["field_errors"][0]["field"] == "/overrides"
    st.acting.pairs.add((CREW_B, SUBJECT))
    override = [{"alert_id": alert["alert_id"], "reason_code": "benefit_outweighs_risk"}]
    assert _confirm(st, proposal, token="crew_b", overrides=override).json()["code"] == "ROLE_NOT_ELIGIBLE"
    wrong = [{"alert_id": alert["alert_id"], "reason_code": "not_a_reason"}]
    assert _confirm(st, proposal, overrides=wrong).json()["code"] == "ROLE_NOT_ELIGIBLE"
    done = _confirm(st, proposal, overrides=override)
    assert done.status_code == 201, done.text
    assert done.json()["overrides"][0]["reason_code"] == "benefit_outweighs_risk"


def _manual(
    st: Station, value: object, *, token: str = "cmo", ucum: str = "{tbl}", lot: str = "lot-apap-001"
) -> Response:
    return _post(
        st,
        "/v1/medication-administrations",
        {
            "subject_person_id": SUBJECT,
            "product_id": "acetaminophen_325_tab",
            "lot_id": lot,
            "amount": {"value": value, "ucum": ucum},
            "administered_at": "2028-03-01T07:30:00Z",
            "reason_text": "given from the kit during a loss of signal",
            "client_time": CLIENT_TIME,
        },
        token,
    )


@pytest.mark.trace("B06", "INV-M4", "INV-M1")
def test_a_manual_administration_is_bounded_by_stock_and_states_every_limit_it_breaks(st: Station) -> None:
    below = _manual(st, 31)
    assert below.status_code == 422 and below.json()["code"] == "SUPPLY_BELOW_ZERO"
    assert _manual(st, 2, token="subject_cmo").json()["code"] == "ROLE_NOT_ELIGIBLE"
    assert _manual(st, 650, ucum="mg").status_code == 422
    over = _manual(st, 11)  # 11 x 325 mg = 3575 mg, over the 3250 mg in 24 hours of LBL01
    assert over.status_code == 201, over.text
    body = over.json()
    _valid("medication-administration.schema.json", body)
    assert body["outside_engine"] is True
    assert "period_max_exceeded:acetaminophen" in body["limit_findings"]
    assert "per_administration_max_exceeded:acetaminophen" in body["limit_findings"]
    assert _stock(st, "lot-apap-001") == 19
    refused = _propose(st, _diagnose(st))  # the manual amount counts against the next proposal
    assert refused["outcome"] == "refusal"


@pytest.mark.trace("T-55")
def test_a_correction_marks_the_record_once_and_moves_no_stock(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    administration = _confirm(st, proposal).json()
    path = f"/v1/medication-administrations/{administration['administration_id']}/corrections"
    first = _post(st, path, {"reason_text": "recorded for the wrong person", "client_time": CLIENT_TIME})
    assert first.status_code == 201 and first.json()["status"] == "entered_in_error"
    assert _post(st, path, {"reason_text": "again", "client_time": CLIENT_TIME}).status_code == 409
    assert _stock(st, "lot-apap-001") == 28  # the tablets left the lot; a correction never puts them back
    assert _propose(st, _diagnose(st))["outcome"] == "proposal"  # it no longer counts toward the interval


@pytest.mark.trace("T-56")
def test_lot_movements_never_take_a_lot_below_zero(st: Station) -> None:
    path = "/v1/medicines/lots/lot-apap-001/movements"
    waste = _post(
        st, path, {"kind": "waste", "quantity": {"value": 31, "ucum": "{tbl}"}, "client_time": CLIENT_TIME}
    )
    assert waste.status_code == 422 and waste.json()["code"] == "SUPPLY_BELOW_ZERO"
    counted = _post(
        st,
        path,
        {"kind": "count_correction", "quantity": {"value": 12, "ucum": "{tbl}"}, "client_time": CLIENT_TIME},
    )
    assert counted.status_code == 201, counted.text
    assert _stock(st, "lot-apap-001") == 12
    assert (
        _post(
            st,
            path,
            {"kind": "return", "quantity": {"value": 1, "ucum": "{tbl}"}, "client_time": CLIENT_TIME},
        ).status_code
        == 201
    )
    assert _stock(st, "lot-apap-001") == 13


@pytest.mark.trace("T-75", "AGENTS-7")
def test_the_medication_context_is_private_to_the_subject_and_the_officers(st: Station) -> None:
    path = f"/v1/people/{SUBJECT}/medication-context"
    assert st.client.get(path, headers=headers("crew_b")).status_code == 404
    mine = st.client.get(path, headers=headers("subject"))
    assert mine.status_code == 200
    _valid("medication-context.schema.json", mine.json())
    assert st.client.get(path, headers=headers("cmo")).status_code == 200
    assert (
        st.client.get(f"/v1/medication-administrations?subject={SUBJECT}", headers=headers("crew_b")).json()[
            "items"
        ]
        == []
    )


@pytest.mark.trace("B07", "T-68")
def test_fhir_export_and_assays_are_refused_not_dropped(st: Station) -> None:
    fhir = st.client.get("/v1/medication-administrations/meda_1/fhir", headers=headers())
    assert fhir.status_code == 422 and fhir.json()["code"] == "CONTEXT_INAPPLICABLE"
    assay = _post(st, "/v1/medicines/assays", {})
    assert assay.json()["code"] == "CAPABILITY_UNAVAILABLE"


@pytest.mark.trace("M-L6", "INV-M2")
def test_a_live_mission_refuses_proposals_from_example_documents(tmp_path: Path) -> None:
    st = build_station(tmp_path, mission=lunar("LIVE"))
    _context(st)
    response = _post(
        st,
        "/v1/dose-proposals",
        {
            "working_diagnosis_id": _diagnose(st),
            "product_id": "acetaminophen_325_tab",
            "client_time": CLIENT_TIME,
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "CONTEXT_INAPPLICABLE"


@pytest.mark.trace("C6")
def test_a_client_time_ahead_of_the_hub_clock_is_refused(st: Station) -> None:
    response = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {"condition_id": "ICL63", "subject_person_id": SUBJECT, "client_time": "2028-03-01T09:00:00Z"},
    )
    assert response.status_code == 422 and response.json()["field_errors"][0]["field"] == "/client_time"


@pytest.mark.trace("AGENTS-1")
def test_a_reviewed_rule_edit_reaches_the_proposal_through_configuration_only(tmp_path: Path) -> None:
    def ground_words(treatments: dict[str, object], policy: dict[str, object]) -> None:
        treatments["unit_display"]["{tbl}"] = "caplets"  # type: ignore[index]

    st = build_station(tmp_path / "hub", config_dir=config_copy(tmp_path, ground_words))
    _context(st)
    assert "caplets" in str(_propose(st, _diagnose(st))["amount_text"])


@pytest.mark.trace("T-59", "AGENTS-4")
def test_no_assistant_or_tool_module_can_reach_medicines() -> None:
    root = REPO / "hub/src/crew_health"
    offenders = []
    for module in ("assistant", "tools", "model"):
        for path in sorted((root / module).rglob("*.py")) if (root / module).is_dir() else []:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "crew_health.medicines"
                ):
                    offenders.append(str(path))
    assert offenders == []


def test_the_station_uses_the_officer_session(st: Station) -> None:
    assert CMO.startswith("person_")
