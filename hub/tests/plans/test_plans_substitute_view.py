"""Review item 8: guidance's canonical show_with_substitute decision shows the configured substitute, and the
principal whose equipment is missing still cannot be completed (C10 substitute rule, SYSTEM 8.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.guidance.application.ports import (
    ContentRef,
    GuidanceDecision,
    GuidanceEligibility,
    GuidanceRequest,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot
from crew_health.plans.application.views import PlanViews

from .plans_stack import PlansStack, obj, rows, stack

PRINCIPAL = "icl10_pulse_at_cameras"
SUBSTITUTE = "icl10_pulse_at_oximeter"


def without_lunar_camera(s: PlansStack) -> None:
    kit_map = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    kits = dict(obj(kit_map["kits"]))
    lunar = kits["lunar_surface"]
    assert isinstance(lunar, list)
    kits["lunar_surface"] = [i for i in lunar if i != "camera_station"]
    s.replace_document(ConfigName.KIT_MAP, {**kit_map, "kits": kits})


def plan_step(view: dict[str, object], step_id: str) -> dict[str, object]:
    return next(
        step for stage in rows(view["stages"]) for step in rows(stage["steps"]) if step["step_id"] == step_id
    )


@pytest.mark.trace("R28-25", "SYSTEM-8.2", "T28-05")
def test_canonical_substitute_decision_shows_the_configured_substitute(tmp_path: Path) -> None:
    s = stack(tmp_path)
    without_lunar_camera(s)
    copy = obj(json.loads(s.docs[ConfigName.UI_COPY].raw))
    words = str(obj(copy["plan_lines"])["substituted"])
    view = PlanViews(s.world).plan(s.session, "plan_icl10", "lunar_surface")
    principal = plan_step(view, PRINCIPAL)
    assert principal["availability"] == "substituted"
    sub_text = plan_step(view, SUBSTITUTE)["text"]
    assert principal["availability_text"] == words.replace("{step}", str(sub_text))


@pytest.mark.trace("R28-25", "SYSTEM-8.2", "T28-05")
def test_principal_with_missing_equipment_still_cannot_be_completed(tmp_path: Path) -> None:
    s = stack(tmp_path)
    without_lunar_camera(s)
    run = s.start()
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id=PRINCIPAL)
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE


@dataclass
class ForgedSubstitute:
    """Wraps the real facade and swaps the substitute reference, as a stale or mismatched E10 answer would."""

    inner: GuidanceEligibility
    forged: ContentRef

    def evaluate(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> GuidanceDecision:
        decision = self.inner.evaluate(snapshot, request)
        if decision.outcome != "show_with_substitute":
            return decision
        return replace(decision, substitute=self.forged)


@pytest.mark.trace("R28-25", "SYSTEM-8.2")
def test_substitute_not_matching_the_configured_reference_reads_unknown(tmp_path: Path) -> None:
    s = stack(tmp_path)
    without_lunar_camera(s)
    assert s.world.guidance is not None
    unknown = obj(obj(json.loads(s.docs[ConfigName.UI_COPY].raw))["designed_states"])["unknown"]
    base = PlanViews(s.world).plan(s.session, "plan_icl10", "lunar_surface")
    assert plan_step(base, PRINCIPAL)["availability"] == "substituted"
    for forged in (
        ContentRef("plan_step", "icl10_note_episodes", "0" * 64),
        ContentRef("plan_step", SUBSTITUTE, "0" * 64),
    ):
        s.rebind(guidance=ForgedSubstitute(s.world.guidance, forged))
        view = PlanViews(s.world).plan(s.session, "plan_icl10", "lunar_surface")
        principal = plan_step(view, PRINCIPAL)
        assert (principal["availability"], principal["availability_text"]) == ("not_aboard", unknown)
