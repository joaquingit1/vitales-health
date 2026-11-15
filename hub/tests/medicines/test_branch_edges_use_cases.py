"""Edges of the medicines commands and reads (E32 4.3, 4.4; SYSTEM 5.2, 5.8, 8.3, 8.4): who may act or read when a
provider is absent or the caller is a station, replays, a mission that changes mid command, unknown targets,
acknowledgements, the plan grant of an acting officer, the lots board by environment and active medicine names."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.commands import AcceptedReceipt, StoredResult, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.medicines.application import use_cases
from crew_health.medicines.application.use_cases import (
    Assay,
    Command,
    MedicinesResultVisibility,
    active_medicine_names,
    administration_view,
    get_context,
    job_view,
    list_administrations,
    lots_view,
    record_assay,
    record_context,
    record_diagnosis,
    record_inspection,
    record_movement,
)
from crew_health.medicines.domain.units import Amount
from crew_health.supplies.application.ports import MissionFacts

from .harness import (
    CREW_B,
    NOW,
    SESSIONS,
    SUBJECT,
    Mission,
    Station,
    build_station,
    fixture_config,
    headers,
    lunar,
)
from .test_flow import CLIENT_TIME, _confirm, _context, _diagnose, _key, _post, _propose

STATION = SessionContext(
    "session_station", "station", Actor("station", "station_1"), None, frozenset({Role.STATION})
)
CLIENT = datetime(2028, 3, 1, 7, 59, tzinfo=UTC)


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


def _cmd() -> Command:
    key = _key()
    return Command(key, hashlib.sha256(key.encode()).hexdigest(), "cor_" + key)


def _code(call: Any, *args: object) -> str:
    with pytest.raises(ProblemError) as refused:
        call(*args)
    return str(refused.value.code.value)


# ---- who may act or read ----


@pytest.mark.trace("SYSTEM-8.3", "SYSTEM-8.4", "E32-4.4")
def test_a_station_session_never_reads_a_record_nor_acts_as_a_clinician(st: Station) -> None:
    assert _code(get_context, st.deps, STATION, SUBJECT) == "UNAUTHORIZED_SCOPE"
    assert _code(list_administrations, st.deps, STATION, SUBJECT) == "UNAUTHORIZED_SCOPE"
    assert _code(lots_view, st.deps, STATION, None) == "UNAUTHORIZED_SCOPE"
    tablets = Amount(Decimal(1), "{tbl}")
    assert (
        _code(record_movement, st.deps, STATION, _cmd(), "lot-apap-001", "waste", tablets, CLIENT)
        == "ROLE_NOT_ELIGIBLE"
    )
    assert st.rows("SELECT count(*) FROM medicines_movement") == [(0,)]


@pytest.mark.trace("SYSTEM-8.4", "E32-3.7", "Q14")
def test_without_the_authority_provider_only_the_officer_role_records_and_nobody_is_acting(
    st: Station,
) -> None:
    st.assign_acting(CREW_B, SUBJECT)  # never read: the provider is absent
    st.rebind(authority=None)
    tablets = Amount(Decimal(1), "{tbl}")
    done = record_movement(st.deps, SESSIONS["cmo"], _cmd(), "lot-apap-001", "waste", tablets, CLIENT)
    assert done.status == 201 and done.body["quantity_now"] == {"value": Decimal(29), "ucum": "{tbl}"}
    assert (
        _code(record_movement, st.deps, SESSIONS["crew_b"], _cmd(), "lot-apap-001", "waste", tablets, CLIENT)
        == "ROLE_NOT_ELIGIBLE"
    )


@pytest.mark.trace("E32-4.4", "E32-5.3")
def test_only_crew_or_the_officer_record_inspections_and_only_the_officer_records_assays(st: Station) -> None:
    assert (
        _code(
            record_inspection, st.deps, SESSIONS["operator"], _cmd(), "lot-apap-001", "no_change", "", CLIENT
        )
        == "UNAUTHORIZED_SCOPE"
    )
    assay = Assay(
        "lot-pmz-tab-001",
        "promethazine",
        datetime(2028, 3, 1, 7, 30, tzinfo=UTC),
        "total",
        Decimal("96.5"),
        Decimal("1.2"),
        "onboard",
        "HPLC",
        "station",
        "",
    )
    assert _code(record_assay, st.deps, SESSIONS["crew_b"], _cmd(), assay, CLIENT) == "UNAUTHORIZED_SCOPE"
    assert st.rows("SELECT count(*) FROM medicines_record WHERE kind IN ('assay', 'inspection')") == [(0,)]


@pytest.mark.trace("SYSTEM-5.8", "T-75")
def test_a_saved_result_without_a_record_reveals_nothing_and_one_with_a_subject_needs_the_grant(
    st: Station,
) -> None:
    diagnosis = _diagnose(st)
    identity = command_identity(
        key="key-visibility",
        actor_ref="person_b",
        operation="recordWorkingDiagnosis",
        target="ep_1",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    receipt = AcceptedReceipt(identity.key, identity.operation, identity.target, "receipt_1", NOW)
    visibility = MedicinesResultVisibility(st.deps)
    with st.db.snapshot() as view:
        # a stored result with no record: nothing to reveal, so not even a station is refused
        visibility.authorize(view, STATION, identity, StoredResult(receipt, 201, None, "x", None))
        saved = StoredResult(receipt, 201, diagnosis, "accepted", None)
        visibility.authorize(view, SESSIONS["cmo"], identity, saved)  # the subject shared it with the officer
        with pytest.raises(ProblemError) as refused:
            visibility.authorize(view, SESSIONS["crew_b"], identity, saved)
    assert refused.value.code.value == "UNAUTHORIZED_SCOPE"


# ---- the command frame: clock, mission, replay ----


@pytest.mark.trace("REVIEW-B3", "E32-C4", "SYSTEM-5.2")
def test_a_correction_older_than_the_jump_never_clears_it(st: Station) -> None:
    st.corrections.record(st.clock.at)  # version 1, before the jump
    _diagnose(st)
    st.clock.jump(timedelta(hours=4))
    refused = _propose(st, _diagnose(st))
    assert cast(dict[str, Any], refused["refusal"])["code"] == "clock_discontinuity"
    assert st.rows("SELECT correction_version FROM medicines_clock_check WHERE kind = 'discontinuity'") == [
        (1,)
    ]
    st.corrections.record(st.clock.at)  # version 2, after it
    assert _propose(st, _diagnose(st))["outcome"] == "proposal"


@pytest.mark.trace("E32-C1", "AGENTS-3")
def test_without_the_clock_port_commands_still_run_and_store_no_reading(st: Station) -> None:
    before = st.rows("SELECT count(*) FROM medicines_clock_check")
    st.rebind(clock=None)
    assert _propose(st, _diagnose(st))["outcome"] == "proposal"
    assert st.rows("SELECT count(*) FROM medicines_clock_check") == before


@dataclass
class _Moving(Mission):
    """The mission mode changes between the command's read and its unit of work."""

    calls: int = 0

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        self.calls += 1
        return lunar("SIM") if self.calls == 1 else lunar("REPLAY")


