"""What the engine decides for one input (E24 4.3).

The application applies a decision in this order: `Clear` (supersessions first), `ResolveOccurrences`,
`Raise`, `Attach`, dwell, deadlines, then outbox and audit. A `Refuse` writes an audit event and nothing
else. A decision never carries a session, a time read from a clock or a value taken from a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.rules.domain.dwell import DwellMarker
from crew_health.rules.domain.occurrences import Occurrence
from crew_health.rules.domain.vocabulary import ClearReason, OriginMode
from crew_health.rules.domain.warning import DefinitionPin, EvidenceRef, TriggerRef


@dataclass(frozen=True, slots=True)
class TimeLabelSpec:
    """How `evidence_time_label` is formatted: mission time HH:MM, or the replay time of a REPLAY reading."""

    kind: Literal["mission", "replay"]  # internal enum
    at: datetime


@dataclass(frozen=True, slots=True)
class RenderValues:
    placeholders: Mapping[str, str]
    evidence_summaries: tuple[str | None, ...]
    time_label: TimeLabelSpec
    assistant_steps: tuple[str, ...]
    body_region: str | None
    replay: bool


@dataclass(frozen=True, slots=True)
class Raise:
    definition_id: str
    scope_key: str
    trigger: TriggerRef
    pin: DefinitionPin
    rule_fingerprint: str
    content_hash: str
    limit_id: str | None
    channel_id: str | None
    person_id: str | None
    location: str | None
    evidence: tuple[EvidenceRef, ...]
    evidence_time: datetime
    origin_mode: OriginMode
    render: RenderValues
    supersedes: tuple[str, ...]
    previous_warning_id: str | None
    insight_id: str | None = None
    source_revision: int | None = None
    source_partition: str | None = None


@dataclass(frozen=True, slots=True)
class Attach:
    warning_id: str
    occurrence: Occurrence


@dataclass(frozen=True, slots=True)
class ResolveOccurrences:
    warning_id: str
    before: datetime
    clear_event_id: str


@dataclass(frozen=True, slots=True)
class Clear:
    warning_id: str
    reason: ClearReason


@dataclass(frozen=True, slots=True)
class PutDwell:
    marker: DwellMarker


@dataclass(frozen=True, slots=True)
class DeleteDwell:
    warning_id: str


@dataclass(frozen=True, slots=True)
class ScheduleDeadline:
    """`absolute` deadlines (a contact time) are never shifted; `elapsed` ones follow the clock."""

    key: str
    kind: Literal["absolute", "elapsed"]  # internal enum
    due_at: datetime | None
    elapsed_seconds: int | None


@dataclass(frozen=True, slots=True)
class CancelDeadline:
    key: str


type RefuseCode = Literal["trigger_spent", "clear_later", "origin_mismatch", "stale_release"]  # internal enum


@dataclass(frozen=True, slots=True)
class Refuse:
    definition_id: str
    scope_key: str
    code: RefuseCode


@dataclass(frozen=True, slots=True)
class RejectInsight:
    insight_id: str
    codes: tuple[str, ...]


type Step = (
    Raise
    | Attach
    | ResolveOccurrences
    | Clear
    | PutDwell
    | DeleteDwell
    | ScheduleDeadline
    | CancelDeadline
    | Refuse
    | RejectInsight
)
type Decision = tuple[Step, ...]
