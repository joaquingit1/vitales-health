"""Domain invariants the routine aggregates refuse on construction or transition (E25 domain edges)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import pytest

from crew_health.routine.domain.activation import CadenceActivation
from crew_health.routine.domain.bindings import ObservationInput, OverlayInput, match_bindings
from crew_health.routine.domain.errors import InvalidBody, InvalidTransition
from crew_health.routine.domain.hold import Interval, held_until, merged
from crew_health.routine.domain.instances import QuestionInstance
from crew_health.routine.domain.override import end, repoint
from crew_health.routine.domain.release import Binding, OverlayOn
from crew_health.routine.domain.vocabulary import EndReason

from .support import NOW, override, release


def activation(
    *,
    revision: int = 1,
    state: Literal["active", "ended"] = "active",
    ended_at: datetime | None = None,
    end_reason: EndReason | None = None,
) -> CadenceActivation:
    item = release().items[0]
    return CadenceActivation(
        "rca_test",
        revision,
        "binding_test",
        item.item_id,
        "p_test",
        item.default_cadence,
        NOW,
        "days",
        NOW + timedelta(days=5),
        state,
        ended_at,
        end_reason,
        "mission_event",
        "evt_1",
        NOW,
        "LIVE",
        "rel_test",
    )


def test_activation_refuses_a_zero_revision_and_an_ended_state_without_its_end() -> None:
    with pytest.raises(InvalidBody) as zero:
        activation(revision=0)
    assert (zero.value.pointer, zero.value.reason) == ("/activation", "invalid_revision_or_state")
    with pytest.raises(InvalidBody):
        activation(state="ended")
    with pytest.raises(InvalidBody):
        activation(ended_at=NOW, end_reason="days_elapsed")


def test_an_ended_activation_cannot_end_again() -> None:
    ended = activation().end(at=NOW, reason="days_elapsed")
    assert (ended.state, ended.revision, ended.end_reason) == ("ended", 2, "days_elapsed")
    with pytest.raises(InvalidTransition) as error:
        ended.end(at=NOW, reason="mission_event")
    assert error.value.current_revision == 2


def test_observation_input_refuses_a_value_that_is_not_finite() -> None:
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(InvalidBody) as error:
            ObservationInput(
                "obs_1", "p_test", "sleep_duration", Decimal(value), "h", NOW, "LIVE", "accepted"
            )
        assert (error.value.pointer, error.value.reason) == ("/value", "finite_required")


def test_an_ended_overlay_without_its_end_time_fires_no_ended_binding() -> None:
    walk_end = next(b for b in release().bindings if isinstance(b.on, OverlayOn) and b.on.edge == "ended")
    only: Binding = walk_end
    rel = replace(release(), bindings=(only,))
    environment = only.environment_id or "station_leo"
    ended = OverlayInput("ovl_1", 2, "p_test", "eva", "ended", None, NOW, NOW + timedelta(hours=1), None)
    assert len(match_bindings(rel, ended, environment)) == 1
    unknown_end = replace(ended, ended_at=None)
    assert match_bindings(rel, unknown_end, environment) == ()


def test_interval_refuses_an_end_before_its_start() -> None:
    with pytest.raises(InvalidBody) as error:
        Interval(NOW, NOW - timedelta(minutes=1), "sleep", "ovl_1")
    assert (error.value.pointer, error.value.reason) == ("/end", "before_start")


def test_an_open_ended_hold_absorbs_every_later_interval() -> None:
    open_sleep = Interval(NOW, None, "sleep", "ovl_open")
    later = Interval(NOW + timedelta(hours=2), NOW + timedelta(hours=3), "eva", "ovl_later")
    assert merged((later, open_sleep)) == ((NOW, None),)
    # An interval with no end holds the occurrence indefinitely: there is no deadline to compute.
    assert held_until(NOW - timedelta(hours=1), NOW + timedelta(hours=4), (later, open_sleep)) is None


def test_question_instance_refuses_a_zero_revision_or_an_empty_window() -> None:
    def instance(revision: int, until: timedelta) -> QuestionInstance:
        return QuestionInstance(
            "rq_1", "rov_1", "p_test", "set_1", "initial", ("q1",), NOW, NOW + until, {}, "open", revision
        )

    assert instance(1, timedelta(hours=1)).revision == 1
    for revision, until in ((0, timedelta(hours=1)), (1, timedelta(0)), (1, -timedelta(minutes=1))):
        with pytest.raises(InvalidBody) as error:
            instance(revision, until)
        assert (error.value.pointer, error.value.reason) == ("/instance", "invalid_window_or_revision")


def test_override_refuses_a_zero_revision_and_an_inconsistent_end_state() -> None:
    with pytest.raises(InvalidBody) as zero:
        replace(override(), revision=0)
    assert (zero.value.pointer, zero.value.reason) == ("/revision", "positive_revision_required")
    with pytest.raises(InvalidBody) as mismatch:
        replace(override(), state="ended")
    assert (mismatch.value.pointer, mismatch.value.reason) == ("/state", "end_state_mismatch")


def test_an_ended_override_cannot_be_repointed_to_a_new_warning() -> None:
    active = override()
    moved = repoint(active, "wrn_2")
    assert (moved.source_ref, moved.source_chain, moved.revision) == ("wrn_2", ("wrn_1", "wrn_2"), 2)
    ended = end(active, at=NOW, reason="warning_cleared")
    with pytest.raises(InvalidTransition) as error:
        repoint(ended, "wrn_2")
    assert error.value.current_revision == ended.revision