@pytest.mark.trace("INV-M7", "SYSTEM-5.8")
def test_a_mission_that_changes_mid_command_is_a_retryable_refusal_that_writes_nothing(st: Station) -> None:
    st.rebind(mission=_Moving(lunar()))
    with pytest.raises(ProblemError) as refused:
        record_diagnosis(st.deps, SESSIONS["cmo"], _cmd(), "ep_1", SUBJECT, "ICL63", "", "", None, CLIENT)
    assert refused.value.code.value == "INTERNAL_ERROR" and refused.value.retryable is True
    assert st.rows("SELECT count(*) FROM medicines_record WHERE kind = 'diagnosis'") == [(0,)]


@pytest.mark.trace("SYSTEM-5.8", "AGENTS-6")
def test_a_retried_diagnosis_replays_the_same_record(st: Station) -> None:
    body: dict[str, object] = {
        "condition_id": "ICL63",
        "subject_person_id": SUBJECT,
        "client_time": CLIENT_TIME,
    }
    first = _post(st, "/v1/episodes/ep_1/working-diagnoses", body, key="key-retry-diag")
    again = _post(st, "/v1/episodes/ep_1/working-diagnoses", body, key="key-retry-diag")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert st.rows("SELECT count(*) FROM medicines_record WHERE kind = 'diagnosis'") == [(1,)]


