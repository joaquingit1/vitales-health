"""E24's private input types (E24 4.3).

Producers (E23 sensors and mission state, E01 records, E29 ground link, E32 supplies, E13 insights) are
mapped to these values by E24's application layer. The domain imports no producer module.

Times follow SYSTEM 5.2: a sensor reading carries its captured `TimeInput` and the `NormalizedTime` the
hub computed for it, so two readings with the same raw time in different clock segments stay distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from crew_health.kernel.clock import NormalizedTime, TimeInput, require_utc
from crew_health.kernel.units import Quantity
from crew_health.rules.domain.errors import InvalidInput
from crew_health.rules.domain.vocabulary import Level, OriginMode


@dataclass(frozen=True, slots=True)
class ObservedTime:
    """When a value was observed: the captured time input and its normalization (D3)."""

    source: TimeInput
    normalized: NormalizedTime

    @property
    def segment_key(self) -> str:
        s = self.source
        return f"{s.source_clock_id}/{s.segment_id}/{s.source_clock_version}"

    def axis(self) -> datetime | None:
        """The instant dwell and ordering are computed on; `None` when it cannot be trusted.

        LIVE and SIM use the corrected time. REPLAY uses its original data time (D14).
        """
        n = self.normalized
        if n.state == "replay":
            return n.original_at
        if n.state == "known" and n.corrected_at is not None and n.uncertainty_ms is not None:
            return n.corrected_at
        return None

    def interval(self) -> tuple[datetime, datetime] | None:
        """The whole uncertainty interval of a corrected time, or `None` when unknown or future."""
        n = self.normalized
        if n.state != "known" or n.corrected_at is None or n.uncertainty_ms is None:
            return None
        half = timedelta(milliseconds=n.uncertainty_ms)
        return (n.corrected_at - half, n.corrected_at + half)

    def display_at(self) -> datetime:
        n = self.normalized
        if n.state == "known" and n.corrected_at is not None:
            return n.corrected_at
        return n.original_at


@dataclass(frozen=True, slots=True)
class LimitRenderFacts:
    """Formatted by E23 for this one limit; E24 formats no sensor value (E24 5.2)."""

    aggregation_id: str
    aggregation_label: str
    window_seconds: int | None
    value_text: str
    unit: str
    unit_display: str
    limit_value_text: str


@dataclass(frozen=True, slots=True)
class LimitResult:
    limit_id: str
    crossed: bool | None
    clear_holds: bool | None
    value: Quantity | None
    coverage: Decimal | None
    limit_fingerprint: str | None
    render: LimitRenderFacts

    def __post_init__(self) -> None:
        if self.coverage is not None and not (Decimal(0) <= self.coverage <= Decimal(1)):
            raise InvalidInput("coverage outside 0..1")


@dataclass(frozen=True, slots=True)
class ChannelRenderFacts:
    sensor_name: str
    location_label: str | None
    source_label: str
    last_received_time: str | None
    replay_dataset: str | None


ClockState = Literal["trusted", "uncertain", "unknown"]


@dataclass(frozen=True, slots=True)
class SensorEvaluatedInput:
    config_release_id: str
    sensor_type_id: str
    channel_id: str
    location: str | None
    origin_mode: OriginMode
    observed: ObservedTime
    receive_reference: datetime | None
    reading_id: str
    clock_state: ClockState
    freshness: Literal["current", "stale"]
    stale_after_seconds: int
    results: tuple[LimitResult, ...]
    channel: ChannelRenderFacts

    def __post_init__(self) -> None:
        if self.receive_reference is not None:
            require_utc(self.receive_reference)
        if self.stale_after_seconds < 1:
            raise InvalidInput("stale_after_seconds must be positive")

    def result_for(self, limit_id: str) -> LimitResult | None:
        for result in self.results:
            if result.limit_id == limit_id:
                return result
        return None


@dataclass(frozen=True, slots=True)
class FreshnessInput:
    config_release_id: str
    channel_id: str
    sensor_type_id: str
    state: Literal["current", "stale", "never_reported"]
    stale_since: datetime | None
    last_reading_id: str | None
    origin_mode: OriginMode
    observed: ObservedTime | None
    receive_reference: datetime | None
    channel: ChannelRenderFacts

    def __post_init__(self) -> None:
        if self.state == "stale" and self.stale_since is None:
            raise InvalidInput("a stale channel names the instant it went stale")


@dataclass(frozen=True, slots=True)
class MissionEventInput:
    event_id: str
    event_type: str
    label: str
    occurred_at: datetime
    occurred_corrected_at: datetime | None
    recorded_at: datetime
    origin_mode: OriginMode
    environment_id: str
    person_ids: tuple[str, ...]
    location: str | None


@dataclass(frozen=True, slots=True)
class StateFactInput:
    fact: str
    value: bool | str | int | float
    environment_id: str
    at: datetime
    cause_event_id: str
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class AnswerInput:
    answer_id: str
    person_id: str
    question_id: str
    presence: str
    answered_at: datetime
    answered_corrected_at: datetime | None
    origin_mode: OriginMode
    day_label: str
    time_label: str


@dataclass(frozen=True, slots=True)
class GroundLinkInput:
    status: Literal["up", "delayed", "down"]
    next_contact_at: datetime | None
    source: str
    environment_id: str
    gap_planned: bool
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SupplyCoverRow:
    """One supply's cover, mapped from the canonical E32 `CoverFact` (SYSTEM 8.1).

    `partition` and `stock_revision` travel with the row so an older batch never replaces newer state.
    """

    supply_id: str
    supply_name: str
    cover_uses: int | None
    partition: str
    stock_revision: int
    release_id: str
    source_refs: tuple[str, ...]
    quantity_now: Quantity | None
    per_use: Quantity | None
    last_use_id: str | None
    last_use_at: datetime | None


@dataclass(frozen=True, slots=True)
class SupplyCoverInput:
    environment_id: str
    cause: Literal["use", "release", "environment"]
    supply_use_id: str | None
    observed_at: datetime
    origin_mode: OriginMode
    rows: tuple[SupplyCoverRow, ...]


@dataclass(frozen=True, slots=True)
class EvidenceKey:
    kind: Literal["observation", "answer", "daily_check", "routine_occurrence"]
    ref_id: str


@dataclass(frozen=True, slots=True)
class RecordFacts:
    """Facts about a record, never its content."""

    exists: bool
    owner_person_id: str | None
    origin_mode: OriginMode | None
    is_sensor: bool
    quality_status: str | None
    state: str | None
    observed_at: datetime | None
    summary: str | None


@dataclass(frozen=True, slots=True)
class InsightDraft:
    insight_id: str
    person_id: str
    topic_id: str
    level: Level
    title: str
    text: str
    why: str
    headline_fragment: str
    steps: tuple[str, ...]
    body_region: str | None
    evidence: tuple[EvidenceKey, ...]
    habitat_reading_ids: tuple[str, ...]
    habitat_fact_text_present: bool
    routine_occurrence_ids: tuple[str, ...]
    origin_mode: OriginMode
    created_at: datetime
    model_release_id: str


@dataclass(frozen=True, slots=True)
class InsightTopic:
    id: str
    warning_definition_id: str | None
    body_region: str | None


@dataclass(frozen=True, slots=True)
class AssistantPolicy:
    allowed_warning_levels: frozenset[Level]
    topics: tuple[InsightTopic, ...]

    def topic(self, topic_id: str) -> InsightTopic | None:
        for topic in self.topics:
            if topic.id == topic_id:
                return topic
        return None


@dataclass(frozen=True, slots=True)
class InsightInput:
    draft: InsightDraft
    facts: tuple[tuple[EvidenceKey, RecordFacts], ...]
    policy: AssistantPolicy


@dataclass(frozen=True, slots=True)
class InsightExpiredInput:
    insight_id: str
    elapsed_seconds: int  # measured on E01's elapsed anchors, so a clock correction moves it with the clock


@dataclass(frozen=True, slots=True)
class ActivationInput:
    kind: Literal["release", "environment"]
    old_release_id: str
    new_release_id: str
    environment_id: str


@dataclass(frozen=True, slots=True)
class DeadlineInput:
    key: str
    due_at: datetime


type RuleInput = (
    SensorEvaluatedInput
    | FreshnessInput
    | MissionEventInput
    | StateFactInput
    | AnswerInput
    | GroundLinkInput
    | SupplyCoverInput
    | InsightInput
    | InsightExpiredInput
    | ActivationInput
    | DeadlineInput
)
