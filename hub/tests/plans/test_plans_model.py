"""Boundary parsing and pinned content integrity."""

import hashlib
import json
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.domain.errors import PlansDocumentInvalid
from crew_health.plans.domain.model import canonical_sha256, parse_plan

from .harness import document, obj, raw_document, rows, supplies


@pytest.mark.trace("R57", "AGENTS-8")
def test_package_example_preserves_order_rechecks_and_supply_units() -> None:
    doc = document()
    assert len(doc.plans) == 8
    p = doc.plan_for_condition("ICL10")
    assert p is not None
    assert [s.step_id for s in p.steps[:4]] == [
        "icl10_pulse_by_crewmate",
        "icl10_note_episodes",
        "icl10_avoid_triggers",
        "icl10_add_ecg_report",
    ]
    assert [s.ordinal for s in p.steps] == list(range(len(p.steps)))
    sub = p.step("icl10_pulse_at_oximeter")
    assert sub is not None and sub.substitute_only and sub.recheck is not None
    assert sub.recheck.to_be_set_by == "clinical review"
    eye = doc.plan_for_flow("eye_injury")
    assert eye is not None
    shield = eye.step("eye_object_shield")
    assert shield is not None
    assert shield.needs.supplies[0].per_use == Quantity(Decimal("1"), Unit("1"))
    assert doc.plan("missing") is None and doc.plan_for_condition("missing") is None
    assert doc.plan_for_flow("missing") is None and p.step("missing") is None
    raw = rows(raw_document()["plans"])[0]
    assert parse_plan(raw, supplies=supplies()) == p
    assert p.content_sha256 == canonical_sha256(raw)


@pytest.mark.trace("T28-17", "R57")
def test_rejected_plans_and_steps_are_excluded_and_recorded() -> None:
    raw = raw_document()
    plans = rows(raw["plans"])
    plans[1]["review_state"] = "rejected"
    steps = rows(rows(plans[0]["stages"])[1]["steps"])
    steps[0]["review_state"] = "rejected"
    doc = document(raw)
    assert doc.rejected_plan_ids == ("plan_eye_injury",)
    assert doc.rejected_step_ids == (("plan_icl10", "icl10_note_episodes"),)
    assert doc.plan("plan_eye_injury") is None
    p = doc.plan("plan_icl10")
    assert p is not None and p.step("icl10_note_episodes") is None
    assert p.steps[1].ordinal == 2


@pytest.mark.trace("R57", "AGENTS-8")
def test_bad_who_medicine_missing_and_mistyped_fields_have_precise_pointers() -> None:
    for key, bad in [
        ("who", "skill:"),
        ("who", "captain"),
        ("medicine", {"named_as": "as in the onboard procedure", "dose": 2}),
        ("opens_help", 1),
        ("text", None),
    ]:
        raw = raw_document()
        step = rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]
        step[key] = bad
        with pytest.raises(PlansDocumentInvalid) as exc:
            document(raw)
        assert exc.value.pointer.startswith("/plans/0/stages/0/steps/0/" + key)
    raw = raw_document()
    del rows(raw["plans"])[0]["title"]
    with pytest.raises(PlansDocumentInvalid) as exc:
        document(raw)
    assert exc.value.pointer == "/plans/0/title"


@pytest.mark.trace("R57", "AGENTS-8")
def test_unknown_supply_stays_unknown_and_scope_is_exclusive() -> None:
    raw = rows(raw_document()["plans"])[1]
    p = parse_plan(raw, supplies={})
    s = p.step("eye_object_shield")
    assert s is not None and not s.needs.supplies[0].known
    assert s.needs.supplies[0].per_use is None
    obj(raw["scope"])["condition_id"] = "ICL10"
    with pytest.raises(PlansDocumentInvalid) as exc:
        parse_plan(raw, supplies=supplies())
    assert exc.value.pointer == "/scope"


@pytest.mark.trace("R57", "T28-39")
@given(st.dictionaries(st.text(max_size=10), st.text(max_size=20), max_size=10))
def test_canonical_hash_matches_independent_json_encoding(value: dict[str, str]) -> None:
    expected = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert canonical_sha256(value) == expected


