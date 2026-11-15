"""Review items 3 and 9: return preparation runs only while the active environment's return option is
established (EI11, design 3.5), and an unestablished option reads as unknown, never as "no return"."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.plans.application.views import PlanViews

from .plans_stack import PlansStack, obj, rows, stack

PREPARE = "eye_return_prepare"


def doc(s: PlansStack, name: ConfigName) -> dict[str, object]:
    return obj(json.loads(s.docs[name].raw))


def without_return_option(s: PlansStack, environment_id: str) -> None:
    profiles = doc(s, ConfigName.ENVIRONMENT_PROFILES)
    for profile in rows(profiles["profiles"]):
        if profile["environment_id"] == environment_id:
            profile["return_option"] = None
    s.replace_document(ConfigName.ENVIRONMENT_PROFILES, profiles)


def eye_run(s: PlansStack) -> dict[str, object]:
    return s.start(plan_id="plan_eye_injury", episode_id=None, help_session_id="hlp_test")


@pytest.mark.trace("EI11", "T28-08", "T28-26")
def test_prepare_step_runs_while_return_is_established(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = eye_run(s)
    done = s.act(run, "step_done", step_id=PREPARE)
    assert any(step["step_id"] == PREPARE and step["state"] == "done" for step in rows(done["steps"]))


@pytest.mark.trace("EI11", "T28-08", "T28-26")
def test_prepare_step_refused_after_return_option_is_withdrawn(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = eye_run(s)
    without_return_option(s, "lunar_surface")
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id=PREPARE)
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    wire = s.runs.get(s.session, str(run["plan_run_id"]))
    assert all(step["step_id"] != PREPARE or step["state"] == "open" for step in rows(wire["steps"]))


@pytest.mark.trace("EI11", "T28-26", "V94")
def test_unestablished_return_option_reads_unknown_not_no_return(tmp_path: Path) -> None:
    s = stack(tmp_path)
    copy = doc(s, ConfigName.UI_COPY)
    unknown = obj(copy["designed_states"])["unknown"]
    no_return = obj(copy["plan_lines"])["no_return"]
    views = PlanViews(s.world)
    lunar = views.plan(s.session, "plan_eye_injury", "lunar_surface")
    assert lunar["no_return_text"] is None
    assert any(stage["stage_id"] == "return" for stage in rows(lunar["stages"]))
    for environment in ("mars_surface", "transit"):
        view = views.plan(s.session, "plan_eye_injury", environment)
        assert not any(stage["stage_id"] == "return" for stage in rows(view["stages"]))
        assert view["no_return_text"] == unknown != no_return
    without_return_option(s, "lunar_surface")
    withdrawn = PlanViews(s.world).plan(s.session, "plan_eye_injury", "lunar_surface")
    assert not any(stage["stage_id"] == "return" for stage in rows(withdrawn["stages"]))
    assert withdrawn["no_return_text"] == unknown
