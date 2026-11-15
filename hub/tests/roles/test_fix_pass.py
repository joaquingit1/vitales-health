"""Findings of the independent build review (probe e31probe/test_probe.py and the coordinator's list), each a test
that failed before its fix."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, EventScope, StoredEvent
from crew_health.roles.adapters.sqlite_repository import SqliteRolesRepository
from crew_health.roles.application.ports import AuthorityDecision
from crew_health.roles.application.proposals import ContextFacts
from crew_health.roles.application.repository import RecomputeRow
from crew_health.roles.application.world import World
from crew_health.roles.module import context_events, dispatch_event

from .harness import (
    NOW,
    ROLE_RULES,
    Harness,
    assignments,
    by_role,
    example,
    help_payload,
    help_session,
    obj,
    objs,
    person_of,
    unfilled,
)

ENGINE = Actor("engine", "fix")
T = "2026-11-13T09:40:00Z"


def _auth(h: Harness, who: str, subject: str) -> AuthorityDecision:
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        return h.provided.authority(
            uow,
            person_id=who,
            subject_person_id=subject,
            action="urgency_disposition",
            dose=None,
            at=h.clock.now(),
        )


def _accept(h: Harness, ctx: str, row: dict[str, object], key: str) -> tuple[int, dict[str, object]]:
    body = {"action": "accept", "role_assignment_id": row["role_assignment_id"], "client_time": T}
    return h.post(
        f"/v1/help-sessions/{ctx}/roles", person_of(row), body, key=key, if_match=int(str(row["revision"]))
    )


def _event(kind: EventType, body: dict[str, object]) -> StoredEvent:
    audience = Audience("crew", "object", AudienceProjectionKey("x", "event", 1), "policy")
    scope = EventScope("crew", None, None)
    return StoredEvent(1, "evt_1", kind, NOW, NOW, scope, "rel_1", None, json.dumps(body).encode(), audience)


def _dispatch(h: Harness, kind: EventType, body: dict[str, object]) -> None:
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        dispatch_event(h.proposals, h.skills, uow, _event(kind, body), body)


def _acting_tomas(h: Harness) -> dict[str, object]:
    h.practice("tomas", "cpr")
    h.practice("mara", "cpr", ref="pre_2")
    h.facts.walk("ilya")
    h.open_help(help_session("hlp_1", subject="sofia", flow="not_breathing", opener="mara"))
    mo = by_role(h.roles("hlp_1"))["medical_officer"]
    assert person_of(mo) == "tomas"
    status, body = _accept(h, "hlp_1", mo, "key-accept-mo")
    assert status == 200, body
    assert _auth(h, "tomas", "sofia").kind == "acting_medical_officer"
    return body


@pytest.mark.trace("R54", "EI9")
def test_1_acting_ends_when_an_officer_is_back_and_needs_a_new_acceptance(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _acting_tomas(h)
    h.facts.overlays.pop("ilya")
    h.overlay_event("ilya", "eva", "ended")
    h.proposals.run_due()
    assert _auth(h, "tomas", "sofia").kind == "none"
    assert [r.acting_medical_officer for r in h.provided.medical_officer_assignments("sofia")] == [False]
    assert "roles.acting_ended" in [a.event_type for a in h.uow.audits]
    h.facts.walk("ilya")
    h.overlay_event("ilya", "eva", "active")
    h.proposals.run_due()
    assert _auth(h, "tomas", "sofia").kind == "none"  # never silently back without a new acceptance


@pytest.mark.trace("EI9")
def test_2_walk_starting_mid_recompute_never_puts_the_walker_on_site(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    original = h.proposals._write
    fired: list[int] = []

    def racing(row: RecomputeRow, facts: ContextFacts | None, world: World | None) -> bool:
        if not fired:
            fired.append(1)
            h.facts.walk("ilya")
            h.overlay_event("ilya", "eva", "active")  # same clock instant as the request being processed
        return original(row, facts, world)

    h.proposals._write = racing  # type: ignore[method-assign]
    h.open_help(help_session("hlp_2", subject="mara", flow="fainting", opener="tomas"))
    on_site = [a for a in assignments(h.roles("hlp_2")) if a["role_id"] != "communicator"]
    assert all(person_of(a) != "ilya" for a in on_site)
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        pending = SqliteRolesRepository(uow.connection).due_recomputes(h.clock.now(), 10)
    assert [r.context_id for r in pending] == ["hlp_2"]  # the newer request survives
    h.proposals.run_due()
    view = h.roles("hlp_2")
    assert by_role(view)["medical_officer"] and person_of(by_role(view)["medical_officer"]) == "sofia"
    assert all(person_of(a) != "ilya" for a in assignments(view) if a["role_id"] != "communicator")


@pytest.mark.trace("R54", "T-E31-043")
def test_3a_freshness_timer_drops_a_stale_holder(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    h.open_plan(h.plan_run())
    h.advance(70 * 86400)  # past stale_at, no event at all
    h.facts.overlays.clear()
    h.proposals.run_due()
    page = h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1]
    assert assignments(page) == []
    assert h.provided.holders("pulse_check_wrist", h.clock.now()) == ()


@pytest.mark.trace("R54", "T-E31-043")
def test_3b_sim_to_live_switch_drops_sim_only_holders(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.open_plan(h.plan_run())
    assert len(assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1])) == 1
    assert h.facts.mission is not None
    h.facts.mission = replace(h.facts.mission, mission_mode="LIVE")
    assert EventType.ENVIRONMENT_ACTIVATED in context_events()
    _dispatch(h, EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "lunar_surface"})
    h.proposals.run_due()
    assert assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1]) == []


@pytest.mark.trace("R54", "T-E31-043")
def test_3c_release_activation_and_startup_sweep_recompute(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "cpr")
    h.open_help(help_session("hlp_3", subject="ilya", flow="not_breathing"))
    assert EventType.CONFIG_RELEASE_ACTIVATED in context_events()
    _dispatch(h, EventType.CONFIG_RELEASE_ACTIVATED, {"release_id": "rel_1"})
    assert h.proposals.run_due() == 1
    assert h.proposals.startup_sweep() == 1
    assert h.proposals.run_due() == 1


@pytest.mark.trace("EI9")
def test_4_accept_applies_the_hard_exclusions(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "cpr")
    h.open_help(help_session("hlp_4", subject="ilya", flow="not_breathing"))
    lead = by_role(h.roles("hlp_4"))["lead_helper"]
    h.facts.walk("tomas")  # the overlay event has not arrived yet
    status, problem = _accept(h, "hlp_4", lead, "key-accept-walker")
    assert status == 422 and objs(problem["field_errors"])[0]["code"] == "not_on_eva_for_on_site"


@pytest.mark.trace("EI9")
def test_5_older_context_event_never_overwrites_newer_state(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    newer = replace(help_session("hlp_5", subject="ilya", flow="not_breathing"), revision=3)
    h.open_help(newer)
    kit = by_role(h.roles("hlp_5"))["kit_fetcher"]
    assert person_of(kit) == "mara"
    stale = {
        **help_payload(newer),
        "revision": 2,
        "subject": {"kind": "crewmate", "person_id": "mara", "display_name": None},
    }
    _dispatch(h, EventType.HELP_SESSION_CHANGED, stale)
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        ctx = SqliteRolesRepository(uow.connection).context("help_session", "hlp_5")
    assert ctx is not None and ctx.subject_person_id == "ilya"
    assert person_of(by_role(h.roles("hlp_5"))["kit_fetcher"]) == "mara"


@pytest.mark.trace("EI9")
def test_6_acting_end_is_audited_on_reassign(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    mo = _acting_tomas(h)
    body = {
        "action": "reassign",
        "role_assignment_id": mo["role_assignment_id"],
        "person_id": "mara",
        "client_time": T,
    }
    status, _ = h.post("/v1/help-sessions/hlp_1/roles", "station", body, key="key-reassign-mo", if_match=2)
    assert status == 200
    assert "roles.acting_ended" in [a.event_type for a in h.uow.audits]


@pytest.mark.trace("R54")
def test_7_skill_practiced_also_reaches_the_crew_medical_officer(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    drill = {
        "scenario_id": "drill_pulse_crewmate",
        "skill_ids": ["pulse_check_wrist"],
        "participant_ids": ["tomas"],
        "origin_mode": "SIM",
    }
    _, started = h.post("/v1/drills", "tomas", drill, key="key-drill-7")
    h.post(
        f"/v1/drills/{started['drill_id']}/complete",
        "tomas",
        {"client_time": T},
        key="key-done-7",
        if_match=1,
    )
    practiced = [e for e in h.uow.events if e.type is EventType.SKILL_PRACTICED]
    assert {(e.scope.kind, e.audience.rule) for e in practiced} == {
        ("person", "roles.person"),
        ("crew", "roles.crew_medical_officer"),
    }


@pytest.mark.trace("V89")
def test_8_plan_run_action_checks_config_release(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.open_plan(h.plan_run())
    (row,) = assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1])
    body = {"action": "accept", "role_assignment_id": row["role_assignment_id"], "client_time": T}
    headers = {
        "X-Test-Session": "tomas",
        "Idempotency-Key": "key-release-1",
        "If-Match": '"1"',
        "Config-Release": "rel_old",
    }
    response = h.client.post("/v1/plan-runs/prn_icl10_mara_1/roles", json=body, headers=headers)
    assert response.status_code == 409 and response.json()["code"] == "CONFIG_VERSION_MISMATCH"
    headers = {**headers, "Config-Release": "rel_1", "Idempotency-Key": "key-release-2"}
    assert (
        h.client.post("/v1/plan-runs/prn_icl10_mara_1/roles", json=body, headers=headers).status_code == 200
    )


@pytest.mark.trace("R54")
def test_9_derived_flows_follow_the_derivation_rule() -> None:
    package = {f["flow_id"] for f in objs(example("config/role-rules.example.json")["flow_roles"])}
    for flow in objs(obj(json.loads(ROLE_RULES.read_text(encoding="utf-8")))["flow_roles"]):
        if flow["flow_id"] in package:
            continue
        for need in objs(flow["roles"]):
            if need["role_id"] in ("second_helper", "communicator"):
                assert need["required"] is False, flow["flow_id"]
            if need["role_id"] == "lead_helper":
                assert need["required"] is True


@pytest.mark.trace("EI9")
def test_10_overdue_counts_from_the_first_unmet_request_and_keeps_required(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    for person in ("tomas", "sofia"):
        h.practice(person, "cpr", ref=f"pre_{person}")
    h.open_help(help_session("hlp_10", subject=None, flow="not_breathing", opener="mara"))
    assert "second_helper" in by_role(h.roles("hlp_10"))
    h.facts.down.add("roster")
    h.overlay_event("ilya", "sleep", "active")  # first unmet request at NOW
    h.proposals.run_due()
    h.advance(20)
    h.overlay_event("ilya", "sleep", "ended")  # a later request for the same context
    h.advance(11)  # 31 s after the first request, 11 s after the latest
    h.proposals.run_due()
    view = h.roles("hlp_10")
    assert all(a["state"] != "proposed" for a in assignments(view))
    assert ("second_helper", False) in unfilled(view) and ("lead_helper", True) in unfilled(view)


@pytest.mark.trace("V89", "D37")
def test_11_role_assignment_reader_for_guided_checks(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.open_plan(h.plan_run())
    (row,) = assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1])
    body = {"action": "accept", "role_assignment_id": row["role_assignment_id"], "client_time": T}
    assert (
        h.post("/v1/plan-runs/prn_icl10_mara_1/roles", "tomas", body, key="key-reader-1", if_match=1)[0]
        == 200
    )
    record = h.provided.get(str(row["role_assignment_id"]))
    assert record is not None
    assert (record.role_assignment_id, record.person_id, record.state, record.skill_id) == (
        row["role_assignment_id"],
        "tomas",
        "accepted",
        "pulse_check_wrist",
    )
    assert (record.plan_run_id, record.help_session_id, record.retired) == ("prn_icl10_mara_1", None, False)
    h.facts.walk("tomas")
    h.overlay_event("tomas", "eva", "active")
    retired = h.provided.get(str(row["role_assignment_id"]))
    assert retired is not None and retired.retired is True
    assert h.provided.get("rol_unknown") is None
