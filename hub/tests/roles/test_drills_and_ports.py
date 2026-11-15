"""Drills over HTTP, event practice, plan run actions, the provided ports and the module registration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from crew_health.app.import_contracts import check_features
from crew_health.app.registry import DeclarationRegistry
from crew_health.guidance.application.ports import Known, Unknown
from crew_health.guidance.application.ports import SkillFacts as GuidanceSkillFacts
from crew_health.kernel.uow import Actor
from crew_health.roles import module
from crew_health.roles.domain.drill import Participation, patches
from crew_health.roles.domain.rules import parse_practice
from crew_health.roles.module import register

from .harness import NOW, REPO, Harness, assignments, example, help_session, obj, objs
from .test_config import _validator

DRILL: dict[str, object] = {
    "scenario_id": "drill_pulse_crewmate",
    "skill_ids": ["pulse_check_wrist"],
    "participant_ids": ["tomas", "mara"],
    "origin_mode": "SIM",
}
DONE = {"client_time": "2026-11-13T09:50:00Z"}
ENGINE = Actor("engine", "test")


def _start(
    h: Harness, body: Mapping[str, object] = DRILL, who: str = "tomas", key: str = "key-drill-1"
) -> tuple[int, dict[str, object]]:
    return h.post("/v1/drills", who, body, key=key)


@pytest.mark.trace("R54", "EI10")
def test_drill_start_complete_credits_participants(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    status, drill = _start(h)
    assert status == 201, drill
    assert drill["state"] == "running" and drill["stores_measurements"] is False
    assert [p["person_id"] for p in objs(drill["participants"])] == ["mara", "tomas"]  # roster order
    assert _start(h) == (201, drill)  # same key, same body: the same drill
    h.advance(600)
    path = f"/v1/drills/{drill['drill_id']}/complete"
    assert h.post(path, "tomas", DONE, key="key-done-1")[1]["code"] == "VERSION_REQUIRED"
    status, done = h.post(path, "tomas", {**DONE, "participant_ids": ["tomas"]}, key="key-done-2", if_match=1)
    assert status == 200, done
    assert done["state"] == "completed"
    skills = {s["skill_id"]: s for s in objs(h.get("/v1/me/skills", "tomas")[1]["skills"])}
    assert (skills["pulse_check_wrist"]["freshness"], skills["pulse_check_wrist"]["source"]) == (
        "fresh",
        "drill",
    )
    mara = {s["skill_id"]: s for s in objs(h.get("/v1/me/skills", "mara")[1]["skills"])}
    assert mara["pulse_check_wrist"]["freshness"] == "never"  # did not complete: no credit
    assert "skill.practiced" in h.uow.event_types()
    again = h.post(path, "tomas", DONE, key="key-done-3", if_match=2)
    assert again[0] == 409 and again[1]["code"] == "INVALID_TRANSITION"


@pytest.mark.trace("R54")
def test_drill_validation_fails_closed(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    cpr = {**DRILL, "scenario_id": "drill_cpr_manikin", "skill_ids": ["cpr"]}
    status, problem = _start(h, cpr, key="key-val-1")
    assert status == 422 and problem["code"] == "CONTEXT_INAPPLICABLE"  # manikin aboard unknown: not assumed
    h.facts.aboard = {"cpr_manikin": True}
    assert _start(h, cpr, key="key-val-2")[0] == 201
    wrong = {**DRILL, "skill_ids": ["cpr"]}
    assert _start(h, wrong, key="key-val-3")[1]["code"] == "INVALID_VALUE"
    assert _start(h, {**DRILL, "origin_mode": "LIVE"}, key="key-val-4")[1]["code"] == "ORIGIN_MISMATCH"
    assert (
        _start(h, {**DRILL, "participant_ids": ["tomas", "zed"]}, key="key-val-5")[1]["code"]
        == "INVALID_VALUE"
    )
    assert _start(h, {**DRILL, "participant_ids": ["sofia"]}, who="tomas", key="key-val-6")[0] == 403
    assert (
        _start(h, {**DRILL, "routine_occurrence_id": "occ_1"}, key="key-val-7")[1]["code"] == "INVALID_VALUE"
    )
    assert (
        _start(h, {**DRILL, "beats": 70}, key="key-val-8")[1]["code"] == "MALFORMED_REQUEST"
    )  # no measurement


@pytest.mark.trace("R54")
def test_drill_abandoned_at_day_end(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _, drill = _start(h)
    h.clock.at = NOW.replace(hour=0, minute=0, second=0) + timedelta(days=1, minutes=5)
    status, seen = h.get(f"/v1/drills/{drill['drill_id']}", "tomas")
    assert status == 200 and seen["state"] == "abandoned"
    assert seen["abandoned_at"] == "2026-11-14T00:00:00Z"
    assert h.provided.running_drill(str(drill["drill_id"])) is None


@pytest.mark.trace("R54")
def test_guided_check_gives_one_event_practice(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    payload: dict[str, object] = {
        "guided_check_id": "gck_1",
        "skill_id": "pulse_check_wrist",
        "state": "checked",
        "result": "agree",
        "drill_id": None,
        "origin_mode": "SIM",
        "helper": {"person_id": "mara", "display_name": "Mara Okafor"},
        "occurred_at": "2026-11-13T09:00:00Z",
    }
    for variant, expected in (
        ({**payload, "result": "disagree"}, 0),
        ({**payload, "drill_id": "drl_9"}, 0),
        ({**payload, "origin_mode": "LIVE"}, 0),
        (payload, 1),
        (payload, 0),  # a retried check: one practice at most
    ):
        with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
            assert h.skills.on_guided_check(uow, variant) == expected
    skills = {s["skill_id"]: s for s in objs(h.get("/v1/me/skills", "mara")[1]["skills"])}
    assert (skills["pulse_check_wrist"]["source"], skills["pulse_check_wrist"]["last_practiced_label"]) == (
        "event",
        "day 13",
    )


@pytest.mark.trace("V89", "D37")
def test_plan_run_accept_and_closed_run(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.open_plan(h.plan_run())
    (row,) = assignments(h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")[1])
    body = {
        "action": "accept",
        "role_assignment_id": row["role_assignment_id"],
        "client_time": "2026-11-13T09:36:50Z",
    }
    status, accepted = h.post(
        "/v1/plan-runs/prn_icl10_mara_1/roles", "tomas", body, key="key-plan-1", if_match=1
    )
    assert status == 200 and accepted["state"] == "accepted"
    assert h.provided.plan_run_person_ids("prn_icl10_mara_1") == frozenset({"tomas"})
    assert h.provided.holders("pulse_check_wrist", NOW) == ("tomas",)
    h.facts.plans["prn_icl10_mara_1"] = replace(h.plan_run(), state="closed")
    status, problem = h.post(
        "/v1/plan-runs/prn_icl10_mara_1/roles",
        "tomas",
        {**body, "action": "reassign", "person_id": "sofia"},
        key="key-plan-2",
        if_match=2,
    )
    assert status == 409 and problem["code"] == "INVALID_TRANSITION"


@pytest.mark.trace("R54")
def test_skill_facts_operators_follow_roster_and_overlays(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.facts.walk("ilya")
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        answer = h.provided.operators(uow, "mara", None)
    assert isinstance(answer, Known)
    facts = answer.value
    assert [f.person_id for f in facts] == ["mara", "ilya", "sofia", "tomas"]
    tomas, ilya = facts[3], facts[1]
    assert tomas.skill_freshness["pulse_check_wrist"] == "fresh"
    assert (ilya.on_eva, ilya.on_site, facts[0].skill_freshness["pulse_check_wrist"]) == (
        True,
        False,
        "stale",
    )


@pytest.mark.trace("R54")
def test_skill_facts_on_site_is_relative_to_the_patient(tmp_path: Path) -> None:
    """Away only when both places are known and differ, the same policy as the acting officer rule."""
    h = Harness(tmp_path)
    h.facts.locations = {"mara": "node_base", "sofia": "node_lab"}
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        at_patient = h.provided.operators(uow, None, "node_base")
        beside_subject = h.provided.operators(uow, "sofia", None)
    assert isinstance(at_patient, Known) and isinstance(beside_subject, Known)
    here = {f.person_id: f.on_site for f in at_patient.value}
    assert here == {"mara": True, "ilya": True, "sofia": False, "tomas": True}
    there = {f.person_id: f.on_site for f in beside_subject.value}
    assert there == {"mara": False, "ilya": True, "sofia": True, "tomas": True}


@pytest.mark.trace("R54")
def test_skill_facts_is_the_guidance_provider_and_fails_as_unknown(tmp_path: Path) -> None:
    """Guidance resolves SkillFacts by its own protocol class; a failed read is Unknown, never an empty roster."""
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    assert GuidanceSkillFacts in registry.providers
    h = Harness(tmp_path)
    h.facts.down.add("roster")
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        answer = h.provided.operators(uow, "mara", None)
    assert isinstance(answer, Unknown) and answer.provider == "roles"


@pytest.mark.trace("R54")
def test_responses_match_the_contract_schemas(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("mara", "cpr")
    h.practice("tomas", "cpr")
    h.open_help(help_session("hlp_station_cpr", subject="ilya", flow="not_breathing"))
    checks = (
        ("help-roles-view.schema.json", h.get("/v1/help-sessions/hlp_station_cpr/roles", "station")[1]),
        ("my-skills-view.schema.json", h.get("/v1/me/skills", "tomas")[1]),
        ("crew-readiness-view.schema.json", h.get("/v1/crew/readiness", "sofia")[1]),
        ("drill.schema.json", _start(h)[1]),
        ("error.schema.json", h.get("/v1/help-sessions/hlp_nope/roles", "mara")[1]),
    )
    for schema, body in checks:
        errors = list(_validator(schema).iter_errors(body))
        assert errors == [], (schema, errors[:2])
    assert isinstance(_validator("role-assignment.schema.json"), Draft202012Validator)
    for row in assignments(checks[0][1]):
        assert list(_validator("role-assignment.schema.json").iter_errors(row)) == []


@pytest.mark.trace("R54")
def test_practice_patches_from_completed_drills_only() -> None:
    rules = parse_practice(obj(example("config/participation-rules.example.json")))
    rows = [Participation(f"drl_{i}", "mara", NOW + timedelta(days=i), core_fresh=i == 2) for i in range(3)]
    kinds = [p.kind for p in patches("mara", "msn_moon_1", "SIM", rows, rules)]
    assert kinds == ["first_drill", "drill_count", "skill_fresh"]
    assert patches("mara", "msn_moon_1", "SIM", [], rules) == ()


def test_module_registers_without_side_effects_and_passes_import_rules() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=64)
    module.register(registry)
    check_features(REPO / "hub/src/crew_health", ("roles",))  # domain purity, layers, no model in safety
    assert registry.declarations["prefixes"] == {
        "rol": "role_assignment",
        "drl": "drill",
        "prc": "skill_practice",
    }
