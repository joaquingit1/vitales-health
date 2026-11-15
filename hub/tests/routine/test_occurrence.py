from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.routine.domain.errors import InvalidBody, InvalidTransition
from crew_health.routine.domain.occurrence import (
    Outcome,
    allowed_actions,
    complete,
    display_state,
    extend_for_hold,
    later,
    miss,
    skip,
)
from crew_health.routine.domain.vocabulary import FINAL_STATES, OccurrenceState

from .support import NOW, occurrence


@given(st.lists(st.sampled_from(("complete", "skip", "miss", "later")), min_size=1, max_size=30))
@pytest.mark.trace("T-E25-001", "INV-E25-01", "INV-E25-02")
def test_random_actions_never_leave_final_state_and_done_always_has_actor(actions: list[str]) -> None:
    occ = occurrence()
    for action in actions:
        previous = occ
        try:
            if action == "complete":
                occ, transition = complete(
                    occ,
                    now=NOW,
                    completed_at=None,
                    outcome=Outcome((), (), None),
                    actor_kind="person",
                    actor_id=occ.person_id,
                    day_start=NOW,
                    guidance_release_id=occ.config_release_id,
                )
            elif action == "skip":
                occ, transition = skip(
                    occ,
                    now=NOW,
                    reason="declined",
                    note=None,
                    linked=False,
                    actor_kind="person",
                    actor_id=occ.person_id,
                )
            elif action == "miss":
                occ, transition = miss(occ, now=NOW, counted=True, reason="deadline", actor_id="engine")
            else:
                occ, transition = later(
                    occ,
                    now=NOW,
                    later_until=NOW + timedelta(hours=1),
                    held=False,
                    actor_kind="person",
                    actor_id=occ.person_id,
                )
        except InvalidTransition as error:
            assert previous.state in FINAL_STATES
            assert error.current_revision == previous.revision
            assert occ == previous
        else:
            assert previous.state not in FINAL_STATES
            assert occ.revision == previous.revision + 1
            assert transition.actor_id and transition.to_state == occ.state
            if occ.state == "done":
                assert occ.completed_at is not None and occ.outcome is not None
            if occ.state in ("skipped", "missed"):
                assert occ.outcome is None


@pytest.mark.trace("T-E25-004")
def test_skip_rejects_linked_records() -> None:
    with pytest.raises(InvalidBody) as error:
        skip(
            occurrence(),
            now=NOW,
            reason="declined",
            note=None,
            linked=True,
            actor_kind="person",
            actor_id="p_test",
        )
    assert (error.value.pointer, error.value.reason) == ("/observation_ids", "skip_with_links")


@pytest.mark.parametrize("offset", [-1, 0, 1441])
@pytest.mark.trace("T-E25-001")
def test_later_rejects_times_outside_window(offset: int) -> None:
    with pytest.raises(InvalidBody):
        later(
            occurrence(),
            now=NOW,
            later_until=NOW + timedelta(minutes=offset),
            held=False,
            actor_kind="person",
            actor_id="p_test",
        )


@pytest.mark.trace("T-E25-001")
def test_later_allows_deadline_and_rejects_hold() -> None:
    occ = occurrence()
    moved, _ = later(
        occ,
        now=NOW,
        later_until=NOW + timedelta(days=1),
        held=False,
        actor_kind="person",
        actor_id=occ.person_id,
    )
    assert moved.later_until == occ.due_until
    with pytest.raises(InvalidTransition):
        later(
            occ,
            now=NOW,
            later_until=NOW + timedelta(hours=1),
            held=True,
            actor_kind="person",
            actor_id=occ.person_id,
        )


@pytest.mark.parametrize("completed,now", [(-1, 10), (11, 10), (1441, 1442)])
@pytest.mark.trace("T-E25-001")
def test_completion_rejects_future_before_day_and_after_deadline(completed: int, now: int) -> None:
    with pytest.raises(InvalidBody) as error:
        complete(
            occurrence(),
            now=NOW + timedelta(minutes=now),
            completed_at=NOW + timedelta(minutes=completed),
            outcome=Outcome((), (), None),
            actor_kind="person",
            actor_id="p_test",
            day_start=NOW,
            guidance_release_id="rel_test",
        )
    assert error.value.pointer == "/completed_at"


