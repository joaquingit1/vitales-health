"""Pure measurement request, attempt and gap invariants (E05 3; CONTRACTS 4, 21).

No I/O, clock or configuration reads: callers pass facts in. Every rule here is a safety invariant in code
(AGENTS 1 rule 1: invariants are never configuration).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, get_args

from crew_health.kernel.units import Quantity
from crew_health.measurement.domain.errors import ActionNotAllowed, InvalidTransition, MeasurementInvariant

type RequestState = Literal[
    "proposed",
    "awaiting_acceptance",
    "accepted",
    "in_progress",
    "completed",
    "unavailable",
    "declined",
    "cancelled",
    "failed",
]
type Action = Literal["submit", "accept", "decline", "start", "cancel"]
type AttemptOutcome = Literal["success", "failed", "rejected"]
type Disposition = Literal["usable", "not_usable", "quarantined"]
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type OperatorRequirement = Literal["self", "crewmate", "crew_medical_officer"]
type GapReason = Literal["no_capability", "device_absent", "operator_absent", "context_inapplicable"]
type CapabilityStatus = Literal["available", "unavailable", "not_applicable"]
type UnavailableReason = Literal[
    "not_in_kit", "device_absent", "operator_absent", "context_inapplicable", "adapter_down"
]
type RequesterKind = Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"]

REQUEST_STATES: tuple[RequestState, ...] = get_args(RequestState.__value__)
ACTIONS: tuple[Action, ...] = get_args(Action.__value__)
ORIGIN_MODES: tuple[OriginMode, ...] = get_args(OriginMode.__value__)
GAP_REASONS: tuple[GapReason, ...] = get_args(GapReason.__value__)
TERMINAL_STATES: frozenset[RequestState] = frozenset(
    {"completed", "unavailable", "declined", "cancelled", "failed"}
)

# Acquisition methods that need no running adapter: a person reads or reports the value (contract vocabulary,
# common.schema.json acquisition_method). A capability with one of them stays usable with every adapter down.
MANUAL_METHODS: frozenset[str] = frozenset(
    {"manual_instrument", "self_report", "crewmate_palpation", "clinician_entry"}
)

_FROM: Mapping[Action, frozenset[RequestState]] = {
    "submit": frozenset({"proposed"}),
    "accept": frozenset({"awaiting_acceptance"}),
    "decline": frozenset({"proposed", "awaiting_acceptance"}),
    "start": frozenset({"accepted"}),
    "cancel": frozenset({"proposed", "awaiting_acceptance", "accepted", "in_progress"}),
}
_TO: Mapping[Action, RequestState] = {
    "submit": "awaiting_acceptance",
    "accept": "accepted",
    "decline": "declined",
    "start": "in_progress",
    "cancel": "cancelled",
}


@dataclass(frozen=True, slots=True)
class KitCheck:
    """CONTRACTS 21 KitCheck, computed once at proposal and never recomputed into this record."""

    can: bool
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]
    cliff_urls: tuple[str, ...]
    text: str
    kit_map_version: str
    environment_id: str

    def __post_init__(self) -> None:
        if self.can and (not self.instrument_ids or self.missing_instrument_ids):
            raise MeasurementInvariant("kit_check_can_requires_instruments")
        if not self.can and self.instrument_ids:
            raise MeasurementInvariant("kit_check_cannot_has_no_instruments")
        if not self.text:
            raise MeasurementInvariant("kit_check_text_required")


@dataclass(frozen=True, slots=True)
class Requester:
    kind: RequesterKind
    id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class HeldValue:
    """A real value the adapter sent that is not usable evidence: kept, never discarded, never a result."""

    patient_id: str
    code: str
    quantity: Quantity
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: str
    device_id: str | None


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    sequence: int
    adapter_id: str
    generation: int
    started_at: datetime
    ended_at: datetime | None
    outcome: AttemptOutcome
    failure_reason: str | None
    observation_ids: tuple[str, ...]
    quality_notes: tuple[str, ...]
    disposition: Disposition
    held_values: tuple[HeldValue, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.generation < 1:
            raise MeasurementInvariant("attempt_sequence_and_generation_positive")
        if self.disposition != "usable" and self.observation_ids:
            raise MeasurementInvariant("only_usable_attempts_reference_records")
        if self.outcome != "success" and self.failure_reason is None:
            raise MeasurementInvariant("failed_attempt_keeps_its_reason")


@dataclass(frozen=True, slots=True)
class MeasurementRequest:
    request_id: str
    revision: int
    episode_id: str | None
    capability_code: str
    reason: str
    decision_affected: str
    kit_check: KitCheck
    requester: Requester
    patient_id: str
    operator_id: str | None
    consent_ref: str | None
    state: RequestState
    attempts: tuple[Attempt, ...]
    result_observation_ids: tuple[str, ...]
    body_region: str | None
    created_at: datetime
    origin_mode: OriginMode
    mission_id: str
    release_id: str
    operator_requirement: OperatorRequirement
    methods: tuple[str, ...]
    generation: int
    unavailable_cause: GapReason | None
    created_by: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.revision < 1 or self.generation < 0:
            raise MeasurementInvariant("revision_and_generation_bounds")
        if not self.kit_check.can and self.state != "unavailable":
            raise MeasurementInvariant("kit_refusal_is_unavailable")
        if self.state == "completed" and not self.result_observation_ids:
            raise MeasurementInvariant("completed_requires_accepted_result")
        usable = {o for a in self.attempts if a.disposition == "usable" for o in a.observation_ids}
        if not set(self.result_observation_ids) <= usable:
            raise MeasurementInvariant("results_come_from_usable_attempts")


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """CONTRACTS 2 CapabilityGap. Opened by the controller only; no automatic open to addressed transition
    exists (a normal pulse, an import, a forecast or a guided check never closes an ECG gap, I7)."""

    gap_id: str
    episode_id: str
    capability_code: str
    reason: GapReason
    consequence: str
    alternatives: tuple[str, ...]
    body_region: str | None
    state: Literal["open", "addressed"]
    plan_id: str | None
    check_another_way_step_ids: tuple[str, ...]
    source_request_id: str | None
    release_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.consequence:
            raise MeasurementInvariant("gap_consequence_required")
        if self.plan_id is None and self.check_another_way_step_ids:
            raise MeasurementInvariant("gap_steps_need_a_plan")


@dataclass(frozen=True, slots=True)
class Viewer:
    """Who acts or reads, as facts the application resolved from the authenticated session."""

    person_id: str | None
    is_medical_officer: bool


def is_subject(request: MeasurementRequest, viewer: Viewer) -> bool:
    return viewer.person_id is not None and viewer.person_id == request.patient_id


def is_bound_operator(request: MeasurementRequest, viewer: Viewer) -> bool:
    return viewer.person_id is not None and viewer.person_id == request.operator_id


def is_requester(request: MeasurementRequest, viewer: Viewer) -> bool:
    return (
        viewer.person_id is not None
        and request.requester.kind == "person"
        and request.requester.id == viewer.person_id
    )


def operator_relation_valid(requirement: OperatorRequirement, subject_id: str, operator_id: str) -> bool:
    """Contract operator_requirement: self is the subject; anyone else is never the subject."""
    if requirement == "self":
        return operator_id == subject_id
    return operator_id != subject_id


def qualified(request: MeasurementRequest, viewer: Viewer) -> bool:
    """The operator relation and qualification hold for this person, whether assigned or not."""
    if viewer.person_id is None:
        return False
    if not operator_relation_valid(request.operator_requirement, request.patient_id, viewer.person_id):
        return False
    return request.operator_requirement != "crew_medical_officer" or viewer.is_medical_officer


def may_accept(request: MeasurementRequest, viewer: Viewer) -> bool:
    if request.operator_id is not None and viewer.person_id != request.operator_id:
        return False
    return qualified(request, viewer)


def actor_may(action: Action, request: MeasurementRequest, viewer: Viewer) -> bool:
    """Per-action actor rules. A non-subject operator cannot decline before acceptance: the minimal
    invitation projection that would let them see the task without its details is R-E05-4."""
    if action == "submit":
        return is_subject(request, viewer) or is_requester(request, viewer)
    if action == "accept":
        return may_accept(request, viewer)
    if action == "decline":
        return is_subject(request, viewer)
    if action == "start":
        return is_bound_operator(request, viewer) and qualified(request, viewer)
    return is_subject(request, viewer) or is_requester(request, viewer) or is_bound_operator(request, viewer)


def allowed_actions(request: MeasurementRequest, viewer: Viewer) -> tuple[Action, ...]:
    return tuple(a for a in ACTIONS if request.state in _FROM[a] and actor_may(a, request, viewer))


def decide(request: MeasurementRequest, action: Action, viewer: Viewer) -> RequestState:
    """Next state for an action, or a typed refusal. A client never sets state directly."""
    if request.state not in _FROM[action]:
        raise InvalidTransition()
    if not actor_may(action, request, viewer):
        raise ActionNotAllowed()
    return _TO[action]


@dataclass(frozen=True, slots=True)
class AttemptEffect:
    disposition: Disposition
    state: RequestState


def attempt_effect(
    request: MeasurementRequest,
    *,
    outcome: AttemptOutcome,
    value_count: int,
    max_attempts: int,
    max_late: int,
) -> AttemptEffect:
    """CONTRACTS 4: only completed with accepted observations is usable; a retry appends a new attempt and
    never rewrites failed history; a late result after cancellation, completion or exhaustion is retained as
    quarantined evidence, never used. Live and late attempts have separate bounds."""
    if max_attempts < 1 or max_late < 1 or value_count < 0:
        raise MeasurementInvariant("attempt_bounds")
    if request.state in TERMINAL_STATES and request.generation >= 1:
        late = sum(1 for a in request.attempts if a.disposition == "quarantined")
        if late >= max_late:
            raise InvalidTransition()
        return AttemptEffect("quarantined", request.state)
    if request.state != "in_progress":
        raise InvalidTransition()
    live = sum(1 for a in request.attempts if a.disposition != "quarantined")
    if live >= max_attempts:
        raise InvalidTransition()
    if outcome == "success" and value_count > 0:
        return AttemptEffect("usable", "completed")
    return AttemptEffect("not_usable", "failed" if live + 1 >= max_attempts else "in_progress")


def append_attempt(
    request: MeasurementRequest, attempt: Attempt, effect: AttemptEffect
) -> MeasurementRequest:
    results = request.result_observation_ids
    if effect.disposition == "usable":
        results = results + tuple(o for o in attempt.observation_ids if o not in results)
    return replace(
        request,
        revision=request.revision + 1,
        attempts=request.attempts + (attempt,),
        result_observation_ids=results,
        state=effect.state,
    )


def capability_status(
    *,
    kit_available: bool,
    methods: tuple[str, ...],
    ready_methods: frozenset[str] | None,
) -> tuple[CapabilityStatus, UnavailableReason | None]:
    """E05 overlay on E27's packed half. Never promotes not_in_kit (T27-26). Unknown adapter readiness is
    not a guessed connected state: only a manual method or a reported ready adapter keeps it available."""
    if not kit_available:
        return "unavailable", "not_in_kit"
    if any(m in MANUAL_METHODS for m in methods):
        return "available", None
    if ready_methods is not None and any(m in ready_methods for m in methods):
        return "available", None
    return "unavailable", "adapter_down"


def gap_reason_for(reason: UnavailableReason) -> GapReason:
    """Map a capability or gate refusal to the CapabilityGap reason enum (E05 3)."""
    if reason == "not_in_kit":
        return "no_capability"
    if reason in ("device_absent", "adapter_down"):
        return "device_absent"
    if reason == "operator_absent":
        return "operator_absent"
    return "context_inapplicable"
