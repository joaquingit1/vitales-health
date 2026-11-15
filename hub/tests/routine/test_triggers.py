from dataclasses import replace
from datetime import datetime, timedelta
from typing import TypedDict

import pytest

from crew_health.routine.domain.admission import admissible
from crew_health.routine.domain.audience import members
from crew_health.routine.domain.bindings import (
    AnswerInput,
    EnvironmentInput,
    MissionEventInput,
    OverlayInput,
    WarningRaisedInput,
    match_bindings,
)
from crew_health.routine.domain.coalesce import absorbing
from crew_health.routine.domain.errors import InvalidTransition
from crew_health.routine.domain.override import (
    applies_to,
    clear_decision,
    days_end_at,
    end,
    reason_text,
    repoint,
    started_by_for,
)
from crew_health.routine.domain.release import AnswerOn, EnvironmentOn, OverlayOn
from crew_health.routine.domain.vocabulary import OriginMode

from .support import NOW, occurrence, override, release


class CoveringArgs(TypedDict):
    item_id: str
    person_id: str
    due_from: datetime
    due_until: datetime


def warning() -> WarningRaisedInput:
    return WarningRaisedInput(
        "wrn_1",
        "co2_above_limit",
        "sensor_limit",
        "location",
        None,
        "cabin",
        "LIVE",
        NOW,
        None,
        None,
        "station_leo",
    )


@pytest.mark.trace("T-E25-092")
def test_real_co2_warning_fires_both_bindings_but_assistant_sources_never_match() -> None:
    r, inp = release(), warning()
    assert tuple(f.binding.binding_id for f in match_bindings(r, inp, "station_leo")) == (
        "daily_check.co2_above_limit",
        "vision_self_check.co2_above_limit",
    )
    assert match_bindings(r, replace(inp, raised_by="assistant"), "station_leo") == ()
    assert match_bindings(r, replace(inp, insight_id="insight_1"), "station_leo") == ()
    assert all(
        f.source_at == NOW and f.source_kind == "warning" for f in match_bindings(r, inp, "station_leo")
    )


@pytest.mark.trace("T-E25-021")
def test_eva_end_and_landing_match_real_bindings_in_release_order() -> None:
    r = release()
    walk = OverlayInput("ov_1", 1, "p_test", "eva", "ended", NOW, NOW, NOW, "cabin")
    assert tuple(f.binding.binding_id for f in match_bindings(r, walk, "lunar_surface")) == (
        "daily_check.after_walk",
        "dust_exposure_check.after_surface_walk",
    )
    assert len(match_bindings(r, walk, "station_leo")) == 1
    event = MissionEventInput("evt_1", "landing", NOW, "LIVE", "lunar_surface", (), None)
    assert tuple(f.binding.binding_id for f in match_bindings(r, event, "lunar_surface")) == (
        "blood_pressure.return_to_gravity",
        "gravity_arrival_check.landing",
    )
    assert match_bindings(r, replace(walk, state="active"), "lunar_surface") == ()


@pytest.mark.trace("T-E25-021")
def test_planned_overlay_answer_and_environment_match_only_their_conditions() -> None:
    r = release()
    binding = r.bindings[2]
    planned = (
        replace(binding, on=replace(binding.on, edge="planned"))
        if isinstance(binding.on, OverlayOn)
        else binding
    )
    walk = OverlayInput("ov_1", 1, "p_test", "eva", "planned", NOW, None, None, None)
    assert len(match_bindings(replace(r, bindings=(planned,)), walk, "station_leo")) == 1
    answer = replace(binding, on=AnswerOn(r.misses.question_id, ("present",)))
    inp = AnswerInput("ans_1", "p_test", r.misses.question_id, "present", NOW, "LIVE")
    assert len(match_bindings(replace(r, bindings=(answer,)), inp, "station_leo")) == 1
    assert (
        match_bindings(replace(r, bindings=(answer,)), replace(inp, presence="absent"), "station_leo") == ()
    )
    environment = replace(binding, kind="cadence_event", on=EnvironmentOn(("station_leo",)))
    assert (
        len(
            match_bindings(
                replace(r, bindings=(environment,)), EnvironmentInput("station_leo", NOW), "station_leo"
            )
        )
        == 1
    )
    assert (
        match_bindings(
            replace(r, bindings=(replace(environment, kind="trigger"),)),
            EnvironmentInput("station_leo", NOW),
            "station_leo",
        )
        == ()
    )


