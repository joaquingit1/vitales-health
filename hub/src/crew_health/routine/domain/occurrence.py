from dataclasses import dataclass, replace
from datetime import datetime

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import InvalidBody, InvalidTransition
from crew_health.routine.domain.vocabulary import (
    FINAL_STATES,
    ActorKind,
    CauseKind,
    DueKind,
    OccurrenceState,
    OriginMode,
    Overlay,
    SkipReason,
)


@dataclass(frozen=True, slots=True)
class Outcome:
    observation_ids: tuple[str, ...]
    answer_ids: tuple[str, ...]
    note: str | None


@dataclass(frozen=True, slots=True)
class Skip:
    reason: SkipReason
    note: str | None
    at: datetime

    def __post_init__(self) -> None:
        require_utc(self.at)


@dataclass(frozen=True, slots=True)
class Occurrence:
    occurrence_id: str
    revision: int
    person_id: str
    item_id: str
    environment_id: str
    mission_day: int
    cause_kind: CauseKind
    cause_key: str
    periodic: bool
    override_id: str | None
    binding_id: str | None
    started_by: str | None
    insight_id: str | None
    state: OccurrenceState
    due_from: datetime | None
    base_due_until: datetime | None
    due_until: datetime | None
    due_kind: DueKind
    later_until: datetime | None
    completed_at: datetime | None
    outcome: Outcome | None
    skip: Skip | None
    miss_counted: bool | None
    end_reason: str | None
    guidance_release_id: str | None
    config_release_id: str
    origin_mode: OriginMode
    clock_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for instant in (
            self.due_from,
            self.base_due_until,
            self.due_until,
            self.later_until,
            self.completed_at,
            self.created_at,
        ):
            if instant is not None:
                require_utc(instant)
        if self.revision < 1:
            raise InvalidBody("/revision", "positive_revision_required")
        if (self.state == "done") != (self.completed_at is not None and self.outcome is not None):
            raise InvalidBody("/state", "done_requires_completion")
        if self.state != "done" and (self.completed_at is not None or self.outcome is not None):
            raise InvalidBody("/outcome", "only_done_has_outcome")
        if (self.state == "skipped") != (self.skip is not None):
            raise InvalidBody("/skip", "skip_state_mismatch")
        if (self.state == "missed") != (self.miss_counted is not None):
            raise InvalidBody("/miss_counted", "miss_state_mismatch")
        if self.periodic and self.cause_kind not in ("cadence", "override"):
            raise InvalidBody("/periodic", "nonperiodic_cause")
        if self.cause_kind == "assistant" and not self.insight_id:
            raise InvalidBody("/insight_id", "assistant_requires_insight")


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    occurrence_id: str
    revision: int
    from_state: OccurrenceState | None
    to_state: OccurrenceState
    at: datetime
    actor_kind: ActorKind
    actor_id: str
    reason_code: str | None
    miss_counted: bool | None

    def __post_init__(self) -> None:
        require_utc(self.at)
        if not self.actor_id:
            raise InvalidBody("/actor_id", "actor_required")


def _open(occ: Occurrence) -> None:
    if occ.state in FINAL_STATES:
        raise InvalidTransition(occ.revision)


def _transition(
    old: Occurrence,
    new: Occurrence,
    now: datetime,
    actor_kind: ActorKind,
    actor_id: str,
    reason: str | None = None,
) -> tuple[Occurrence, TransitionRecord]:
    return new, TransitionRecord(
        new.occurrence_id,
        new.revision,
        old.state,
        new.state,
        now,
        actor_kind,
        actor_id,
        reason,
        new.miss_counted,
    )


def display_state(
    occ: Occurrence, *, now: datetime, today: int, held_kind: Overlay | None
) -> tuple[OccurrenceState, Overlay | None]:
    if occ.state in FINAL_STATES:
        return occ.state, None
    if occ.state == "not_due" and occ.mission_day > today:
        return "not_due", None
    if held_kind is not None:
        return "later", held_kind
    if occ.due_until is not None and now >= occ.due_until:
        return "missed", None
    if occ.state == "due":
        return "due", None
    if occ.mission_day > today:
        return "not_due", None
    if (occ.due_from is not None and now < occ.due_from) or (
        occ.later_until is not None and now < occ.later_until
    ):
        return "later", None
    return "due", None


def complete(
    occ: Occurrence,
    *,
    now: datetime,
    completed_at: datetime | None,
    outcome: Outcome,
    actor_kind: ActorKind,
    actor_id: str,
    day_start: datetime,
    guidance_release_id: str,
) -> tuple[Occurrence, TransitionRecord]:
    _open(occ)
    at = now if completed_at is None else completed_at
    require_utc(at)
    if at > now or at < day_start or (occ.due_until is not None and at > occ.due_until):
        raise InvalidBody("/completed_at", "outside_completion_window")
    new = replace(
        occ,
        revision=occ.revision + 1,
        state="done",
        completed_at=at,
        outcome=outcome,
        later_until=None,
        guidance_release_id=guidance_release_id,
    )
    return _transition(occ, new, now, actor_kind, actor_id)


def skip(
    occ: Occurrence,
    *,
    now: datetime,
    reason: SkipReason,
    note: str | None,
    linked: bool,
    actor_kind: ActorKind,
    actor_id: str,
) -> tuple[Occurrence, TransitionRecord]:
    _open(occ)
    if linked:
        raise InvalidBody("/observation_ids", "skip_with_links")
    new = replace(
        occ,
        revision=occ.revision + 1,
        state="skipped",
        skip=Skip(reason, note, now),
        outcome=None,
        later_until=None,
    )
    return _transition(occ, new, now, actor_kind, actor_id, reason)


def later(
    occ: Occurrence, *, now: datetime, later_until: datetime, held: bool, actor_kind: ActorKind, actor_id: str
) -> tuple[Occurrence, TransitionRecord]:
    _open(occ)
    if held:
        raise InvalidTransition(occ.revision)
    require_utc(later_until)
    if occ.due_until is None or later_until <= now or later_until > occ.due_until:
        raise InvalidBody("/later_until", "outside_due_window")
    new = replace(occ, revision=occ.revision + 1, state="later", later_until=later_until)
    return _transition(occ, new, now, actor_kind, actor_id)


def miss(
    occ: Occurrence, *, now: datetime, counted: bool, reason: str, actor_id: str
) -> tuple[Occurrence, TransitionRecord]:
    _open(occ)
    new = replace(
        occ,
        revision=occ.revision + 1,
        state="missed",
        outcome=None,
        miss_counted=counted,
        end_reason=reason,
        later_until=None,
    )
    return _transition(occ, new, now, "engine", actor_id, reason)


def allowed_actions(occ: Occurrence, *, session_person_id: str | None, held: bool) -> tuple[str, ...]:
    if occ.person_id != session_person_id or occ.state in FINAL_STATES or occ.state == "not_due":
        return ()
    return ("complete", "skip") if held else ("complete", "skip", "later")


def extend_for_hold(occ: Occurrence, due_until: datetime | None) -> Occurrence:
    _open(occ)
    if due_until is not None and occ.base_due_until is not None and due_until < occ.base_due_until:
        raise InvalidBody("/due_until", "before_base_deadline")
    return replace(occ, revision=occ.revision + 1, due_until=due_until)