@pytest.mark.trace("T-E25-001")
def test_completion_at_deadline_is_valid_and_records_guidance() -> None:
    occ = occurrence()
    end = NOW + timedelta(days=1)
    done, record = complete(
        occ,
        now=end,
        completed_at=end,
        outcome=Outcome(("obs_1",), ("ans_1",), None),
        actor_kind="person",
        actor_id=occ.person_id,
        day_start=NOW,
        guidance_release_id="rel_guidance",
    )
    assert done.completed_at == end and done.guidance_release_id == "rel_guidance"
    assert record.actor_id == occ.person_id and record.revision == 2


@pytest.mark.trace("T-E25-001", "T-E25-005")
def test_actions_and_display_follow_session_hold_and_time() -> None:
    occ = occurrence()
    assert allowed_actions(occ, session_person_id="other", held=False) == ()
    assert allowed_actions(occ, session_person_id=None, held=False) == ()
    assert allowed_actions(occ, session_person_id=occ.person_id, held=True) == ("complete", "skip")
    assert allowed_actions(occ, session_person_id=occ.person_id, held=False) == ("complete", "skip", "later")
    assert display_state(occ, now=NOW, today=1, held_kind="sleep") == ("later", "sleep")
    assert display_state(replace(occ, mission_day=2), now=NOW, today=1, held_kind="sleep") == (
        "not_due",
        None,
    )
    assert display_state(
        replace(occ, due_from=NOW + timedelta(hours=1)), now=NOW, today=1, held_kind=None
    ) == ("later", None)
    assert display_state(
        replace(occ, later_until=NOW + timedelta(hours=1)), now=NOW, today=1, held_kind=None
    ) == ("later", None)
    assert display_state(occ, now=NOW, today=1, held_kind=None) == ("due", None)
    final, _ = miss(occ, now=NOW, counted=False, reason="outage", actor_id="engine")
    assert allowed_actions(final, session_person_id=occ.person_id, held=False) == ()
    assert display_state(final, now=NOW, today=1, held_kind="sleep") == ("missed", None)


@pytest.mark.parametrize("state", ["done", "skipped", "missed"])
@pytest.mark.trace("INV-E25-01", "INV-E25-02")
def test_storage_rejects_final_states_without_required_fields(state: OccurrenceState) -> None:
    with pytest.raises(InvalidBody):
        replace(occurrence(), state=state)


@pytest.mark.trace("INV-E25-03")
def test_hold_extension_never_precedes_base_deadline() -> None:
    occ = occurrence()
    with pytest.raises(InvalidBody):
        extend_for_hold(occ, NOW)
    assert extend_for_hold(occ, None).due_until is None
    assert extend_for_hold(occ, NOW + timedelta(days=2)).revision == 2


@pytest.mark.trace("T-E25-001")
def test_later_requires_a_finite_due_deadline() -> None:
    with pytest.raises(InvalidBody):
        later(
            replace(occurrence(), due_until=None),
            now=NOW,
            later_until=NOW + timedelta(hours=1),
            held=False,
            actor_kind="person",
            actor_id="p_test",
        )


@pytest.mark.trace("INV-E25-01", "INV-E25-02", "INV-E25-05")
def test_storage_rejects_invalid_revision_outcome_and_assistant_provenance() -> None:
    occ = occurrence()
    with pytest.raises(InvalidBody):
        replace(occ, revision=0)
    with pytest.raises(InvalidBody):
        replace(occ, outcome=Outcome((), (), None))
    with pytest.raises(InvalidBody):
        replace(occ, cause_kind="person")
    with pytest.raises(InvalidBody):
        replace(occ, cause_kind="assistant", periodic=False)
    with pytest.raises(InvalidBody):
        complete(
            occ,
            now=NOW,
            completed_at=None,
            outcome=Outcome((), (), None),
            actor_kind="person",
            actor_id="",
            day_start=NOW,
            guidance_release_id=occ.config_release_id,
        )
