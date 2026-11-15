"""Review item 4: an open run keeps the guidance, kit and catalog semantics of the release it pinned, while
stock, people, location and return withdrawal stay live. The content provider in plans_stack validates the
release and step hash it is asked about, as E10 will."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.errors import ProblemError

from .plans_stack import PlansStack, obj, rows, stack

NOTE = "icl10_note_episodes"
CAMERAS = "icl10_pulse_at_cameras"


def doc(s: PlansStack, name: ConfigName) -> dict[str, object]:
    return obj(json.loads(s.docs[name].raw))


def edit_step_text(s: PlansStack, step_id: str, text: str) -> None:
    plans = doc(s, ConfigName.ONBOARD_PLANS)
    for plan in rows(plans["plans"]):
        for stage in rows(plan["stages"]):
            for step in rows(stage.get("steps", stage.get("prepare", []))):
                if step["step_id"] == step_id:
                    step["text"] = text
    s.replace_document(ConfigName.ONBOARD_PLANS, plans)


def step_state(wire: dict[str, object], step_id: str) -> object:
    return next(step["state"] for step in rows(wire["steps"]) if step["step_id"] == step_id)


@pytest.mark.trace("T28-17", "R28-18", "SYSTEM-8.2")
def test_pinned_step_stays_executable_after_a_release_changes_its_text(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    edit_step_text(s, NOTE, "A changed note text in a later release.")
    done = s.act(run, "step_done", step_id=NOTE)
    assert step_state(done, NOTE) == "done"


@pytest.mark.trace("T28-17", "SYSTEM-8.2")
def test_a_new_run_uses_the_new_release_and_an_unknown_release_is_refused(tmp_path: Path) -> None:
    s = stack(tmp_path)
    edit_step_text(s, NOTE, "A changed note text in a later release.")
    run = s.start(key="new_run")
    done = s.act(run, "step_done", step_id=NOTE)
    assert step_state(done, NOTE) == "done"
    s.act(done, "close", disposition="other")
    s.content.releases.clear()
    other = s.start(key="unknown_release")
    with pytest.raises(ProblemError) as error:
        s.act(other, "step_done", step_id=NOTE, key="note_unknown_release")
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE


def set_lunar_kit(s: PlansStack, *, camera: bool) -> None:
    kit_map = doc(s, ConfigName.KIT_MAP)
    kits = dict(obj(kit_map["kits"]))
    others = [i for i in rows_of(kits["lunar_surface"]) if i != "camera_station"]
    kits["lunar_surface"] = others + (["camera_station"] if camera else [])
    s.replace_document(ConfigName.KIT_MAP, {**kit_map, "kits": kits})


@pytest.mark.trace("T28-17", "R28-18")
def test_open_run_keeps_its_pinned_kit_when_a_release_changes_it(tmp_path: Path) -> None:
    s = stack(tmp_path)
    set_lunar_kit(s, camera=False)
    run = s.start()
    set_lunar_kit(s, camera=True)
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id=CAMERAS)
    assert error.value.code is ErrorCode.STEP_NOT_AVAILABLE
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    s.act(current, "close", disposition="other")
    fresh = s.start(key="after_kit_change")
    assert step_state(s.act(fresh, "step_done", step_id=CAMERAS, key="cameras_fresh"), CAMERAS) == "done"


def rows_of(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(v) for v in value]