@pytest.mark.trace("R57", "AGENTS-8", "T28-18")
def test_malformed_nested_fields_and_nonfinite_supply_numbers_fail_at_boundary() -> None:
    for target, value, suffix in (
        ("needs", [], "/needs"),
        ("source_ids", "source", "/source_ids"),
        ("substitute_only", None, "/substitute_only"),
        ("recheck", {"cadence": "daily"}, "/recheck/n"),
        ("who", 4, "/who"),
        ("review_state", "unknown", "/review_state"),
    ):
        raw = raw_document()
        step = rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]
        step[target] = value
        with pytest.raises(PlansDocumentInvalid) as exc:
            document(raw)
        assert exc.value.pointer == "/plans/0/stages/0/steps/0" + suffix
    for amount in (0, -1, True, "2", float("nan"), float("inf")):
        raw = raw_document()
        eye_step = rows(rows(rows(raw["plans"])[1]["stages"])[1]["steps"])[1]
        rows(obj(eye_step["needs"])["supplies"])[0]["per_use"] = amount
        with pytest.raises(PlansDocumentInvalid) as exc:
            document(raw)
        assert exc.value.pointer == "/plans/1/stages/1/steps/1/needs/supplies/0/per_use"
    for raw in ({**raw_document(), "plans": "invalid"}, {**raw_document(), "sources": [False]}):
        with pytest.raises(PlansDocumentInvalid):
            document(raw)
    raw = raw_document()
    rows(raw["plans"])[1]["plan_id"] = "plan_icl10"
    with pytest.raises(PlansDocumentInvalid, match="duplicate_plan_id"):
        document(raw)
    raw = raw_document()
    stages = rows(rows(raw["plans"])[0]["stages"])
    rows(stages[1]["steps"])[0]["step_id"] = "icl10_pulse_by_crewmate"
    with pytest.raises(PlansDocumentInvalid, match="duplicate_step_id"):
        document(raw)


@pytest.mark.trace("R57", "T28-18")
def test_rejected_return_prepare_step_is_filtered_and_records_original_ordinal() -> None:
    raw = raw_document()
    stages = rows(rows(raw["plans"])[1]["stages"])
    ret = next(s for s in stages if s["stage_id"] == "return")
    rows(ret["prepare"])[0]["review_state"] = "rejected"
    doc = document(raw)
    assert ("plan_eye_injury", "eye_return_prepare") in doc.rejected_step_ids
    p = doc.plan_for_flow("eye_injury")
    assert p is not None and p.return_stage is not None and p.return_stage.prepare == ()


@pytest.mark.trace("R57", "AGENTS-1", "T28-39")
@given(st.text(min_size=1, max_size=100))
def test_configuration_edit_changes_rendered_content_and_pinned_hash_without_code_change(text: str) -> None:
    raw = raw_document()
    p = rows(raw["plans"])[0]
    p["title"] = text
    step = rows(rows(p["stages"])[1]["steps"])[0]
    step["text"] = text
    doc = document(raw)
    parsed = doc.plan("plan_icl10")
    assert parsed is not None and parsed.title == text
    assert parsed.steps[1].text == text and parsed.content_sha256 == canonical_sha256(p)
    assert document(raw) == doc


@pytest.mark.trace("R57", "T28-18", "AGENTS-5")
def test_return_rtdc_nested_shape_is_checked_before_canonical_encoding() -> None:
    for field, bad, suffix in (
        ("treated_best", None, "/treated_best"),
        ("treated_best", {"min_pct": 1}, "/treated_best/max_pct"),
        ("treated_best", {"min_pct": True, "max_pct": 10}, "/treated_best/min_pct"),
        ("treated_best", {"min_pct": float("inf"), "max_pct": 10}, "/treated_best/min_pct"),
        ("page", None, "/page"),
    ):
        raw = raw_document()
        ret = next(s for s in rows(rows(raw["plans"])[1]["stages"]) if s["stage_id"] == "return")
        obj(ret["rtdc"])[field] = bad
        with pytest.raises(PlansDocumentInvalid) as exc:
            document(raw)
        assert exc.value.pointer == "/plans/1/stages/5/rtdc" + suffix
