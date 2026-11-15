from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import InvalidBody


@dataclass(frozen=True, slots=True)
class QuestionInstance:
    instance_id: str
    override_id: str
    person_id: str
    question_set_id: str
    slot: str
    question_ids: tuple[str, ...]
    due_from: datetime
    due_until: datetime
    answers: Mapping[str, str]
    state: Literal["open", "answered", "partially_answered", "unanswered"]  # internal enum
    revision: int

    def __post_init__(self) -> None:
        require_utc(self.due_from)
        require_utc(self.due_until)
        if self.revision < 1 or self.due_until <= self.due_from:
            raise InvalidBody("/instance", "invalid_window_or_revision")
        object.__setattr__(self, "answers", MappingProxyType(dict(self.answers)))


def plan_slots(
    *, source_at: datetime, repeat_after_minutes: tuple[int, ...], day_end_of: Callable[[datetime], datetime]
) -> tuple[tuple[str, datetime, datetime], ...]:
    require_utc(source_at)
    slots = [("initial", source_at, day_end_of(source_at))]
    for i, minutes in enumerate(repeat_after_minutes):
        at = source_at + timedelta(minutes=minutes)
        until = day_end_of(at)
        require_utc(until)
        slots.append((f"repeat:{i}", at, until))
    require_utc(slots[0][2])
    return tuple(slots)


def apply_answer(
    inst: QuestionInstance, *, question_id: str, answer_id: str, answered_at: datetime
) -> QuestionInstance | None:
    require_utc(answered_at)
    if (
        inst.state != "open"
        or question_id not in inst.question_ids
        or question_id in inst.answers
        or not inst.due_from <= answered_at < inst.due_until
    ):
        return None
    answers = {**inst.answers, question_id: answer_id}
    state: Literal["open", "answered"] = (
        "answered" if all(q in answers for q in inst.question_ids) else "open"
    )  # internal enum
    return replace(inst, answers=answers, state=state, revision=inst.revision + 1)


def expire(inst: QuestionInstance, *, now: datetime) -> QuestionInstance:
    require_utc(now)
    if inst.state != "open" or now < inst.due_until:
        return inst
    return replace(
        inst, state="partially_answered" if inst.answers else "unanswered", revision=inst.revision + 1
    )


def pick_instance(
    open_instances: Sequence[QuestionInstance], question_id: str, answered_at: datetime
) -> QuestionInstance | None:
    eligible = [
        i
        for i in open_instances
        if i.state == "open"
        and question_id in i.question_ids
        and question_id not in i.answers
        and i.due_from <= answered_at < i.due_until
    ]
    return min(eligible, key=lambda i: (i.due_from, i.instance_id), default=None)
