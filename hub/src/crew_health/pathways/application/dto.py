"""Canonical public E11 values. Implementation/domain types never cross these seams."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal

from crew_health.guidance.application.ports import GuidanceDecision
from crew_health.kernel.uow import Actor
from crew_health.knowledge.application.dto import PathwayCard

type UrgencyState = Literal["none", "needs_urgent_review", "active_response", "disposition_recorded"]
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type Disposition = Literal[
    "resolved_on_board", "handed_to_crew_medical_officer", "ground_consulted", "false_alarm", "other"
]


@dataclass(frozen=True, slots=True)
class UrgencyToken:
    episode_id: str
    cycle: int
    revision: int


@dataclass(frozen=True, slots=True)
class TurnContext:
    kind: str
    person_id: str | None
    episode_id: str | None


@dataclass(frozen=True, slots=True)
class AssistantContext:
    kind: str
    person_id: str
    episode_id: str
    conversation_id: str
    turn_id: str
    model_release_id: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Proceed:
    token: UrgencyToken | None
    urgent_review_pending: bool
    review_intent_pending: bool = False


@dataclass(frozen=True, slots=True)
class Interrupted:
    help_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RedFlagsPending:
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Refused:
    reason: str


type GateDecision = Proceed | Interrupted | RedFlagsPending | Refused


@dataclass(frozen=True, slots=True)
class DispositionRecord:
    disposition: Disposition
    rationale: str
    by: Actor
    authority: Literal["crew_medical_officer", "acting_medical_officer"]
    role_assignment_id: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class UrgencySummary:
    state: UrgencyState
    cycle: int
    revision: int
    since: datetime | None
    signals_count: int
    help_session_ids: tuple[str, ...]
    disposition: DispositionRecord | None
    review_intent_pending: bool = False


@dataclass(frozen=True, slots=True)
class SignalView:
    seq: int
    kind: str
    ref_id: str
    from_state: UrgencyState
    to_state: UrgencyState
    actor: Actor
    at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class SignalPage:
    items: tuple[SignalView, ...]
    next_after_seq: int | None


@dataclass(frozen=True, slots=True)
class UrgencyChanged:
    event_type: ClassVar[str] = "urgency.changed"
    episode_id: str
    cycle: int
    revision: int
    from_state: UrgencyState
    to_state: UrgencyState
    signal_kind: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ManualAlternative:
    capability_code: str
    method: Literal["manual_instrument"] = "manual_instrument"


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    reason: (
        Literal[
            "no_capability", "context_inapplicable", "device_absent", "operator_absent", "consent_missing"
        ]
        | None
    )
    supported_alternative: ManualAlternative | None = None


@dataclass(frozen=True, slots=True)
class PathwayContext:
    subject_person_id: str
    origin_mode: OriginMode
    environment_id: str
    evidence_release_id: str
    proposed_operator_id: str | None = None


@dataclass(frozen=True, slots=True)
class Discrepancy:
    passage_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ConfirmingTest:
    name: str
    instrument_ids: tuple[str, ...]
    kind: Literal["instrument", "report_or_exam"]
    gate: GateResult


@dataclass(frozen=True, slots=True)
class PathwayEvaluation:
    condition_id: str
    evidence_release_id: str
    card: PathwayCard | None
    decision: GuidanceDecision | None
    discrepancies: tuple[Discrepancy, ...]
    tests: tuple[ConfirmingTest, ...]
    guidance_refused: str | None
