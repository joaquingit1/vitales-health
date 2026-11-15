from dataclasses import dataclass, replace
from enum import StrEnum

from crew_health.assistant.domain.errors import AssistantRefused


class TurnState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMMITTING = "committing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    FAILED = "failed"
    STALE = "stale"


class TurnEvent(StrEnum):
    START = "start"
    VALIDATED = "validated"
    COMMIT = "commit"
    CANCEL = "cancel"
    PREEMPT = "preempt"
    SNAPSHOT_CHANGED = "snapshot_changed"
    DEADLINE = "deadline"
    REJECT = "reject"
    RUNTIME_LOST = "runtime_lost"
    EVICT = "evict"
    DEPENDENCY_FAILED = "dependency_failed"
    STORAGE_FAILED = "storage_failed"
    HUB_RESTART = "hub_restart"


@dataclass(frozen=True, slots=True)
class Turn:
    state: TurnState
    preemptions: int

    def __post_init__(self) -> None:
        if type(self.preemptions) is not int or self.preemptions < 0:
            raise AssistantRefused("invalid_turn_budget")


_TRANSITIONS = {
    (TurnState.QUEUED, TurnEvent.START): TurnState.RUNNING,
    (TurnState.QUEUED, TurnEvent.CANCEL): TurnState.CANCELLED,
    (TurnState.QUEUED, TurnEvent.EVICT): TurnState.FAILED,
    (TurnState.QUEUED, TurnEvent.RUNTIME_LOST): TurnState.FAILED,
    (TurnState.QUEUED, TurnEvent.HUB_RESTART): TurnState.FAILED,
    (TurnState.RUNNING, TurnEvent.VALIDATED): TurnState.COMMITTING,
    (TurnState.RUNNING, TurnEvent.SNAPSHOT_CHANGED): TurnState.STALE,
    (TurnState.RUNNING, TurnEvent.DEADLINE): TurnState.TIMED_OUT,
    (TurnState.RUNNING, TurnEvent.REJECT): TurnState.REJECTED,
    (TurnState.RUNNING, TurnEvent.CANCEL): TurnState.CANCELLED,
    (TurnState.RUNNING, TurnEvent.RUNTIME_LOST): TurnState.FAILED,
    (TurnState.RUNNING, TurnEvent.DEPENDENCY_FAILED): TurnState.FAILED,
    (TurnState.RUNNING, TurnEvent.HUB_RESTART): TurnState.FAILED,
    (TurnState.COMMITTING, TurnEvent.COMMIT): TurnState.COMPLETED,
    (TurnState.COMMITTING, TurnEvent.SNAPSHOT_CHANGED): TurnState.STALE,
    (TurnState.COMMITTING, TurnEvent.STORAGE_FAILED): TurnState.FAILED,
    (TurnState.COMMITTING, TurnEvent.HUB_RESTART): TurnState.FAILED,
}


def transition(turn: Turn, event: TurnEvent, *, max_preemptions: int) -> Turn:
    if type(max_preemptions) is not int or max_preemptions < 0:
        raise AssistantRefused("invalid_turn_budget")
    if (turn.state, event) == (TurnState.RUNNING, TurnEvent.PREEMPT):
        return (
            replace(turn, state=TurnState.QUEUED, preemptions=turn.preemptions + 1)
            if turn.preemptions < max_preemptions
            else replace(turn, state=TurnState.FAILED)
        )
    target = _TRANSITIONS.get((turn.state, event))
    if target is None:
        raise AssistantRefused("invalid_turn_transition")
    return replace(turn, state=target)
