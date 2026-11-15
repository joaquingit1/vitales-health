from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, cast

from crew_health.kernel.clock import TimeInput
from crew_health.kernel.config import ConfigName, ReleaseHandle
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.units import Quantity, UnitRegistry
from crew_health.kernel.uow import Actor
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.observations import OriginMode

type Presence = Literal["present", "absent", "unknown", "declined"]
type AnswerValue = Quantity | str | bool | None


@dataclass(frozen=True, slots=True)
class Choice:
    id: str
    presence: Presence | None
    opens: str | None


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    kind: str
    choices: tuple[Choice, ...]
    minimum: int | None
    maximum: int | None
    unit: str | None
    answer_code: str | None


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    answer_id: str
    question_id: str
    questionnaire_version: str
    presence: Presence
    value: AnswerValue
    body_region: str | None
    laterality: str | None
    actor: Actor
    answered_at: datetime
    origin_mode: OriginMode
    supersedes: str | None
    patient_id: str
    mission_id: str
    episode_id: str | None
    release_id: str
    observed: TimeInput
    received_at: datetime
    estimate_observation_id: str | None


@dataclass(frozen=True, slots=True)
class AnswerAccepted:
    event_type: ClassVar[str] = "answer.accepted"
    answer: AnswerRecord
    episode_id: str | None
    mission_id: str

    def __post_init__(self) -> None:
        if self.episode_id != self.answer.episode_id or self.mission_id != self.answer.mission_id:
            raise RecordError(ErrorCode.IDENTITY_MISMATCH)


def load_questions(release: ReleaseHandle) -> dict[str, Question]:
    value: object = json.loads(release.document(ConfigName.QUESTIONNAIRES).raw)
    if not isinstance(value, dict) or not isinstance(value.get("questions"), list):
        raise RecordError(ErrorCode.CONFIG_INVALID)
    result: dict[str, Question] = {}
    for raw in value["questions"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("choices"), list):
            raise RecordError(ErrorCode.CONFIG_INVALID)
        choices = tuple(
            Choice(str(c["id"]), cast(Presence | None, c["presence"]), c["opens"]) for c in raw["choices"]
        )
        scale = raw.get("scale")
        question = Question(
            raw["id"],
            raw["text"],
            raw["kind"],
            choices,
            scale["min"] if isinstance(scale, dict) else None,
            scale["max"] if isinstance(scale, dict) else None,
            raw.get("unit"),
            raw.get("answer_code"),
        )
        if question.id in result:
            raise RecordError(ErrorCode.CONFIG_INVALID)
        result[question.id] = question
    return result


def validate_answer(
    question: Question, *, presence: Presence, value: AnswerValue, units: UnitRegistry
) -> AnswerValue:
    if presence not in ("present", "absent", "unknown", "declined"):
        raise RecordError(ErrorCode.INVALID_VALUE)
    if presence in ("unknown", "declined"):
        if value is not None:
            raise RecordError(ErrorCode.INVALID_VALUE)
        return None
    if question.kind in ("presence", "choice") or (
        question.kind == "confirm_estimate" and isinstance(value, str)
    ):
        return _choice_answer(question, presence, value)
    if not isinstance(value, Quantity) or presence != "present":
        raise RecordError(ErrorCode.INVALID_VALUE)
    if question.kind == "scale":
        return _scale_answer(question, value)
    if question.kind in ("duration", "confirm_estimate") and question.unit:
        if value.value < Decimal(0):
            raise RecordError(ErrorCode.INVALID_VALUE)
        return units.convert(value, units.parse(question.unit))
    raise RecordError(ErrorCode.INVALID_VALUE)


def _choice_answer(question: Question, presence: Presence, value: AnswerValue) -> AnswerValue:
    choices = tuple(c for c in question.choices if c.id == value and c.opens != "correction")
    if len(choices) != 1 or choices[0].presence not in (None, presence):
        raise RecordError(ErrorCode.INVALID_VALUE)
    return value


def _scale_answer(question: Question, value: Quantity) -> Quantity:
    if value.unit.code != "1" or value.value != value.value.to_integral_value():
        raise RecordError(ErrorCode.INVALID_VALUE)
    if question.minimum is None or question.maximum is None:
        raise RecordError(ErrorCode.CONFIG_INVALID)
    if not question.minimum <= value.value <= question.maximum:
        raise RecordError(ErrorCode.INVALID_VALUE)
    return value