@pytest.mark.parametrize("mission", ["LIVE", "SIM", "REPLAY"])
@pytest.mark.parametrize("origin", ["LIVE", "SIM", "REPLAY"])
@pytest.mark.parametrize("group", [True, False])
@pytest.mark.trace("T-E25-029", "D16", "INV-E25-15")
def test_admission_matrix(mission: OriginMode, origin: OriginMode, group: bool) -> None:
    # E25 3.8.1: LIVE only LIVE; SIM accepts REPLAY and SIM, LIVE only for a person; REPLAY only REPLAY.
    expected = mission == origin or (
        mission == "SIM" and (origin == "REPLAY" or (origin == "LIVE" and not group))
    )
    result = admissible(mission_mode=mission, input_origin=origin, group_audience=group, on_roster=True)
    assert result.ok == expected
    assert result.code == (None if expected else "routine.origin_rejected")
    assert (
        admissible(mission_mode=mission, input_origin=origin, group_audience=group, on_roster=False).code
        == "routine.not_on_roster"
    )


@pytest.mark.trace("T-E25-104")
def test_audience_includes_unknown_locations_and_interim_excludes_walkers() -> None:
    roster = ("c", "b", "a")
    locations = {"a": "cabin", "b": None, "c": "other"}
    assert members(
        "everyone_in_location",
        subject_ids=(),
        roster=roster,
        location_of=locations.get,
        source_location="cabin",
        walking=frozenset(),
    ) == (("a", "location"), ("b", "location_unknown"))
    assert members(
        "everyone_in_location",
        subject_ids=(),
        roster=roster,
        location_of=None,
        source_location=None,
        walking=frozenset(("c",)),
    ) == (("a", "site"), ("b", "site"))
    assert (
        members(
            "all_crew",
            subject_ids=(),
            roster=None,
            location_of=None,
            source_location=None,
            walking=frozenset(),
        )
        is None
    )
    assert members(
        "person",
        subject_ids=("b", "a"),
        roster=None,
        location_of=None,
        source_location=None,
        walking=frozenset(),
    ) == (("a", "subject"), ("b", "subject"))
    assert members(
        "all_crew",
        subject_ids=(),
        roster=roster,
        location_of=None,
        source_location=None,
        walking=frozenset(("c",)),
    ) == (("a", "site"), ("b", "site"), ("c", "site"))


@pytest.mark.trace("T-E25-043")
def test_covering_window_absorbs_added_occurrence_but_all_day_never_absorbs_repeat() -> None:
    occ = occurrence()
    args: CoveringArgs = dict(
        item_id=occ.item_id, person_id=occ.person_id, due_from=NOW, due_until=NOW + timedelta(hours=1)
    )
    assert absorbing((occ,), **args, repeat=False) == occ.occurrence_id
    assert absorbing((occ,), **args, repeat=True) is None
    assert absorbing((replace(occ, due_kind="specific"),), **args, repeat=True) == occ.occurrence_id
    assert absorbing((replace(occ, due_until=None),), **args, repeat=False) == occ.occurrence_id
    assert absorbing((replace(occ, person_id="other"),), **args, repeat=False) is None
    assert absorbing((replace(occ, due_from=NOW + timedelta(minutes=1)),), **args, repeat=False) is None


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("superseded", "repoint"),
        ("config_changed", "await"),
        ("environment_changed", "environment_changed"),
        ("resolved", "end"),
    ],
)
@pytest.mark.trace("T-E25-008", "T-E25-100")
def test_warning_clear_decisions_preserve_tightening_until_disposition(reason: str, expected: str) -> None:
    ovr = override()
    assert clear_decision(ovr, warning_id="wrn_1", clear_reason=reason) == expected
    assert clear_decision(ovr, warning_id="older", clear_reason=reason) == "ignore"
    assert ovr.state == "active"
    pointed = repoint(ovr, "wrn_2")
    assert pointed.source_chain == ("wrn_1", "wrn_2") and pointed.revision == 2
    assert clear_decision(pointed, warning_id="wrn_1", clear_reason=reason) == "ignore"


@pytest.mark.trace("T-E25-008", "T-E25-023")
def test_override_end_reason_text_and_membership() -> None:
    ovr = override()
    assert reason_text(ovr.trigger_text) == ovr.trigger_text.removesuffix(" (sensor_limit)").strip()
    assert reason_text("  Words (rule)  ") == "Words"
    assert started_by_for("sensor_limit") == "sensor_limit"
    assert started_by_for("person") == "rule"
    assert applies_to(ovr, "p_test") and not applies_to(ovr, "other")
    assert applies_to(replace(ovr, audience="person", person_id="other"), "other")
    ended = end(ovr, at=NOW, reason="warning_cleared")
    assert ended.revision == 2 and ended.ended_at == NOW
    with pytest.raises(InvalidTransition):
        end(ended, at=NOW, reason="warning_cleared")
    assert days_end_at(NOW, 3, timedelta(hours=24)) == NOW + timedelta(days=3)
