"""V84 handler half in the E05 fallback, registered inside the composed plans stack.

The real E12 gateway and output policy are not on main. This verifies the plans
handler half of V84 only, with an explicit SIM ToolRegistry and controller scope.
"""

import logging
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import pytest
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.config import ConfigName
from crew_health.plans.application.assistant_copies import (
    Deadline,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    ToolScope,
)
from crew_health.plans.domain.text_check import find_amounts
from pydantic import JsonValue
from stack import NOW, Station, steps

pytestmark = pytest.mark.trace("V84", "T28-19", "T28-43", "EI6")


def invoke(
    station: Station, name: str, scope: ToolScope, arguments: Mapping[str, JsonValue]
) -> HandlerOutcome:
    handler = station.tools.handler(name)
    assert handler is not None
    with station.db.begin(actor=scope.session.actor, correlation_id="v84_handler") as uow:
        return handler.handle(
            uow,
            scope,
            arguments,
            Deadline(TimeInput(NOW, "hub", "segment", 0, "hub_corrected", "SIM"), timedelta(seconds=1)),
        )


def test_registered_handler_rejections_and_valid_proposal_never_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    station = Station(tmp_path)
    run = station.start()
    scope = ToolScope(
        station.base.session,
        "mara",
        "ep_test",
        None,
        str(run["plan_run_id"]),
        "release_e28_shipped",
        "turn_v84",
    )
    assert set(station.tools.handlers) == {"get_plan", "choose_plan_step", "explain_step"}
    assert {name: handler.effect for name, handler in station.tools.handlers.items()} == {
        "get_plan": "read",
        "choose_plan_step": "proposal",
        "explain_step": "read",
    }
    station.equipment.removed.add("camera_station")
    response = station.client.get("/v1/conditions/ICL10/plan")
    assert response.status_code == 200, response.text
    view_before = response.content
    before = station.all_rows()
    base: dict[str, JsonValue] = {
        "plan_id": "plan_icl10",
        "step_id": "icl10_pulse_by_crewmate",
        "because_answer_id": "answer_here",
    }
    cases: tuple[tuple[dict[str, JsonValue], str], ...] = (
        ({**base, "plan_id": "plan_other", "step_id": "missing", "note": "give 5 mg"}, "plan_not_active"),
        ({**base, "step_id": "missing", "note": "give 5 mg"}, "step_not_in_plan"),
        ({**base, "step_id": "icl10_pulse_at_oximeter", "note": "give 5 mg"}, "step_substitute_only"),
        ({**base, "step_id": "icl10_pulse_at_cameras", "note": "give 5 mg"}, "step_not_runnable"),
        ({**base, "because_answer_id": "answer_elsewhere", "note": "give 5 mg"}, "answer_out_of_scope"),
        ({**base, "note": "give 5 mg"}, "dose_in_text"),
        ({**base, "subject_person_id": "tomas"}, "answer_out_of_scope"),
    )
    with caplog.at_level(logging.WARNING, logger="crew_health.plans"):
        for arguments, reason in cases:
            assert invoke(station, "choose_plan_step", scope, arguments) == HandlerRejected(reason)
            assert station.all_rows() == before
    messages = [record.getMessage() for record in caplog.records if record.name == "crew_health.plans"]
    assert messages == ["choose_plan_step turn_v84 " + reason for _, reason in cases]
    assert "give 5 mg" not in caplog.text
    assert "answer_elsewhere" not in caplog.text
    assert invoke(station, "choose_plan_step", scope, base) == HandlerOk(
        {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate", "proposed": True}
    )
    assert station.client.get("/v1/conditions/ICL10/plan").content == view_before
    assert station.all_rows() == before


def test_explain_step_returns_release_text_and_get_plan_is_read_only(tmp_path: Path) -> None:
    station = Station(tmp_path)
    run = station.start()
    scope = ToolScope(
        station.base.session,
        "mara",
        "ep_test",
        None,
        str(run["plan_run_id"]),
        "release_e28_shipped",
        "turn_v84_explain",
    )
    before = station.all_rows()
    view = station.client.get("/v1/plans/plan_icl10")
    assert view.status_code == 200, view.text
    result = invoke(station, "get_plan", scope, {})
    assert isinstance(result, HandlerOk)
    assert result.result == view.json()
    expected = next(
        s for s in steps(station.raw(ConfigName.ONBOARD_PLANS)) if s["step_id"] == "icl10_pulse_by_crewmate"
    )
    explanation = invoke(
        station, "explain_step", scope, {"plan_id": "plan_icl10", "step_id": str(expected["step_id"])}
    )
    assert isinstance(explanation, HandlerOk)
    assert explanation.result == {
        "text": expected["text"],
        "text_first_time": expected["text_first_time"],
        "source_ids": expected["source_ids"],
        "passages": [],
    }
    assert find_amounts("take 2 tablets of 500 mg")
    assert station.all_rows() == before
