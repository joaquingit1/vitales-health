"""A configuration edit changes a threshold, a label and a step with no code change (AGENTS 1 rule 1, R43, V75).

Each test edits a scratch copy of config/warning-rules.json (never the shipped file) and runs the same engine: the
answer window of `dizzy_after_landing` (a threshold E24 itself evaluates, `within_days_after_event.days`), the
storm title (a label) and a storm step. None of them touches a safety mapping (D15: which level a rule raises and
who may raise or lower it); `test_the_edits_touch_no_safety_mapping` checks exactly that for every edit used here.
The sensor-limit threshold edit is T-E24-090 in `test_config_document.py` (fingerprint) and the pinned-threshold
sequence in `test_release_changes.py`; E23 evaluates sensor thresholds, E24 only pins them.

T-E24-090 to 093; R43, V75; D15.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from crew_health.rules.domain.definitions import RulesDocument, parse_rules_document
from crew_health.rules.domain.inputs import AnswerInput, MissionEventInput
from crew_health.rules.domain.vocabulary import EnvironmentId, OriginMode

from . import rules_harness as h

pytestmark = [pytest.mark.E24, pytest.mark.trace("R43", "V75", "D15")]

Edit = Callable[[dict[str, Any]], None]


def _definition(raw: dict[str, Any], definition_id: str) -> dict[str, Any]:
    (found,) = [d for d in raw["definitions"] if d["id"] == definition_id]
    assert isinstance(found, dict)
    return found


def window_days(days: int) -> Edit:
    def edit(raw: dict[str, Any]) -> None:
        _definition(raw, "dizzy_after_landing")["condition"]["within_days_after_event"]["days"] = days

    return edit


def storm_title(raw: dict[str, Any]) -> None:
    _definition(raw, "radiation_storm_shelter")["content"]["title"] = "Storm shelter now (scratch edit)"


def storm_step(raw: dict[str, Any]) -> None:
    _definition(raw, "radiation_storm_shelter")["content"]["steps"]["items"][0] = "Scratch edited first step."


def edited(edit: Edit) -> RulesDocument:
    raw = copy.deepcopy(h.deployment_rules())
    edit(raw)
    return parse_rules_document(raw)


def _answer_days_after_landing(doc: RulesDocument, days_after: int) -> bool:
    world = h.World(doc, h.release_documents(), environment=EnvironmentId.MARS_SURFACE)
    world.answer_events = (world.event("evt_land", "landing", h.T0 - timedelta(days=days_after)),)
    answer = AnswerInput(
        "ans_1", "p1", "dizzy_standing", "present", h.T0, h.T0, OriginMode.LIVE, "day", "09:10"
    )
    world.feed(answer)
    return bool(world.by_definition("dizzy_after_landing"))


def _storm(doc: RulesDocument) -> Any:
    world = h.World(doc, h.release_documents())
    world.feed(MissionEventInput("evt_1", "space_weather_alert", "Space weather alert", h.T0, h.T0, h.T0,
                                 OriginMode.LIVE, "station_leo", (), None))  # fmt: skip
    (w,) = world.by_definition("radiation_storm_shelter")
    return w


@pytest.mark.trace("T-E24-090", "T-E24-093")
def test_a_threshold_edit_moves_the_answer_window_with_no_code_change(deployment_doc: RulesDocument) -> None:
    shipped = _definition(h.deployment_rules(), "dizzy_after_landing")["condition"]["within_days_after_event"]
    days = int(shipped["days"])
    assert _answer_days_after_landing(deployment_doc, days - 1)
    assert not _answer_days_after_landing(edited(window_days(days - 2)), days - 1)  # narrower: now outside
    assert _answer_days_after_landing(edited(window_days(days + 2)), days + 1)  # wider: now inside
    assert not _answer_days_after_landing(deployment_doc, days + 1)


@pytest.mark.trace("T-E24-091", "T-E24-093")
def test_a_label_and_a_step_edit_reach_the_next_warning(deployment_doc: RulesDocument) -> None:
    shipped = _storm(deployment_doc)
    titled = _storm(edited(storm_title))
    stepped = _storm(edited(storm_step))
    assert titled.title == "Storm shelter now (scratch edit)" != shipped.title
    assert stepped.steps[0] == "Scratch edited first step." != shipped.steps[0]
    assert (titled.level, stepped.level) == (shipped.level, shipped.level)


def test_the_edits_touch_no_safety_mapping(deployment_doc: RulesDocument) -> None:
    for edit in (window_days(2), window_days(9), storm_title, storm_step):
        doc = edited(edit)
        assert [(d.id, d.level, d.raised_by, d.condition.kind, d.scope) for d in doc.definitions] == [
            (d.id, d.level, d.raised_by, d.condition.kind, d.scope) for d in deployment_doc.definitions
        ]
