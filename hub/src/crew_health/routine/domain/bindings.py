from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.release import (
    AnswerOn,
    Binding,
    EnvironmentOn,
    MissionEventOn,
    ObservationOn,
    OverlayOn,
    RoutineRelease,
    WarningRaisedOn,
)
from crew_health.routine.domain.vocabulary import OriginMode, Overlay, SourceKind


@dataclass(frozen=True, slots=True)
class WarningRaisedInput:
    warning_id: str
    definition_id: str
    raised_by: str
    scope: str
    person_id: str | None
    location: str | None
    origin_mode: OriginMode
    raised_at: datetime
    insight_id: str | None
    previous_warning_id: str | None
    environment_id: str

    def __post_init__(self) -> None:
        require_utc(self.raised_at)


@dataclass(frozen=True, slots=True)
class WarningClearedInput:
    warning_id: str
    clear_reason: str
    cleared_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.cleared_at)


@dataclass(frozen=True, slots=True)
class MissionEventInput:
    event_id: str
    event_type: str
    occurred_at: datetime
    origin_mode: OriginMode
    environment_id: str
    person_ids: tuple[str, ...]
    location: str | None

    def __post_init__(self) -> None:
        require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class OverlayInput:
    overlay_id: str
    revision: int
    person_id: str
    kind: Overlay
    state: Literal["planned", "active", "ended"]
    planned_start: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    location: str | None

    def __post_init__(self) -> None:
        for instant in (self.planned_start, self.started_at, self.ended_at):
            if instant is not None:
                require_utc(instant)


@dataclass(frozen=True, slots=True)
class AnswerInput:
    answer_id: str
    person_id: str
    question_id: str
    presence: str
    answered_at: datetime
    origin_mode: OriginMode

    def __post_init__(self) -> None:
        require_utc(self.answered_at)


@dataclass(frozen=True, slots=True)
class EnvironmentInput:
    environment_id: str
    activated_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.activated_at)


@dataclass(frozen=True, slots=True)
class ObservationInput:
    observation_id: str
    patient_id: str
    code: str
    value: Decimal
    unit: str
    observed_at: datetime
    origin_mode: OriginMode
    quality_status: str

    def __post_init__(self) -> None:
        require_utc(self.observed_at)
        if not self.value.is_finite():
            from crew_health.routine.domain.errors import InvalidBody

            raise InvalidBody("/value", "finite_required")


def observation_matches(on: ObservationOn, inp: ObservationInput) -> bool:
    if inp.quality_status != "accepted" or on.code != inp.code or on.unit != inp.unit:
        return False
    if on.comparator == "above":
        return inp.value > on.value
    if on.comparator == "at_or_above":
        return inp.value >= on.value
    if on.comparator == "below":
        return inp.value < on.value
    return inp.value <= on.value


type TriggerInput = (
    ObservationInput
    | WarningRaisedInput
    | WarningClearedInput
    | MissionEventInput
    | OverlayInput
    | AnswerInput
    | EnvironmentInput
)


@dataclass(frozen=True, slots=True)
class Firing:
    binding: Binding
    source_kind: SourceKind
    source_ref: str
    source_at: datetime
    origin_mode: OriginMode | None
    subject_ids: tuple[str, ...]
    location: str | None

    def __post_init__(self) -> None:
        require_utc(self.source_at)


def is_assistant_source(inp: TriggerInput) -> bool:
    return isinstance(inp, WarningRaisedInput) and (
        inp.raised_by == "assistant" or inp.insight_id is not None
    )


def _match(binding: Binding, inp: TriggerInput) -> Firing | None:
    on = binding.on
    if isinstance(on, ObservationOn) and isinstance(inp, ObservationInput) and observation_matches(on, inp):
        return Firing(
            binding,
            "observation",
            inp.observation_id,
            inp.observed_at,
            inp.origin_mode,
            (inp.patient_id,),
            None,
        )
    if (
        isinstance(on, WarningRaisedOn)
        and isinstance(inp, WarningRaisedInput)
        and inp.definition_id in on.definition_ids
    ):
        subjects = () if inp.person_id is None else (inp.person_id,)
        return Firing(
            binding, "warning", inp.warning_id, inp.raised_at, inp.origin_mode, subjects, inp.location
        )
    if (
        isinstance(on, MissionEventOn)
        and isinstance(inp, MissionEventInput)
        and inp.event_type in on.event_types
    ):
        return Firing(
            binding,
            "mission_event",
            inp.event_id,
            inp.occurred_at,
            inp.origin_mode,
            inp.person_ids,
            inp.location,
        )
    if (
        isinstance(on, OverlayOn)
        and isinstance(inp, OverlayInput)
        and on.overlay == inp.kind
        and on.edge == inp.state
    ):
        at = inp.ended_at if on.edge == "ended" else inp.planned_start
        if at is not None:
            return Firing(binding, "overlay", inp.overlay_id, at, None, (inp.person_id,), inp.location)
    if (
        isinstance(on, AnswerOn)
        and isinstance(inp, AnswerInput)
        and on.question_id == inp.question_id
        and inp.presence in on.presences
    ):
        return Firing(
            binding, "answer", inp.answer_id, inp.answered_at, inp.origin_mode, (inp.person_id,), None
        )
    if (
        isinstance(on, EnvironmentOn)
        and isinstance(inp, EnvironmentInput)
        and binding.kind == "cadence_event"
        and inp.environment_id in on.environments
    ):
        # Environment activations are cadence activations, never override source records.
        return Firing(
            binding,
            "mission_event",
            inp.environment_id + ":" + inp.activated_at.isoformat(),
            inp.activated_at,
            None,
            (),
            None,
        )
    return None


def match_bindings(
    release: RoutineRelease, inp: TriggerInput, active_environment_id: str
) -> tuple[Firing, ...]:
    if is_assistant_source(inp):
        return ()
    result: list[Firing] = []
    for binding in release.bindings:
        if binding.kind == "cadence_event" and binding.environment_id not in (None, active_environment_id):
            continue
        if (
            binding.environment_id is not None
            and isinstance(inp, MissionEventInput)
            and inp.environment_id != binding.environment_id
        ):
            continue
        firing = _match(binding, inp)
        if firing is not None:
            result.append(firing)
    return tuple(result)
