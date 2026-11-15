"""Pure public episode values for consumers; no dependency on an episode implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]


class OnsetValue(Protocol):
    @property
    def at(self) -> datetime | None: ...
    @property
    def precision(self) -> str: ...


@dataclass(frozen=True, slots=True)
class PersonRef:
    person_id: str
    display_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class NewEpisode:
    patient_id: str
    mission_id: str
    origin_mode: OriginMode
    concern_text: str
    onset: OnsetValue
    created_by: PersonRef
    questionnaire_version: str
    red_flag_ids: tuple[str, ...]
    config_release_id: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class EpisodeRef:
    episode_id: str
    revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EpisodeFact:
    episode_id: str
    patient_id: str
    mission_id: str
    origin_mode: OriginMode
    state: str
    revision: int
    questionnaire_version: str
    red_flag_ids: tuple[str, ...]
    route_module_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Viewer:
    kind: str
    person_id: str | None
    roles: frozenset[str]


@dataclass(frozen=True, slots=True)
class CrewMember:
    person_id: str
    display_name: str
    medical_officer_rank: int | None


@dataclass(frozen=True, slots=True)
class Answer:
    answer_id: str
    question_id: str
    questionnaire_version: str
    presence: str
    patient_id: str
    episode_id: str | None
    value: object
    answered_at: datetime
    origin_mode: OriginMode
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationFact:
    observation_id: str
    patient_id: str
    episode_id: str | None
    quality_status: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class EpisodeToolFacts:
    episode_id: str
    patient_id: str
    revision: int
    sections: dict[str, object]
    unavailable: frozenset[str]


@dataclass(frozen=True, slots=True)
class EpisodeCreated:
    event_type: ClassVar[str] = "episode.created"
    episode_id: str
    patient_id: str
    mission_id: str
    origin_mode: OriginMode
    at: datetime


@dataclass(frozen=True, slots=True)
class EpisodeChanged:
    event_type: ClassVar[str] = "episode.changed"
    episode_id: str
    revision: int
    from_state: str
    to_state: str
    change_kind: str
    at: datetime


@dataclass(frozen=True, slots=True)
class LinkedFact:
    event_type: ClassVar[str] = "episode.linked_fact"
    event_id: str
    episode_id: str
    kind: str
    ref_id: str
    state: str | None
    origin_mode: OriginMode
    at: datetime
    supersedes: str | None = None
    is_model: bool = False
    attempt_id: str | None = None
    reason_code: str | None = None
    opens_urgency_cycle: bool = False


@dataclass(frozen=True, slots=True)
class AnswerFact:
    event_type: ClassVar[str] = "episode.answer_fact"
    episode_id: str
    answer_id: str
    question_id: str
    origin_mode: OriginMode
    at: datetime
    supersedes: str | None = None