@pytest.mark.trace("T-54", "C5", "SYSTEM-5.8")
def test_a_retried_confirmation_of_an_expired_proposal_replays_the_expired_proposal(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    st.clock.at = st.clock.at + timedelta(hours=1)
    first = _confirm(st, proposal, key="key-retry-confirm")
    again = _confirm(st, proposal, key="key-retry-confirm")
    assert first.status_code == again.status_code == 409
    assert again.json()["current_state"] == first.json()["current_state"]
    assert again.json()["current_state"]["state"] == "expired"
    assert again.json()["current_state"]["proposal_id"] == proposal["proposal_id"]
    assert st.rows("SELECT count(*) FROM medicines_record WHERE kind = 'administration'") == [(0,)]


# ---- unknown targets ----


@pytest.mark.trace("E32-4.4")
def test_unknown_jobs_proposals_administrations_and_parts_are_refused(st: Station) -> None:
    with st.db.snapshot() as view:
        assert _code(job_view, st.deps, view, "medr_none") == "NOT_FOUND"
        assert _code(administration_view, st.deps, view, "meda_none") == "NOT_FOUND"
    diagnosis = _diagnose(st)
    with st.db.snapshot() as view:  # a record of another kind is not a proposal
        assert _code(use_cases.proposal_view, st.deps, view, diagnosis) == "NOT_FOUND"
    assert (
        _code(record_context, st.deps, SESSIONS["cmo"], _cmd(), SUBJECT, "diet", "none", (), CLIENT)
        == "INVALID_VALUE"
    )
    manual = _post(
        st,
        "/v1/medication-administrations",
        {
            "subject_person_id": SUBJECT,
            "product_id": "acetaminophen_325_tab",
            "lot_id": "lot-none",
            "amount": {"value": 1, "ucum": "{tbl}"},
            "administered_at": "2028-03-01T07:30:00Z",
            "reason_text": "given from the kit",
            "client_time": CLIENT_TIME,
        },
    )
    assert manual.status_code == 404 and manual.json()["field_errors"][0]["field"] == "/lot_id"


@pytest.mark.trace("T-50", "SYSTEM-8.3")
def test_a_diagnosis_cannot_supersede_another_crew_members_diagnosis(st: Station) -> None:
    _context(st, subject=CREW_B)
    other = _diagnose(st, subject=CREW_B)
    response = _post(
        st,
        "/v1/episodes/ep_1/working-diagnoses",
        {
            "condition_id": "ICL63",
            "subject_person_id": SUBJECT,
            "supersedes": other,
            "client_time": CLIENT_TIME,
        },
    )
    assert response.status_code == 404 and response.json()["field_errors"][0]["field"] == "/supersedes"


# ---- proposals and confirmation ----


@pytest.mark.trace("T-54", "INV-M8")
def test_a_proposal_for_other_ingredients_leaves_the_open_one_proposed(st: Station) -> None:
    diagnosis = _diagnose(st)
    apap = _propose(st, diagnosis)
    _propose(st, diagnosis, "ibuprofen_400_tab")
    shown = st.client.get(f"/v1/dose-proposals/{apap['proposal_id']}", headers=headers()).json()
    assert shown["state"] == "proposed"
    assert _confirm(st, apap).status_code == 201


@pytest.mark.trace("T-60", "E32-3.7")
def test_an_acknowledge_alert_must_be_acknowledged_before_its_overrides_are_read(st: Station) -> None:
    _context(
        st,
        other_medicines={
            "state": "documented",
            "items": [{"ingredient_id": "acetaminophen", "name": "Tylenol"}],
        },
    )
    proposal = _propose(st, _diagnose(st))
    levels = {a["kind"]: a["level"] for a in cast(list[dict[str, str]], proposal["alerts"])}
    assert levels["duplicate_ingredient"] == "acknowledge"
    assert levels["outside_medicine_overlap"] == "override_required"
    unacknowledged = _confirm(st, proposal)
    assert unacknowledged.status_code == 422
    assert unacknowledged.json()["field_errors"][0]["field"] == "/acknowledged_alert_ids"
    acknowledged = ["duplicate_ingredient:acetaminophen"]
    no_override = _confirm(st, proposal, acknowledged_alert_ids=acknowledged)
    assert no_override.status_code == 422 and no_override.json()["field_errors"][0]["field"] == "/overrides"
    override = [
        {"alert_id": "outside_medicine_overlap:acetaminophen", "reason_code": "benefit_outweighs_risk"}
    ]
    done = _confirm(st, proposal, acknowledged_alert_ids=acknowledged, overrides=override)
    assert done.status_code == 201, done.text


@pytest.mark.trace("Q14", "SYSTEM-8.4", "E32-3.7")
def test_an_acting_officer_needs_the_plan_provider_to_confirm_an_officer_only_product(st: Station) -> None:
    st.consent.grants.add((SUBJECT, CREW_B, "*"))
    st.assign_acting(CREW_B, SUBJECT)
    st.plans.named.add((SUBJECT, "ICL97", "promethazine_25_tab"))
    diagnosis = _diagnose(st, "ICL97")
    blocked = _propose(st, diagnosis, "promethazine_25_tab")
    assert blocked["outcome"] == "proposal"
    st.rebind(plans=None)  # absent provider: no plan grants it
    assert _confirm(st, blocked, token="crew_b").json()["code"] == "ROLE_NOT_ELIGIBLE"
    st.rebind(plans=st.plans)
    allowed = _confirm(st, blocked, token="crew_b")
    assert allowed.status_code == 201, allowed.text


# ---- the lots board ----


@pytest.mark.trace("E32-4.3", "INV-M7")
def test_the_board_lists_the_lots_in_custody_of_the_asked_environment_at_this_instant(st: Station) -> None:
    here = lots_view(st.deps, SESSIONS["cmo"], None)
    assert here["environment_id"] == "lunar_surface" and len(cast(list[object], here["lots"])) > 0
    assert lots_view(st.deps, SESSIONS["cmo"], "transit")["lots"] == []  # the lots left transit_kit on 01-15
    st.clock.at = datetime(2028, 1, 12, tzinfo=UTC)
    transit = lots_view(st.deps, SESSIONS["operator"], "transit")
    assert transit["environment_id"] == "transit"
    assert {"lot-apap-001", "lot-pmz-tab-001"} <= {
        str(x["lot_id"]) for x in cast(list[dict[str, object]], transit["lots"])
    }
    st.clock.at = datetime(2027, 1, 1, tzinfo=UTC)  # before any lot entered custody
    assert lots_view(st.deps, SESSIONS["cmo"], None)["lots"] == []


@pytest.mark.trace("R-E32-04", "DD9")
def test_a_queued_run_keeps_the_completed_one_and_an_unseen_finish_reads_unknown(tmp_path: Path) -> None:
    st = build_station(tmp_path, config_dir=fixture_config(tmp_path / "config-fixture-lots"))
    assert st.drain() == 1
    completed = cast(dict[str, Any], lots_view(st.deps, SESSIONS["cmo"], None)["assessment"])
    assert completed["status"] == "completed"
    queued = st.client.post("/v1/medicines/assessments", headers=headers("operator", _key()))
    assert queued.status_code == 202 and queued.json()["state"] == "queued"
    board = cast(dict[str, Any], lots_view(st.deps, SESSIONS["cmo"], None)["assessment"])
    assert board["run_id"] == completed["run_id"] and board["status"] == "completed"
    st.rebind(limits=replace(st.deps.limits, max_rows=1))  # the bounded page holds only the queued run
    bounded = cast(dict[str, Any], lots_view(st.deps, SESSIONS["cmo"], None)["assessment"])
    assert bounded["run_id"] == completed["run_id"] and bounded["status"] == "unknown"


# ---- active medicine names ----


@pytest.mark.trace("E32-4.3", "T-75")
def test_active_names_skip_unnamed_and_ended_outside_medicines_and_a_subject_without_a_statement(
    st: Station,
) -> None:
    assert active_medicine_names(st.deps, SESSIONS["cmo"], CREW_B) == ()  # nothing recorded for person_b
    _context(
        st,
        other_medicines={
            "state": "documented",
            "items": [
                {"ingredient_id": "loratadine", "name": "Claritin"},
                {"ingredient_id": "cetirizine"},
                {"ingredient_id": "zolpidem", "name": "Ambien", "until": "2028-02-01T00:00:00Z"},
                {"ingredient_id": "melatonin", "name": "Melatonin", "until": "2028-04-01T00:00:00Z"},
            ],
        },
    )
    assert active_medicine_names(st.deps, SESSIONS["cmo"], SUBJECT) == ("Claritin", "Melatonin")
