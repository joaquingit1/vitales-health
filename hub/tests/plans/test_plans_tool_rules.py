"""Assistant tools reject in the contract's declared reason order."""

import pytest

from crew_health.plans.domain.tool_rules import choose_plan_step_reason, explain_step_reason

from .harness import plan


@pytest.mark.trace("T28-19", "EI6")
def test_choose_reason_order_and_explain_membership() -> None:
    p = plan()

    def reason(
        active: bool, sid: str, actionable: bool, runnable: bool, scope: bool, text: str
    ) -> str | None:
        return choose_plan_step_reason(
            plan_active=active,
            plan=p,
            step_id=sid,
            actionable=actionable,
            runnable=runnable,
            answer_in_scope=scope,
            string_arguments=(text,),
        )

    assert reason(False, "missing", False, False, False, "2 mg") == "plan_not_active"
    assert reason(True, "missing", False, False, False, "2 mg") == "step_not_in_plan"
    assert reason(True, p.steps[5].step_id, False, False, False, "2 mg") == "step_substitute_only"
    assert reason(True, p.steps[1].step_id, True, False, False, "2 mg") == "step_not_runnable"
    assert reason(True, p.steps[1].step_id, True, True, False, "2 mg") == "answer_out_of_scope"
    assert reason(True, p.steps[1].step_id, True, True, True, "2 mg") == "dose_in_text"
    assert reason(True, p.steps[1].step_id, True, True, True, "10 seconds") is None
    assert reason(True, p.steps[5].step_id, True, True, True, "") is None
    assert explain_step_reason(plan_active=False, plan=p, step_id="missing") == "plan_not_active"
    assert explain_step_reason(plan_active=True, plan=None, step_id="missing") == "plan_not_active"
    assert explain_step_reason(plan_active=True, plan=p, step_id="missing") == "step_not_in_plan"
    assert explain_step_reason(plan_active=True, plan=p, step_id=p.steps[0].step_id) is None
