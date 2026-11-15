from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.turn import Turn, TurnEvent, TurnState, transition


@pytest.mark.trace("T-E13-001", "T-E13-013", "R28")
def test_only_committed_turn_can_complete_and_terminal_states_are_closed() -> None:
    turn = Turn(TurnState.QUEUED, 0)
    turn = transition(turn, TurnEvent.START, max_preemptions=2)
    with pytest.raises(AssistantRefused):
        transition(turn, TurnEvent.COMMIT, max_preemptions=2)
    turn = transition(turn, TurnEvent.VALIDATED, max_preemptions=2)
    assert turn.state == TurnState.COMMITTING
    turn = transition(turn, TurnEvent.COMMIT, max_preemptions=2)
    assert turn.state == TurnState.COMPLETED
    for event in TurnEvent:
        with pytest.raises(AssistantRefused):
            transition(turn, event, max_preemptions=2)


@pytest.mark.trace("T-E13-001", "T-E13-024", "V27")
@pytest.mark.parametrize("state", [TurnState.QUEUED, TurnState.RUNNING])
def test_cancel_is_terminal_before_commit(state: TurnState) -> None:
    cancelled = transition(Turn(state, 0), TurnEvent.CANCEL, max_preemptions=2)
    assert cancelled.state == TurnState.CANCELLED
    with pytest.raises(AssistantRefused):
        transition(cancelled, TurnEvent.VALIDATED, max_preemptions=2)


@pytest.mark.trace("T-E13-002", "T-E13-021", "T-E13-025", "R15")
@given(limit=st.integers(min_value=0, max_value=16))
def test_preemption_budget_is_finite_and_the_next_preemption_fails(limit: int) -> None:
    turn = Turn(TurnState.RUNNING, 0)
    for expected in range(1, limit + 1):
        turn = transition(turn, TurnEvent.PREEMPT, max_preemptions=limit)
        assert turn.state == TurnState.QUEUED and turn.preemptions == expected
        turn = transition(turn, TurnEvent.START, max_preemptions=limit)
    failed = transition(turn, TurnEvent.PREEMPT, max_preemptions=limit)
    assert failed.state == TurnState.FAILED


@pytest.mark.trace("T-E13-001", "T-E13-030", "T-E13-031")
@pytest.mark.parametrize("state", [TurnState.RUNNING, TurnState.COMMITTING])
def test_changed_snapshot_never_completes(state: TurnState) -> None:
    stale = transition(Turn(state, 0), TurnEvent.SNAPSHOT_CHANGED, max_preemptions=2)
    assert stale.state == TurnState.STALE
    with pytest.raises(AssistantRefused):
        transition(stale, TurnEvent.COMMIT, max_preemptions=2)


@pytest.mark.trace("T-E13-001", "T-E13-060")
@pytest.mark.parametrize("state", [TurnState.QUEUED, TurnState.RUNNING, TurnState.COMMITTING])
def test_restart_abandons_each_speculative_turn_state(state: TurnState) -> None:
    failed = transition(Turn(state, 0), TurnEvent.HUB_RESTART, max_preemptions=2)
    assert failed.state == TurnState.FAILED
