"""End to end over HTTP: the router wired with the real domain, application and SQLite adapters, kernel fakes and
port fakes (E31 design 12; brief scenarios: officer present, away, incapacitated, nobody qualified, conflicting or
stale location facts, concurrent edits, the assistant never assigns)."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.kernel.uow import Actor, ReadSnapshot
from crew_health.roles.adapters.sqlite_repository import SqliteRolesRepository
from crew_health.roles.application.ports import AuthorityDecision
from crew_health.roles.application.provided import Action

from .harness import (
    NOW,
    Harness,
    assignments,
    by_role,
    help_payload,
    help_session,
    objs,
    person_of,
    texts,
    unfilled,
)

CPR = "hlp_station_cpr"
ACTION_TIME = "2026-11-13T09:40:00Z"


def _cpr(h: Harness, *, subject: str = "ilya") -> None:
    h.practice("mara", "cpr")
    h.practice("tomas", "cpr")
    h.open_help(help_session(CPR, subject=subject, flow="not_breathing", opener="mara"))


def _act(
    h: Harness, who: str, body: dict[str, object], key: str, if_match: int | None
) -> tuple[int, dict[str, object]]:
    return h.post(f"/v1/help-sessions/{CPR}/roles", who, body, key=key, if_match=if_match)


ENGINE = Actor("engine", "test")


@pytest.mark.trace("V88", "R54", "EI9")
def test_officer_incapacitated_cascade_to_second_officer(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)  # Ilya, first officer, is the patient
    view = h.roles(CPR)
    roles = by_role(view)
    assert person_of(roles["medical_officer"]) == "sofia"
    assert roles["medical_officer"]["reason_text"] == "Second medical officer"
    assert roles["medical_officer"]["context_reasons"] == [
        {"reason_code": "officer_is_subject", "text": "Ilya Brandt is the patient"}
    ]
    assert person_of(roles["lead_helper"]) == "tomas"  # tie with the opener Mara: the non-opener first (Q2)
    assert roles["lead_helper"]["reason_text"] == "CPR practiced day 12"
    assert person_of(roles["kit_fetcher"]) == "mara"
    assert unfilled(view) == [("second_helper", False), ("communicator", False)]
    assert all(person_of(a) != "ilya" for a in assignments(view))


@pytest.mark.trace("V88", "R54")
def test_officer_present_is_first_officer(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h, subject="mara")
    roles = by_role(h.roles(CPR))
    assert person_of(roles["medical_officer"]) == "ilya"
    assert roles["medical_officer"]["reason_text"] == "Medical officer"
    assert roles["medical_officer"]["context_reasons"] == []


@pytest.mark.trace("V89", "R54")
def test_officers_away_freshest_holder_takes_medical_officer(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    _cpr(h, subject="mara")
    roles = by_role(h.roles(CPR))
    assert person_of(roles["medical_officer"]) == "tomas"
    assert roles["medical_officer"]["reason_text"] == "CPR practiced day 12"
    assert texts(roles["medical_officer"]["context_reasons"]) == [
        "Ilya Brandt, medical officer, is on a surface walk until 11:40",
        "Sofia Reyes, medical officer, is on a surface walk until 11:40",
    ]
    on_site = [a for a in assignments(h.roles(CPR)) if a["role_id"] != "communicator"]
    assert all(person_of(a) not in ("ilya", "sofia") for a in on_site)
    assert person_of(by_role(h.roles(CPR))["communicator"]) == "ilya"  # off-site: a walker may keep the log


@pytest.mark.trace("V89", "R54", "EI9")
def test_nobody_qualified_fails_closed_and_says_so(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    h.open_help(help_session(CPR, subject="mara", flow="not_breathing"))  # nobody practiced CPR
    view = h.roles(CPR)
    roles = by_role(view)
    assert "lead_helper" not in roles and "medical_officer" not in roles
    assert ("lead_helper", True) in unfilled(view) and ("medical_officer", True) in unfilled(view)
    assert person_of(roles["kit_fetcher"]) == "tomas"  # an unskilled role still goes to a free person


@pytest.mark.trace("V89", "EI9")
def test_walk_without_planned_end_renders_unknown_time(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.facts.walk("ilya", until=None)  # Q9: E23 has no planned end yet
    h.facts.walk("sofia", until=NOW.replace(hour=8))  # a planned end already past is stale, never current
    h.open_plan(h.plan_run())
    (row,) = assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1])
    assert texts(row["context_reasons"]) == [
        "Ilya Brandt, medical officer, is on a surface walk until Unknown",
        "Sofia Reyes, medical officer, is on a surface walk until Unknown",
    ]


@pytest.mark.trace("EI9", "R54")
def test_conflicting_location_keeps_walker_off_site_and_grants_no_authority(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    h.facts.locations.update({"ilya": "node_base", "mara": "node_base", "sofia": "node_rover"})
    _cpr(h, subject="mara")
    roles = by_role(h.roles(CPR))
    assert person_of(roles["medical_officer"]) == "tomas"  # the walk overlay still excludes Ilya on site
    mo = roles["medical_officer"]
    status, accepted = _act(
        h,
        "tomas",
        {"action": "accept", "role_assignment_id": mo["role_assignment_id"], "client_time": ACTION_TIME},
        "key-accept-1",
        int(str(mo["revision"])),
    )
    assert status == 200, accepted
    decision = _authority(h, "tomas", "mara")
    assert accepted["state"] == "accepted"
    assert "roles.acting_started" not in [e.event_type for e in h.uow.audits]  # Ilya may be beside Mara
    assert (decision.kind, decision.reason) == ("none", "no_grant")  # conflicting facts: fail closed


@pytest.mark.trace("EI9", "R54")
def test_both_officers_certainly_away_grants_acting_authority(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    _cpr(h, subject="mara")
    mo = by_role(h.roles(CPR))["medical_officer"]
    status, _ = _act(
        h,
        "tomas",
        {"action": "accept", "role_assignment_id": mo["role_assignment_id"], "client_time": ACTION_TIME},
        "key-accept-2",
        1,
    )
    assert status == 200
    decision = _authority(h, "tomas", "mara")
    assert (decision.kind, decision.role_assignment_id) == (
        "acting_medical_officer",
        mo["role_assignment_id"],
    )
    assert _authority(h, "tomas", "mara", action="clinician_assessed").kind == "none"  # Q16
    assert _authority(h, "mara", "mara").reason == "own_case"
    h.facts.locations["ilya"] = (
        None  # stale or missing location of an officer: still away by the walk overlay
    )
    assert _authority(h, "tomas", "mara").kind == "acting_medical_officer"
    h.facts.down.add("overlays")  # a port that cannot answer never grants
    assert _authority(h, "tomas", "mara").kind == "none"


def _authority(
    h: Harness, person: str, subject: str, action: Action = "dose_confirmation"
) -> AuthorityDecision:
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        snapshot: ReadSnapshot = uow
        return h.provided.authority(
            snapshot,
            person_id=person,
            subject_person_id=subject,
            action=action,
            dose=None,
            at=h.clock.now(),
        )


@pytest.mark.trace("EI9", "R54")
def test_assistant_can_never_assign_a_safety_role(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    mo = by_role(h.roles(CPR))["medical_officer"]
    before = (len(h.uow.events), len(h.uow.audits))
    for action in ("accept", "reassign"):
        body = {"action": action, "role_assignment_id": mo["role_assignment_id"], "client_time": ACTION_TIME}
        if action == "reassign":
            body["person_id"] = "tomas"
        status, problem = _act(h, "assistant", body, f"key-asst-{action}", 1)
        assert status == 403 and problem["code"] == "UNAUTHORIZED_SCOPE"
    assert (len(h.uow.events), len(h.uow.audits)) == before
    after = by_role(h.roles(CPR))["medical_officer"]
    assert (person_of(after), after["state"], after["revision"]) == ("sofia", "proposed", 1)


@pytest.mark.trace("EI9")
def test_two_concurrent_edits_one_wins_one_stale(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    first = {"action": "reassign", "role_assignment_id": kit["role_assignment_id"], "person_id": "tomas"}
    second = {**first, "person_id": "sofia"}
    results: list[tuple[int, dict[str, object]]] = []
    lock = threading.Lock()

    def edit(who: str, body: dict[str, object], key: str) -> None:
        outcome = _act(h, who, {**body, "client_time": ACTION_TIME}, key, 1)
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=edit, args=("sofia", first, "key-race-a")),
        threading.Thread(target=edit, args=("ilya", second, "key-race-b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    statuses = sorted(s for s, _ in results)
    assert statuses == [200, 409]
    loser = next(b for s, b in results if s == 409)
    assert loser["code"] == "STALE_VERSION" and loser["current_state"] is not None
    winner = next(b for s, b in results if s == 200)
    assert winner["revision"] == 2 and winner["state"] == "changed"
    assert winner["reason_code"] == "changed_by_helper"


@pytest.mark.trace("EI9")
def test_reassign_kept_with_who_made_it_and_never_moved_by_engine(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    body = {
        "action": "reassign",
        "role_assignment_id": kit["role_assignment_id"],
        "person_id": "sofia",
        "client_time": ACTION_TIME,
    }
    status, changed = _act(h, "sofia", body, "key-reassign-1", 1)
    assert status == 200, changed
    assert changed["reason_text"] == "Changed by Sofia Reyes"
    assert changed["changed_by"] == {"kind": "person", "id": "sofia", "display_name": "Sofia Reyes"}
    h.practice("ilya", "cpr", ref="pre_2")  # new input: the engine recomputes
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        h.proposals.on_practice(uow)
    h.proposals.run_due()
    assert person_of(by_role(h.roles(CPR))["kit_fetcher"]) == "sofia"


@pytest.mark.trace("EI9", "V88")
def test_reassign_to_subject_or_walker_is_not_eligible(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.facts.walk("tomas")
    _cpr(h)
    roles = by_role(h.roles(CPR))
    kit = roles["kit_fetcher"]
    body = {"action": "reassign", "role_assignment_id": kit["role_assignment_id"], "client_time": ACTION_TIME}
    status, problem = _act(h, "sofia", {**body, "person_id": "ilya"}, "key-elig-1", 1)
    assert status == 422 and problem["code"] == "ROLE_NOT_ELIGIBLE"
    assert problem["field_errors"] == [
        {
            "field": "/person_id",
            "code": "never_subject",
            "message": "The person who needs help never gets a role.",
        }
    ]
    status, problem = _act(h, "sofia", {**body, "person_id": "tomas"}, "key-elig-2", 1)
    assert status == 422 and texts(problem["field_errors"], "code") == ["not_on_eva_for_on_site"]


@pytest.mark.trace("EI9")
def test_idempotent_replay_and_key_reuse(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    body = {
        "action": "reassign",
        "role_assignment_id": kit["role_assignment_id"],
        "person_id": "sofia",
        "client_time": ACTION_TIME,
    }
    first = _act(h, "sofia", body, "key-idem-1", 1)
    events = len(h.uow.events)
    again = _act(h, "sofia", body, "key-idem-1", 1)  # a retry after a lost response
    assert first == again and len(h.uow.events) == events
    other = _act(h, "sofia", {**body, "person_id": "mara"}, "key-idem-1", 2)
    assert other[0] == 409 and other[1]["code"] == "DUPLICATE_COMMAND"


@pytest.mark.trace("EI9")
def test_if_match_required_and_malformed_bodies(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    body = {"action": "accept", "role_assignment_id": kit["role_assignment_id"], "client_time": ACTION_TIME}
    assert _act(h, "mara", body, "key-ifm-1", None)[1]["code"] == "VERSION_REQUIRED"
    assert _act(h, "mara", {**body, "extra": 1}, "key-ifm-2", 1)[1]["code"] == "MALFORMED_REQUEST"
    assert _act(h, "mara", body, "short", 1)[1]["code"] == "MALFORMED_REQUEST"  # Idempotency-Key under 8
    reassign = {**body, "action": "reassign"}
    assert _act(h, "mara", reassign, "key-ifm-3", 1)[1]["code"] == "MALFORMED_REQUEST"  # no person_id
    status, problem = _act(h, "tomas", body, "key-ifm-4", 1)  # not the assignee
    assert status == 403 and problem["code"] == "UNAUTHORIZED_SCOPE"
    status, accepted = _act(h, "mara", body, "key-ifm-5", 1)
    assert status == 200 and accepted["state"] == "accepted" and accepted["allowed_actions"] == ["reassign"]


@pytest.mark.trace("EI9", "R54")
def test_walk_starts_retires_on_site_role_at_once_even_with_ports_down(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    lead = by_role(h.roles(CPR))["lead_helper"]
    assert person_of(lead) == "tomas"
    h.facts.walk("tomas")
    h.facts.down.update({"overlays", "roster"})
    h.overlay_event("tomas", "eva", "active")  # the handler needs no port
    roles = by_role(h.roles(CPR))
    assert "lead_helper" not in roles
    assert h.uow.event_types()[-1] == "role.changed"
    h.proposals.run_due()  # ports still down: backoff, nothing proposed
    h.advance(31)  # past roles.proposal_overdue_seconds
    h.proposals.run_due()
    view = h.roles(CPR)
    assert all(a["state"] != "proposed" for a in assignments(view))  # degraded: proposed rows leave
    h.facts.down.clear()
    h.advance(120)
    h.proposals.run_due()
    roles = by_role(h.roles(CPR))
    assert (
        person_of(roles["lead_helper"]) == "mara"
        and roles["lead_helper"]["reason_text"] == "CPR practiced day 12"
    )


@pytest.mark.trace("EI9")
def test_subject_change_retires_their_role_in_the_handler(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    assert person_of(kit) == "mara"
    snap = replace(help_session(CPR, subject="mara", flow="not_breathing"), revision=2)
    h.facts.down.add("help")
    h.facts.help[CPR] = snap
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        h.proposals.on_help_session_changed(uow, help_payload(snap))
        rows = SqliteRolesRepository(uow.connection).current("help_session", CPR)
    assert all(r.person_id != "mara" for r in rows)


@pytest.mark.trace("R54")
def test_privacy_skills_are_own_and_readiness_is_officer_only(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "cpr")
    status, mine = h.get("/v1/me/skills", "mara")
    assert status == 200
    assert {person_of(s) for s in objs(mine["skills"])} == {"mara"}
    assert texts(mine["skills"], "freshness") == ["never", "never"]
    status, theirs = h.get("/v1/me/skills", "tomas")
    cpr = next(s for s in objs(theirs["skills"]) if s["skill_id"] == "cpr")
    assert (cpr["freshness"], cpr["last_practiced_label"]) == ("fresh", "day 12")
    assert h.get("/v1/crew/readiness", "mara")[0] == 403
    assert h.get("/v1/me/skills", "station")[0] == 403
    status, readiness = h.get("/v1/crew/readiness", "sofia")
    assert status == 200
    skills = {s["skill_id"]: s for s in objs(readiness["skills"])}
    assert skills["cpr"]["fresh_count"] == 1 and skills["cpr"]["text"] == "CPR: 1 fresh"
    assert [person_of(p) for p in objs(skills["cpr"]["holders"])] == ["mara", "ilya", "sofia", "tomas"]


@pytest.mark.trace("R54")
def test_views_unknown_context_and_closed_session(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    assert h.get("/v1/help-sessions/hlp_nope/roles", "mara")[1]["code"] == "NOT_FOUND"
    _cpr(h)
    kit = by_role(h.roles(CPR))["kit_fetcher"]
    h.facts.help[CPR] = help_session(CPR, subject="ilya", flow="not_breathing", state="closed")
    body = {"action": "accept", "role_assignment_id": kit["role_assignment_id"], "client_time": ACTION_TIME}
    status, problem = _act(h, "mara", body, "key-closed-1", 1)
    assert status == 409 and problem["code"] == "SESSION_CLOSED"
    assert all(a["allowed_actions"] == [] for a in assignments(h.roles(CPR)))
    triage = help_session("hlp_triage", subject="ilya", flow=None)
    h.open_help(triage)
    view = h.roles("hlp_triage")
    assert view["assignments"] == [] and view["unfilled"] == []  # before a flow both lists are empty
