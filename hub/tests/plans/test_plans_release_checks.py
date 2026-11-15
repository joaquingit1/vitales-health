"""C11 checks 14 and 15 reject unsafe releases with attributable findings."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal
from unittest.mock import patch

import pytest

from crew_health.plans.domain.model import PlansDocument, all_steps
from crew_health.plans.domain.release_checks import (
    CatalogFacts,
    KitFacts,
    ReleaseFacts,
    check_plans,
    plan_rows_aboard,
)

from .harness import context, document, obj, raw_document, rows, supplies


def facts_for(doc: PlansDocument, mode: Literal["LIVE", "REPLAY", "SIM"] = "LIVE") -> ReleaseFacts:
    ctx = context()
    environments = sorted({e for p in doc.plans for e in p.environments})
    steps = tuple(s for p in doc.plans for s in all_steps(p))
    instruments = frozenset(i for s in steps for i in s.needs.instruments)
    kit = KitFacts(
        instruments,
        supplies(),
        {env: instruments for env in environments},
        {env: ctx.stock or {} for env in environments},
        tuple((p.condition_id, p.plan_id) for p in doc.plans if p.condition_id is not None),
    )
    skills = frozenset(
        k
        for s in steps
        for k in (
            *s.needs.skills,
            *((s.who.skill_id,) if s.who.skill_id else ()),
            *((s.guided_check_skill_id,) if s.guided_check_skill_id else ()),
        )
    )
    landmarks = frozenset(s.landmark_id for s in steps if s.landmark_id is not None)
    flows = frozenset(
        (
            *tuple(p.flow_id for p in doc.plans if p.flow_id is not None),
            *tuple(s.opens_flow_id for s in steps if s.opens_flow_id is not None),
        )
    )
    quotes = {
        (q.source_id, q.page, q.quote): True for s in steps if (q := s.replaces_ground_task) is not None
    }
    return ReleaseFacts(
        kit,
        CatalogFacts(skills, landmarks, ()),
        flows,
        {env: True for env in environments},
        quotes,
        {p.plan_id: True for p in doc.plans},
        mode,
        100,
        100,
    )


def first_step(raw: dict[str, object]) -> dict[str, object]:
    return rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]


@pytest.mark.trace("T28-17", "T28-18", "T28-20", "R57")
def test_example_passes_all_release_checks_and_counts_every_condition() -> None:
    raw = raw_document()
    doc = document(raw)
    facts = facts_for(doc)
    assert check_plans(doc, raw, facts) == ()
    assert plan_rows_aboard(doc, facts) == (7, 7)


@pytest.mark.trace("T28-18", "EI4", "EI6", "EI11")
def test_each_check_rejects_a_minimal_content_edit() -> None:
    for mutation, expected in (
        ("order", "14a"),
        ("blocking", "14a"),
        ("return", "14b"),
        ("instrument", "14c"),
        ("substitute", "14c"),
        ("officer", "14d"),
        ("guided", "14d"),
        ("amount", "14e"),
        ("medicine", "14e"),
        ("quote", "14f"),
        ("rtdc", "14f"),
        ("recheck", "14g"),
        ("size", "14h"),
    ):
        raw = raw_document()
        original = document(raw)
        facts = facts_for(original)
        p = rows(raw["plans"])[0]
        stages = rows(p["stages"])
        step = first_step(raw)
        if mutation == "order":
            stages[0], stages[1] = stages[1], stages[0]
            p["stages"] = stages
        elif mutation == "blocking":
            stages[-1]["blocking"] = True
        elif mutation == "return":
            facts = replace(facts, return_options={})
        elif mutation == "instrument":
            obj(step["needs"])["instruments"] = ["unknown"]
        elif mutation == "substitute":
            rows(stages[2]["steps"])[1]["substitute_only"] = False
        elif mutation == "officer":
            rows(stages[1]["steps"])[0]["who"] = "crew_medical_officer"
        elif mutation == "guided":
            rows(stages[2]["steps"])[0]["guided_check_skill_id"] = "pulse_check_wrist"
        elif mutation == "amount":
            step["text_first_time"] = "Use 2 mg"
        elif mutation == "medicine":
            step["text"] = "Use pain relief"
        elif mutation == "quote":
            facts = replace(facts, quote_verified={})
        elif mutation == "rtdc":
            facts = replace(facts, rtdc_verified={})
        elif mutation == "recheck":
            recheck = obj(rows(stages[2]["steps"])[0]["recheck"])
            recheck["cadence"] = "daily"
            recheck["interval_seconds"] = 60
        elif mutation == "size":
            facts = replace(facts, max_plans_per_document=1, max_steps_per_plan=1)
        findings = check_plans(document(raw), raw, facts)
        matching = tuple(f for f in findings if f.check_id == expected)
        assert matching, (mutation, findings)
        assert all(f.severity == "reject" and f.pointer.startswith("/") and f.message for f in matching)


@pytest.mark.trace("T28-17", "T28-18")
def test_references_and_rejected_content_are_checked_after_filtering() -> None:
    for mutation in (
        "supply",
        "skill",
        "who_skill",
        "guided_skill",
        "landmark",
        "flow",
        "condition",
        "substitute_missing",
        "orphan",
        "rejected_substitute",
    ):
        raw = raw_document()
        facts = facts_for(document(raw))
        step = first_step(raw)
        stages = rows(rows(raw["plans"])[0]["stages"])
        if mutation == "supply":
            obj(step["needs"])["supplies"] = [
                {"supply_id": "unknown", "per_use": 1, "basis": "team default", "to_be_set_by": None}
            ]
        elif mutation == "skill":
            obj(step["needs"])["skills"] = ["unknown"]
        elif mutation == "who_skill":
            step["who"] = "skill:unknown"
        elif mutation == "guided_skill":
            step["guided_check_skill_id"] = "unknown"
        elif mutation == "landmark":
            step["landmark_id"] = "unknown"
        elif mutation == "flow":
            step["opens_flow_id"] = "unknown"
        elif mutation == "condition":
            obj(rows(raw["plans"])[0]["scope"])["condition_id"] = "ICL999"
        elif mutation == "substitute_missing":
            step["substitute_step_id"] = "unknown"
        elif mutation == "orphan":
            step["substitute_only"] = True
        else:
            rows(stages[2]["steps"])[1]["review_state"] = "rejected"
        assert any(f.check_id == "14c" for f in check_plans(document(raw), raw, facts)), mutation


@pytest.mark.trace("T28-18", "EI6")
def test_text_checks_include_every_content_location_and_fail_closed_on_exception() -> None:
    raw = raw_document()
    doc = document(raw)
    facts = facts_for(doc)
    for field in ("text", "text_first_time", "cannot_replace_text"):
        changed = raw_document()
        first_step(changed)[field] = "2 mg"
        assert any(
            f.check_id == "14e" and field in f.pointer for f in check_plans(document(changed), changed, facts)
        )
    changed = raw_document()
    eye = rows(changed["plans"])[1]
    ret = next(s for s in rows(eye["stages"]) if s["stage_id"] == "return")
    rows(ret["criteria"])[0]["text"] = "2 mg"
    assert any(
        f.check_id == "14e" and "/criteria/" in f.pointer
        for f in check_plans(document(changed), changed, facts)
    )
    rows(ret["criteria"])[0]["text"] = "Use medicine"
    rows(ret["prepare"])[0]["text"] = "2 mg"
    log = rows(eye["stages"])[-1]
    log["items"] = [{"text": "2 mg", "source_ids": []}]
    findings = check_plans(document(changed), changed, facts)
    assert any(f.check_id == "14e" and "/prepare/" in f.pointer for f in findings)
    assert any(f.check_id == "14e" and "/items/" in f.pointer for f in findings)
    facts = replace(facts, catalog=replace(facts.catalog, teach_texts=(("/teach/0/text", "2 mg"),)))
    assert any(f.check_id == "14e" and f.pointer == "/teach/0/text" for f in check_plans(doc, raw, facts))
    with patch("crew_health.plans.domain.release_checks.find_amounts", side_effect=RuntimeError("failure")):
        findings = check_plans(doc, raw, facts)
    assert any(f.check_id == "14e" and f.severity == "reject" and "failed" in f.message for f in findings)


@pytest.mark.trace("T28-18", "R57")
def test_nonconsumable_per_use_and_medicine_annotation_are_rejected() -> None:
    raw = raw_document()
    facts = facts_for(document(raw))
    step = first_step(raw)
    obj(step["needs"])["supplies"] = [
        {"supply_id": "towels", "per_use": 1, "basis": "team default", "to_be_set_by": None}
    ]
    step["text"] = "Use medicine as in the onboard procedure"
    findings = check_plans(document(raw), raw, facts)
    assert any(f.check_id == "14d" for f in findings)
    assert any(f.check_id == "14e" for f in findings)
    step["medicine"] = {"named_as": "as in the onboard procedure"}
    assert not any(f.check_id == "14e" for f in check_plans(document(raw), raw, facts))


@pytest.mark.trace("T28-17", "T28-20", "V82", "EI3")
def test_missing_aboard_plan_is_live_reject_sim_warning_and_count_gap() -> None:
    raw = raw_document()
    facts = facts_for(document(raw))
    for stage in rows(rows(raw["plans"])[0]["stages"]):
        for step in rows(stage.get("steps", [])):
            if step["who"] == "any_crew":
                step["who"] = "crew_medical_officer"
    doc = document(raw)
    modes: tuple[tuple[Literal["LIVE", "SIM", "REPLAY"], str], ...] = (
        ("LIVE", "reject"),
        ("SIM", "warning"),
        ("REPLAY", "warning"),
    )
    for mode, severity in modes:
        findings = [f for f in check_plans(doc, raw, replace(facts, mode=mode)) if f.check_id == "15"]
        assert any(
            "ICL10" in f.message and "lunar_surface" in f.message and f.severity == severity for f in findings
        )
    assert plan_rows_aboard(doc, facts) == (6, 7)
    for pid in (None, "absent", "plan_eye_injury"):
        broken = replace(facts, kit=replace(facts.kit, rows=(("ICL10", pid),)))
        assert any(f.check_id == "15" for f in check_plans(doc, raw, broken))
        assert plan_rows_aboard(doc, broken) == (0, 1)
    rows(raw["plans"])[0]["review_state"] = "rejected"
    assert any(f.check_id == "15" and "ICL10" in f.message for f in check_plans(document(raw), raw, facts))


@pytest.mark.trace("T28-18", "EI11")
def test_return_coverage_and_stage_membership_checks_reject_precise_content() -> None:
    raw = raw_document()
    facts = facts_for(document(raw))
    eye = rows(raw["plans"])[1]
    ret = next(s for s in rows(eye["stages"]) if s["stage_id"] == "return")
    ret["environments"] = ["uncovered"]
    assert any(
        f.check_id == "14b" and "uncovered" in f.message for f in check_plans(document(raw), raw, facts)
    )
    for modification in ("missing_log", "duplicate_stage", "flow_scope"):
        changed = raw_document()
        p = rows(changed["plans"])[0]
        stages = rows(p["stages"])
        if modification == "missing_log":
            p["stages"] = stages[:-1]
        elif modification == "duplicate_stage":
            stages[1]["stage_id"] = "check_another_way"
        else:
            obj(rows(changed["plans"])[1]["scope"])["flow_id"] = "unknown"
        expected = "14c" if modification == "flow_scope" else "14a"
        assert any(f.check_id == expected for f in check_plans(document(changed), changed, facts))


@pytest.mark.trace("T28-17", "R28-21", "EI3")
def test_uncatalogued_supply_passes_only_with_a_declared_unknown_quantity() -> None:
    cases = (
        ({"per_use": None, "to_be_set_by": "clinical review"}, False),
        ({"per_use": 1, "to_be_set_by": "clinical review"}, True),
        ({"per_use": None, "to_be_set_by": None}, True),
    )
    for fields, rejected in cases:
        raw = raw_document()
        facts = facts_for(document(raw))
        obj(first_step(raw)["needs"])["supplies"] = [
            {"supply_id": "not_in_catalog", "basis": "team default", **fields}
        ]
        findings = check_plans(document(raw), raw, facts)
        assert any(f.check_id == "14c" and "not_in_catalog" in f.message for f in findings) is rejected, (
            fields
        )
