"""Computed configuration views, with guidance and D9 privacy applied on every read."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import pytest

from crew_health.plans.application.views import PlanViews

from .plans_stack import rows, stack


@pytest.mark.trace("T28-02", "EI7", "SYSTEM-8.2")
def test_unknown_content_never_reads_as_runnable(tmp_path: Path) -> None:
    s = stack(tmp_path)
    s.content.unknown = True
    result = PlanViews(s.world).condition_plan(s.session, "ICL10", None)
    for stage in rows(result["stages"]):
        for step in rows(stage.get("steps", [])):
            assert step["availability"] != "aboard"
            assert step["who_can"] == []


@pytest.mark.trace("T28-01", "T28-29", "EI7")
def test_lunar_golden_has_only_the_documented_safety_and_substitute_differences(tmp_path: Path) -> None:
    from .plans_stack import ROOT, load_json
    from .view_harness import install_golden_crew, validate_view

    s = stack(tmp_path)
    install_golden_crew(s)
    actual = PlanViews(s.world).condition_plan(s.session, "ICL10", None)
    validate_view(actual)
    expected = load_json(ROOT / "contracts/api/examples/plans/view-icl10-lunar.json")
    for key in ("release_id", "plan_version", "as_of"):
        expected[key] = actual[key]
    actual_stages, expected_stages = rows(actual["stages"]), rows(expected["stages"])
    for key in ("link_text", "leaves_at"):
        expected_stages[-1][key] = actual_stages[-1][key]
    # The source has substitute_only=true (R28-8); the old illustrative view has false.
    for stage in expected_stages:
        for step in rows(stage.get("steps", [])):
            if step["step_id"] == "icl10_pulse_at_oximeter":
                step["substitute_only"] = True
            # Both officers are on EVA. Guidance therefore refuses execution (SPEC-3 EI7).
            if step["step_id"] == "icl10_mo_review":
                step["availability"] = "not_aboard"
                step["availability_text"] = "Unknown"
    assert actual == expected


@pytest.mark.trace("T28-02", "SYSTEM-8.2")
def test_absent_guidance_and_absent_content_provider_fail_closed(tmp_path: Path) -> None:
    from crew_health.guidance.application.facade import EligibilityFacade, FactProviders, GuidanceBounds

    for guidance in (None, EligibilityFacade(GuidanceBounds(200, 32))):
        s = stack(tmp_path / ("absent" if guidance is None else "content"))
        if guidance is not None:
            guidance.attach(FactProviders(None, None, None, None, None))
        s.rebind(guidance=guidance)
        result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
        assert all(
            step["availability"] == "not_aboard" and step["who_can"] == []
            for stage in rows(result["stages"])
            for step in rows(stage.get("steps", []))
        )


@pytest.mark.trace("T28-02", "EI7")
def test_missing_camera_displays_only_a_guidance_executable_substitute(tmp_path: Path) -> None:
    import json

    from crew_health.kernel.config import ConfigName

    from .plans_stack import obj

    s = stack(tmp_path)
    kit = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    from typing import cast

    kits = obj(kit["kits"])
    kits["lunar_surface"] = [v for v in cast(list[str], kits["lunar_surface"]) if v != "camera_station"]
    s.equipment.removed.add("camera_station")
    s.replace_document(ConfigName.KIT_MAP, kit)
    result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    step = next(
        v
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
        if v["step_id"] == "icl10_pulse_at_cameras"
    )
    assert step["availability"] == "substituted"
    assert "oximeter" in str(step["availability_text"])
    s.content.unknown = True
    result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    assert all(
        v["availability"] != "substituted"
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
    )


@pytest.mark.trace("T28-29", "D9", "T28-36")
def test_environment_coverage_unknown_ids_and_content_privacy(tmp_path: Path) -> None:
    import json
    from dataclasses import replace

    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.config import ConfigName
    from crew_health.kernel.errors import ErrorCode, ProblemError
    from crew_health.kernel.uow import Actor

    from .plans_stack import obj

    s = stack(tmp_path)
    source = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    rows(source["plans"])[0]["environments"] = ["lunar_surface"]
    s.replace_document(ConfigName.ONBOARD_PLANS, source)
    views = PlanViews(s.world)
    result = views.plan(s.session, "plan_icl10", "transit")
    assert [v["stage_id"] for v in rows(result["stages"])] == ["ground_log"]
    assert result["plan_aboard"] is False
    cases: tuple[tuple[Callable[[], object], ErrorCode], ...] = (
        (lambda: views.condition_plan(s.session, "ICL10", "transit"), ErrorCode.CONTEXT_INAPPLICABLE),
        (lambda: views.plan(s.session, "plan_icl10", "unknown"), ErrorCode.INVALID_VALUE),
        (lambda: views.condition_plan(s.session, "ICL999", None), ErrorCode.NOT_FOUND),
    )
    for operation, code in cases:
        with pytest.raises(ProblemError) as caught:
            operation()
        assert caught.value.code is code
    for role in (Role.GROUND, Role.OPERATOR, Role.ADAPTER, Role.EDITOR):
        kind = cast(Literal["ground", "operator", "adapter", "editor"], role.value)
        caller = replace(s.session, kind=kind, roles=frozenset({role}))
        result = views.plan(caller, "plan_icl10", None)
        assert all(
            v["who_can"] == [] for stage in rows(result["stages"]) for v in rows(stage.get("steps", []))
        )
    with pytest.raises(ProblemError) as caught:
        views.plan(replace(s.session, actor=Actor("assistant", "assistant_test")), "plan_icl10", None)
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    s.mission.environment = None
    with pytest.raises(ProblemError) as caught:
        views.plan(s.session, "plan_icl10", None)
    assert caught.value.code is ErrorCode.CONTEXT_INAPPLICABLE


@pytest.mark.trace("T28-36", "AGENTS-1")
def test_configuration_edits_change_text_equipment_and_environment_without_code(tmp_path: Path) -> None:
    import json

    from crew_health.kernel.config import ConfigName

    from .plans_stack import obj

    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    for stage in rows(plan["stages"]):
        for step in rows(stage.get("steps", [])):
            if step["step_id"] == "icl10_note_episodes":
                step["text"] = "Changed configuration text"
            if step["step_id"] == "icl10_pulse_at_cameras":
                obj(step["needs"])["instruments"] = ["camera_station", "pulse_oximeter"]
    fourteen = next(p for p in rows(raw["plans"]) if p["plan_id"] == "plan_icl14")
    fourteen["environments"] = [e for e in cast(list[str], fourteen["environments"]) if e != "transit"]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    changed = next(
        v
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
        if v["step_id"] == "icl10_pulse_at_cameras"
    )
    assert any(
        v["text"] == "Changed configuration text"
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
        if v["step_id"] == "icl10_note_episodes"
    )
    assert changed["availability"] == "aboard"
    kit = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    kits = obj(kit["kits"])
    kits["lunar_surface"] = [i for i in cast(list[str], kits["lunar_surface"]) if i != "pulse_oximeter"]
    s.equipment.removed.add("pulse_oximeter")
    s.replace_document(ConfigName.KIT_MAP, kit)
    without = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    camera = next(
        v
        for stage in rows(without["stages"])
        for v in rows(stage.get("steps", []))
        if v["step_id"] == "icl10_pulse_at_cameras"
    )
    assert camera["availability"] == "not_aboard"
    assert obj(camera["missing"])["instruments"] == ["pulse_oximeter"]
    assert camera["substitute_step_id"] == "icl10_pulse_at_oximeter"
    assert (
        rows(PlanViews(s.world).plan(s.session, "plan_icl14", "transit")["stages"])[0]["stage_id"]
        == "ground_log"
    )


@pytest.mark.trace("T28-20", "EI3", "D22", "EI5")
def test_every_built_condition_in_all_four_environments_and_d22(tmp_path: Path) -> None:
    from crew_health.kernel.config import ConfigName

    from .plans_stack import ROOT, load_json
    from .view_harness import validate_view

    s = stack(tmp_path)
    kit = load_json(ROOT / "config/kit-map.json")
    s.replace_document(ConfigName.KIT_MAP, kit)
    s.replace_document(ConfigName.ONBOARD_PLANS, load_json(ROOT / "config/onboard-plans.json"))
    conditions = rows(kit["conditions"])
    assert len(conditions) == 119
    for environment in ("station_leo", "transit", "lunar_surface", "mars_surface"):
        s.mission.environment = environment
        for condition in conditions:
            result = PlanViews(s.world).condition_plan(s.session, str(condition["condition_id"]), environment)
            assert result["plan_aboard"] is True, (condition["condition_id"], environment)
            assert rows(result["stages"])[-1]["blocking"] is False
            if condition["condition_id"] == "ICL36A":
                validate_view(result)


@pytest.mark.trace("T28-26", "EI11", "T28-29")
def test_return_stage_trip_words_and_no_return_text_match_environment_profiles(tmp_path: Path) -> None:
    from .view_harness import validate_view

    s = stack(tmp_path)
    for environment, returns in (("lunar_surface", True), ("mars_surface", False), ("transit", False)):
        s.mission.environment = environment
        result = PlanViews(s.world).plan(s.session, "plan_eye_injury", environment)
        validate_view(result)
        stages = rows(result["stages"])
        assert any(v["stage_id"] == "return" for v in stages) is returns
        assert (result["no_return_text"] is None) is returns
        if returns:
            returning = next(v for v in stages if v["stage_id"] == "return")
            assert returning["trip_text"] == "A three-day lunar trip"
            assert returning["who_decides_text"]


@pytest.mark.trace("T28-29", "AGENTS-5")
def test_missing_ui_copy_is_typed_retryable_internal_error_and_missing_origin_is_unknown(
    tmp_path: Path,
) -> None:
    from crew_health.kernel.config import ConfigName
    from crew_health.kernel.errors import ErrorCode, ProblemError
    from crew_health.kernel.testing import MemoryRelease

    s = stack(tmp_path)
    s.mission.environment = None
    result = PlanViews(s.world).plan(s.session, "plan_icl10", "lunar_surface")
    assert all(
        v["availability"] == "not_aboard"
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
    )
    del s.docs[ConfigName.UI_COPY]
    s.release = MemoryRelease("release_no_copy", s.docs)
    with pytest.raises(ProblemError) as caught:
        PlanViews(s.world).plan(s.session, "plan_icl10", "lunar_surface")
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert caught.value.retryable


@pytest.mark.trace("T28-02", "T28-23", "EI7")
def test_missing_skill_holder_and_unknown_supply_cover_remain_explicit(tmp_path: Path) -> None:
    from dataclasses import replace

    s = stack(tmp_path)
    s.crew.skills.clear()
    result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    pulse = next(
        v
        for stage in rows(result["stages"])
        for v in rows(stage.get("steps", []))
        if v["step_id"] == "icl10_pulse_by_crewmate"
    )
    assert pulse["availability"] == "needs_skill_holder"
    assert pulse["availability_text"] == "Needs a crewmate with Pulse check practice"
    assert pulse["who_can"] == []
    s.world = replace(s.world, cover=None)
    eye = PlanViews(s.world).plan(s.session, "plan_eye_injury", None)
    covers = [
        c
        for stage in rows(eye["stages"])
        for v in rows(stage.get("steps", stage.get("prepare", [])))
        for c in rows(v["supply_cover_after"])
    ]
    assert covers
    assert all(c["uses_left"] is None for c in covers)


@pytest.mark.trace("T28-36", "R28-2", "AGENTS-1")
def test_configured_review_words_and_template_override_the_notice_interim(tmp_path: Path) -> None:
    import json

    from crew_health.kernel.config import ConfigName

    from .plans_stack import obj

    s = stack(tmp_path)
    copy = obj(json.loads(s.docs[ConfigName.UI_COPY].raw))
    copy["review_state_words"] = {"unreviewed": "Awaiting clinical review"}
    obj(copy["plan_lines"])["plan_tag"] = "Plan {plan_label}: {review_state}"
    s.replace_document(ConfigName.UI_COPY, copy)
    result = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    assert result["tag_text"] == "Plan ICL10: Awaiting clinical review"
