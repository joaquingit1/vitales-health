"""Measurement use cases (E05 4). One command is one unit of work: state, transition row, audit, public event,
replication declaration, receipt and ledger entry commit together or not at all (SYSTEM 5.7, 5.8).

A kit refusal is a committed domain outcome (unavailable request, gap where an episode exists, receipt), returned
with its 422 after commit; it is never an exception that rolls the record back (CONTRACTS 1198).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import (
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
    canonical_json,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity, UnitRegistry
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.measurement.application.access import can_read, consented, is_crew_person, viewer_of
from crew_health.measurement.application.errors import (
    IdentityMismatch,
    InvalidInput,
    InvalidValue,
    MeasurementError,
    problem_of,
)
from crew_health.measurement.application.ports import (
    AdapterAssignmentPort,
    ConsentQueries,
    EpisodeFacts,
    GapPlanPort,
    KitGate,
    MissionFacts,
    MissionStatePort,
    ObservationCommands,
    ObservationDraft,
    ProposalInput,
    ProposalResult,
    RequestGatePort,
    View,
)
from crew_health.measurement.application.queries import AUDIENCE_RULE, CapabilityService
from crew_health.measurement.application.representation import request_wire
from crew_health.measurement.application.store import AcquisitionBinding, MeasurementStore, Transition
from crew_health.measurement.application.wire import format_instant
from crew_health.measurement.domain.errors import DomainRefusal
from crew_health.measurement.domain.model import (
    Action,
    Attempt,
    AttemptOutcome,
    CapabilityGap,
    GapReason,
    HeldValue,
    KitCheck,
    MeasurementRequest,
    OriginMode,
    Requester,
    RequestState,
    append_attempt,
    attempt_effect,
    decide,
    gap_reason_for,
    may_accept,
    operator_relation_valid,
)

PROJECTION = AudienceProjectionKey(owner="measurement", name="measurement_request", version=1)
POLICY_VERSION = "1"
type Refusal = Literal["KIT_CANNOT_PERFORM", "CAPABILITY_UNAVAILABLE", "CONTEXT_INAPPLICABLE"]
_REFUSALS: dict[str, Refusal] = {
    "KIT_CANNOT_PERFORM": "KIT_CANNOT_PERFORM",
    "CAPABILITY_UNAVAILABLE": "CAPABILITY_UNAVAILABLE",
    "CONTEXT_INAPPLICABLE": "CONTEXT_INAPPLICABLE",
}


@dataclass(frozen=True, slots=True)
class Limits:
    """Registered hub settings (E05 11), read once at startup."""

    max_attempts: int
    max_late_attempts: int
    max_observations: int
    max_text_bytes: int
    max_notes: int
    max_context_entries: int
    replay_window_ms: int


@dataclass(frozen=True, slots=True)
class Providers:
    """Consumed ports. Optional providers may be absent; a missing fact never implies permission."""

    kit_gate: KitGate | None
    mission: MissionStatePort | None
    request_gate: RequestGatePort | None
    consent: ConsentQueries | None
    episodes: EpisodeFacts | None
    gap_plans: GapPlanPort | None
    observations: ObservationCommands | None
    assignments: AdapterAssignmentPort | None
    units: UnitRegistry


@dataclass(frozen=True, slots=True)
class Platform:
    uows: UnitOfWorkFactory
    ids: IdFactory
    ledger: IdempotencyStore
    receipts: CommandReceipts


@dataclass(frozen=True, slots=True)
class ObservationInput:
    patient_id: str
    episode_id: str | None
    code: str
    value: Decimal
    unit: str
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: str
    device_id: str | None
    context: tuple[tuple[str, str], ...]
    body_region: str | None
    supersedes: str | None
    measurement_request_id: str | None


@dataclass(frozen=True, slots=True)
class AttemptInput:
    sequence: int
    started_at: datetime
    ended_at: datetime
    outcome: AttemptOutcome
    failure_reason: str | None
    observations: tuple[ObservationInput, ...]
    quality_notes: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    status: int
    body: dict[str, object]
    request_id: str
    revision: int
    release_id: str
    refusal: Refusal | None


def _problem(code: ErrorCode, current_state: dict[str, object] | None = None) -> ProblemError:
    return ProblemError(code, code.value, current_state=current_state)


def _stored_revision(safe_body: bytes | None) -> int | None:
    if safe_body is None:
        return None
    try:
        loaded = json.loads(safe_body)
    except ValueError:
        return None
    value = loaded.get("revision") if isinstance(loaded, dict) else None
    return value if type(value) is int and value >= 1 else None


def _not_allowed(detail_code: str) -> MeasurementError:
    return MeasurementError(ErrorCode.ACTION_NOT_ALLOWED, field="/action", detail_code=detail_code)


def _check_release(uow: UnitOfWork, config_release: str | None) -> None:
    """ConfigReleaseIn: a client that rendered from another release than the active one is refused (409)."""
    if config_release is not None and config_release != uow.release.release_id:
        raise _problem(ErrorCode.CONFIG_VERSION_MISMATCH)


class MeasurementService:
    """MeasurementCommands, GapProposalPort and MeasurementAssignmentPort provider."""

    def __init__(
        self,
        *,
        platform: Platform,
        store: MeasurementStore,
        capabilities: CapabilityService,
        providers: Providers,
        limits: Limits,
    ) -> None:
        self._p = platform
        self._store = store
        self._caps = capabilities
        self._prov = providers
        self._limits = limits

    # ------------------------------------------------------------------------------------------------ create

    def create(
        self,
        session: SessionContext,
        identity: CommandIdentity,
        proposal: ProposalInput,
        *,
        correlation_id: str,
        config_release: str | None = None,
    ) -> CommandOutcome:
        if not is_crew_person(session) or session.person_id is None:
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        with self._p.uows.begin(actor=session.actor, command=identity, correlation_id=correlation_id) as uow:
            replayed = self._replay(uow, session, identity)
            if replayed is not None:
                return replayed
            _check_release(uow, config_release)
            subject = self._bind_subject(uow, session, proposal.episode_id)
            requester = Requester("person", session.person_id, None)
            request, result = self._propose(uow, requester, subject, proposal, created_by=session.person_id)
            status = 422 if result.refusal is not None else 201
            self._record(uow, identity, status, request, result.refusal or "created")
            return self._outcome(uow, session, request, status, result.refusal)

    def propose(
        self, uow: UnitOfWork, *, requester: Actor, subject_person_id: str, proposal: ProposalInput
    ) -> ProposalResult:
        """Commit-only controller proposal inside the caller's unit of work (E12/E13 apply phase)."""
        try:
            _, result = self._propose(
                uow,
                Requester(requester.kind, requester.id, None),
                subject_person_id,
                proposal,
                created_by=None,
            )
        except DomainRefusal as error:
            raise problem_of(error) from error
        return result

    def _bind_subject(self, uow: UnitOfWork, session: SessionContext, episode_id: str | None) -> str:
        if episode_id is None:
            if session.person_id is None:
                raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
            return session.person_id
        episodes = self._prov.episodes
        fact = None if episodes is None else episodes.get(uow, episode_id)
        if episodes is None or fact is None or not episodes.visible_to(uow, episode_id, session.actor):
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        return fact.patient_id

    def _mission(self, uow: UnitOfWork) -> MissionFacts:
        facts = None if self._prov.mission is None else self._prov.mission.current(uow)
        if facts is None or self._prov.kit_gate is None:
            raise MeasurementError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                field="/capability_code",
                detail_code="prerequisites_unknown",
            )
        return facts

    def _propose(
        self,
        uow: UnitOfWork,
        requester: Requester,
        subject: str,
        proposal: ProposalInput,
        *,
        created_by: str | None,
    ) -> tuple[MeasurementRequest, ProposalResult]:
        self._text(proposal.reason, "/reason")
        self._text(proposal.decision_affected, "/decision_affected")
        mission = self._mission(uow)
        if proposal.episode_id is not None:
            episodes = self._prov.episodes
            fact = None if episodes is None else episodes.get(uow, proposal.episode_id)
            if fact is None or fact.patient_id != subject or fact.mission_id != mission.mission_id:
                raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        capability = self._caps.get(uow, proposal.capability_code)
        if capability is None:
            raise InvalidValue("/capability_code", "unknown_capability")
        operator = proposal.operator_id
        if operator is not None and not operator_relation_valid(
            capability.operator_requirement, subject, operator
        ):
            raise InvalidValue("/operator_id", "operator_requirement")
        if operator is not None and capability.operator_requirement == "crew_medical_officer":
            raise InvalidValue("/operator_id", "operator_qualification_unknown")
        if operator is None and capability.operator_requirement == "self":
            operator = subject
        kit_gate = self._prov.kit_gate
        if kit_gate is None:
            raise MeasurementError(ErrorCode.CAPABILITY_UNAVAILABLE, detail_code="prerequisites_unknown")
        outcome = kit_gate.check(
            uow,
            capability_code=proposal.capability_code,
            environment_id=mission.active_environment_id,
            condition_ids=proposal.condition_ids,
        )
        cause, refusal = self._gate(uow, outcome.kit_check, proposal.capability_code, subject, operator)
        request = MeasurementRequest(
            request_id=self._p.ids.new("mrq"),
            revision=1,
            episode_id=proposal.episode_id,
            capability_code=proposal.capability_code,
            reason=proposal.reason,
            decision_affected=proposal.decision_affected,
            kit_check=outcome.kit_check,
            requester=requester,
            patient_id=subject,
            operator_id=operator,
            consent_ref=None,
            state="unavailable" if refusal is not None else "proposed",
            attempts=(),
            result_observation_ids=(),
            body_region=proposal.body_region if proposal.body_region_given else capability.body_region,
            created_at=uow.now,
            origin_mode=mission.mission_mode,
            mission_id=mission.mission_id,
            release_id=uow.release.release_id,
            operator_requirement=capability.operator_requirement,
            methods=capability.methods,
            generation=0,
            unavailable_cause=cause,
            created_by=created_by,
        )
        self._store.insert_request(uow.connection, request)
        self._changed(uow, request, None, "created", None, operation="create")
        gap_id = None
        if cause is not None:
            gap_id = self._open_gap(
                uow, request, cause, capability.body_region, mission.active_environment_id
            )
        return request, ProposalResult(request.request_id, request.state, refusal, gap_id)

    def _gate(
        self, uow: UnitOfWork, kit: KitCheck, code: str, subject: str, operator: str | None
    ) -> tuple[GapReason | None, Refusal | None]:
        """E05 4 gate order after access and references: kit, applicability, operator, readiness. Consent waits
        for acceptance (E11 3.7: a missing consent leaves the request waiting, it is not a gap)."""
        if not kit.can:
            return "no_capability", "KIT_CANNOT_PERFORM"
        gate = self._prov.request_gate
        if gate is not None:
            result = gate.check(uow, code, subject, operator)
            if result.passed or result.reason in (None, "consent_missing"):
                return None, None
            if result.reason == "no_capability":
                return "no_capability", "KIT_CANNOT_PERFORM"
            if result.reason == "context_inapplicable":
                return "context_inapplicable", "CONTEXT_INAPPLICABLE"
            if result.reason == "operator_absent":
                return "operator_absent", "CAPABILITY_UNAVAILABLE"
            return "device_absent", "CAPABILITY_UNAVAILABLE"
        capability = self._caps.get(uow, code)
        if capability is None:
            raise InvalidValue("/capability_code", "unknown_capability")
        if capability.status == "not_applicable":
            return "context_inapplicable", "CONTEXT_INAPPLICABLE"
        if capability.status == "unavailable" and capability.unavailable_reason is not None:
            reason = gap_reason_for(capability.unavailable_reason)
            return reason, (
                "CONTEXT_INAPPLICABLE" if reason == "context_inapplicable" else "CAPABILITY_UNAVAILABLE"
            )
        return None, None

    def _open_gap(
        self,
        uow: UnitOfWork,
        request: MeasurementRequest,
        reason: GapReason,
        region: str | None,
        environment_id: str,
    ) -> str | None:
        """One request opens at most one gap. Without an episode no gap is invented: audited no_episode."""
        if request.episode_id is None:
            self._audit(uow, "gap_not_opened", {"request_id": request.request_id}, {"reason": "no_episode"})
            return None
        plans = self._prov.gap_plans
        plan = None
        if plans is not None:
            plan = plans.plan_for(
                condition_ids=request.kit_check.condition_ids,
                environment_id=environment_id,
                subject_person_id=request.patient_id,
            )
        gap = CapabilityGap(
            gap_id=self._p.ids.new("gap"),
            episode_id=request.episode_id,
            capability_code=request.capability_code,
            reason=reason,
            consequence=request.decision_affected,
            alternatives=(),
            body_region=region,
            state="open",
            plan_id=None if plan is None else plan.plan_id,
            check_another_way_step_ids=() if plan is None else plan.check_another_way_step_ids,
            source_request_id=request.request_id,
            release_id=request.release_id,
            created_at=uow.now,
        )
        self._store.insert_gap(uow.connection, gap)
        self._audit(
            uow,
            "gap_opened",
            {"gap_id": gap.gap_id, "request_id": request.request_id, "episode_id": gap.episode_id},
            {"reason": reason},
        )
        return gap.gap_id

    # ------------------------------------------------------------------------------------------------ actions

    def act(
        self,
        session: SessionContext,
        identity: CommandIdentity,
        request_id: str,
        *,
        if_match: int | None,
        action: Action,
        correlation_id: str,
        config_release: str | None = None,
    ) -> CommandOutcome:
        if not is_crew_person(session) or session.person_id is None:
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        with self._p.uows.begin(actor=session.actor, command=identity, correlation_id=correlation_id) as uow:
            replayed = self._replay(uow, session, identity)
            if replayed is not None:
                return replayed
            _check_release(uow, config_release)
            request = self._store.get_request(uow.connection, request_id)
            if request is None:
                raise _problem(ErrorCode.NOT_FOUND)
            viewer = viewer_of(session)
            readable = can_read(uow, self._prov.consent, request, session)
            if not readable and not (action == "accept" and may_accept(request, viewer)):
                raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
            current = request_wire(request, viewer) if readable else None
            if if_match is None:
                raise _problem(ErrorCode.VERSION_REQUIRED)
            if if_match != request.revision:
                raise _problem(ErrorCode.STALE_VERSION, current)
            try:
                target = decide(request, action, viewer)
            except DomainRefusal as error:
                state = current if error.kind == "INVALID_TRANSITION" else None
                raise problem_of(error, current_state=state) from error
            updated = self._apply_action(uow, request, action, target, session.person_id)
            self._record(uow, identity, 200, updated, action)
            return self._outcome(uow, session, updated, 200, None)

    def _apply_action(
        self, uow: UnitOfWork, request: MeasurementRequest, action: Action, target: RequestState, person: str
    ) -> MeasurementRequest:
        """Authorization, consent and assignment are settled before anything is written or returned; only then
        can a confirmed loss of capability record the request as unavailable."""
        operator = request.operator_id
        if action == "accept":
            operator = operator or person
            self._require_consent(uow, request, operator)
        binding = None
        if action == "start":
            if operator is None:
                raise _not_allowed("operator_unassigned")
            self._require_consent(uow, request, operator)
            binding = self._assign(uow, request, operator)
        if action in ("submit", "accept", "start"):
            cause = self._recheck(uow, request, operator)
            if cause is not None:
                return self._become_unavailable(uow, request, cause)
        generation = request.generation
        if binding is not None:
            generation = binding.generation
            self._store.start_acquisition(uow.connection, binding, at=uow.now)
        if action == "cancel" and request.generation >= 1:
            self._store.fence_acquisition(uow.connection, request.request_id, request.generation, at=uow.now)
        updated = replace(
            request, revision=request.revision + 1, state=target, operator_id=operator, generation=generation
        )
        self._store.update_request(uow.connection, updated, expected_revision=request.revision)
        self._changed(uow, updated, request.state, action, None, operation="version")
        return updated

    def _assign(self, uow: UnitOfWork, request: MeasurementRequest, operator: str) -> AcquisitionBinding:
        """The acquisition records which adapter, method and device it was assigned (CONTRACTS 5: an adapter is
        limited to its assigned acquisitions). No assignment source or no adapter: start fails closed."""
        port = self._prov.assignments
        assignment = (
            None
            if port is None
            else port.assign(uow, capability_code=request.capability_code, methods=request.methods)
        )
        if assignment is None or assignment.method not in request.methods:
            raise _not_allowed("adapter_unassigned")
        return AcquisitionBinding(
            request.request_id,
            request.generation + 1,
            operator,
            assignment.adapter_id,
            assignment.method,
            assignment.device_id,
            False,
        )

    def _require_consent(self, uow: UnitOfWork, request: MeasurementRequest, operator: str) -> None:
        if operator == request.patient_id:
            return
        if not consented(
            uow,
            self._prov.consent,
            subject=request.patient_id,
            recipient=operator,
            capability_code=request.capability_code,
        ):
            raise _problem(ErrorCode.CONSENT_REQUIRED)

    def _recheck(
        self, uow: UnitOfWork, request: MeasurementRequest, operator: str | None
    ) -> GapReason | None:
        """Gates again at submit, acceptance and start, against the current environment. The stored KitCheck is
        never recomputed. Unknown prerequisites refuse the action without inventing a cause."""
        facts = None if self._prov.mission is None else self._prov.mission.current(uow)
        gate = self._prov.kit_gate
        if facts is None or gate is None:
            raise _not_allowed("prerequisites_unknown")
        if facts.mission_id != request.mission_id or facts.mission_mode != request.origin_mode:
            raise _not_allowed("mission_partition_changed")
        outcome = gate.check(
            uow,
            capability_code=request.capability_code,
            environment_id=facts.active_environment_id,
            condition_ids=request.kit_check.condition_ids,
        )
        cause, _ = self._gate(uow, outcome.kit_check, request.capability_code, request.patient_id, operator)
        return cause

    def _become_unavailable(
        self, uow: UnitOfWork, request: MeasurementRequest, cause: GapReason
    ) -> MeasurementRequest:
        """Confirmed loss of capability, context or operator: unavailable with reason, gap where an episode exists.
        Returned as the action's 200 state, never as a fabricated successful transition."""
        updated = replace(
            request, revision=request.revision + 1, state="unavailable", unavailable_cause=cause
        )
        self._store.update_request(uow.connection, updated, expected_revision=request.revision)
        self._changed(uow, updated, request.state, "gate_" + cause, None, operation="version")
        if self._store.gap_for_request(uow.connection, request.request_id) is None:
            capability = self._caps.get(uow, request.capability_code)
            region = None if capability is None else capability.body_region
            facts = None if self._prov.mission is None else self._prov.mission.current(uow)
            env = request.kit_check.environment_id if facts is None else facts.active_environment_id
            self._open_gap(uow, updated, cause, region, env)
        return updated

    # ------------------------------------------------------------------------------------------------ attempts

    def record_attempt(
        self,
        session: SessionContext,
        identity: CommandIdentity,
        request_id: str,
        attempt: AttemptInput,
        *,
        correlation_id: str,
    ) -> CommandOutcome:
        if session.kind != "adapter" or session.actor.kind != "adapter":
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        with self._p.uows.begin(actor=session.actor, command=identity, correlation_id=correlation_id) as uow:
            replayed = self._replay(uow, session, identity)
            if replayed is not None:
                return replayed
            request = self._store.get_request(uow.connection, request_id)
            if request is None:
                raise _problem(ErrorCode.NOT_FOUND)
            binding = self._trusted_binding(uow, request, session.actor.id)
            capability = self._caps.get(uow, request.capability_code)
            panel = () if capability is None else capability.components
            quantities = self._validate_attempt(request, attempt, binding, panel)
            existing = next((a for a in request.attempts if a.sequence == attempt.sequence), None)
            if existing is not None:
                if existing.fingerprint == attempt.fingerprint and existing.adapter_id == session.actor.id:
                    self._record(uow, identity, 200, request, "attempt_duplicate")
                    return self._outcome(uow, session, request, 200, None)
                raise MeasurementError(
                    ErrorCode.INVALID_TRANSITION, field="/sequence", detail_code="sequence_reused"
                )
            updated = self._append(uow, request, attempt, quantities, session.actor.id)
            self._record(uow, identity, 200, updated, "attempt_" + attempt.outcome)
            return self._outcome(uow, session, updated, 200, None)

    def _trusted_binding(
        self, uow: UnitOfWork, request: MeasurementRequest, adapter_id: str
    ) -> AcquisitionBinding:
        """No acquisition before start (I2). Only the adapter assigned at start may write to that generation, while
        it runs and late (a late result is retained as quarantined evidence); every other adapter is refused."""
        if request.generation < 1:
            raise MeasurementError(ErrorCode.INVALID_TRANSITION)
        binding = self._store.acquisition(uow.connection, request.request_id, request.generation)
        if binding is None or binding.adapter_id != adapter_id:
            raise _problem(ErrorCode.ADAPTER_NOT_TRUSTED)
        return binding

    def _append(
        self,
        uow: UnitOfWork,
        request: MeasurementRequest,
        attempt: AttemptInput,
        quantities: tuple[Quantity, ...],
        adapter_id: str,
    ) -> MeasurementRequest:
        effect = attempt_effect(
            request,
            outcome=attempt.outcome,
            value_count=len(attempt.observations),
            max_attempts=self._limits.max_attempts,
            max_late=self._limits.max_late_attempts,
        )
        attempt_id = self._p.ids.new("att")
        observation_ids: tuple[str, ...] = ()
        held: tuple[HeldValue, ...] = ()
        if effect.disposition == "usable":
            observation_ids = self._append_observations(uow, request, attempt, quantities, attempt_id)
        else:
            held = tuple(
                HeldValue(
                    o.patient_id, o.code, q, o.observed_at, o.origin_mode, o.acquisition_method, o.device_id
                )
                for o, q in zip(attempt.observations, quantities, strict=True)
            )
        record = Attempt(
            attempt_id=attempt_id,
            sequence=attempt.sequence,
            adapter_id=adapter_id,
            generation=request.generation,
            started_at=attempt.started_at,
            ended_at=attempt.ended_at,
            outcome=attempt.outcome,
            failure_reason=attempt.failure_reason,
            observation_ids=observation_ids,
            quality_notes=attempt.quality_notes,
            disposition=effect.disposition,
            held_values=held,
            fingerprint=attempt.fingerprint,
        )
        updated = append_attempt(request, record, effect)
        self._store.insert_attempt(
            uow.connection, request.request_id, record, recorded_at=uow.now, request_revision=updated.revision
        )
        self._store.update_request(uow.connection, updated, expected_revision=request.revision)
        if updated.state != request.state and updated.state in ("completed", "failed"):
            self._store.fence_acquisition(uow.connection, request.request_id, request.generation, at=uow.now)
        self._changed(
            uow, updated, request.state, "attempt_" + effect.disposition, attempt_id, operation="append"
        )
        return updated

    def _append_observations(
        self,
        uow: UnitOfWork,
        request: MeasurementRequest,
        attempt: AttemptInput,
        quantities: tuple[Quantity, ...],
        attempt_id: str,
    ) -> tuple[str, ...]:
        records = self._prov.observations
        if records is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "records_unavailable", retryable=True)
        ids: list[str] = []
        for observation, quantity in zip(attempt.observations, quantities, strict=True):
            draft = ObservationDraft(
                patient_id=observation.patient_id,
                episode_id=request.episode_id,
                code=observation.code,
                quantity=quantity,
                observed_at=observation.observed_at,
                origin_mode=observation.origin_mode,
                acquisition_method=observation.acquisition_method,
                device_id=observation.device_id,
                context=dict(observation.context),
                body_region=observation.body_region,
                supersedes=observation.supersedes,
                measurement_request_id=request.request_id,
                attempt_id=attempt_id,
                operator_id=request.operator_id,
            )
            ids.append(records.append_for_attempt(uow, draft))
        return tuple(ids)

    def _validate_attempt(
        self,
        request: MeasurementRequest,
        attempt: AttemptInput,
        binding: AcquisitionBinding,
        panel: tuple[str, ...] = (),
    ) -> tuple[Quantity, ...]:
        """All-or-none boundary validation before any write (E05 4, 5)."""
        lim = self._limits
        if attempt.ended_at < attempt.started_at:
            raise InvalidInput("/ended_at", "negative_duration")
        if attempt.outcome == "success" and attempt.failure_reason is not None:
            raise InvalidInput("/failure_reason", "success_has_no_failure_reason")
        if attempt.outcome != "success" and attempt.failure_reason is None:
            raise InvalidInput("/failure_reason", "failure_reason_required")
        if attempt.failure_reason is not None:
            self._text(attempt.failure_reason, "/failure_reason")
        if len(attempt.observations) > lim.max_observations:
            raise InvalidInput("/observations", "measurement.max_observations")
        if len(attempt.quality_notes) > lim.max_notes:
            raise InvalidInput("/quality_notes", "measurement.max_notes")
        for index, note in enumerate(attempt.quality_notes):
            self._text(note, f"/quality_notes/{index}")
        quantities = tuple(
            self._validate_value(request, binding, o, f"/observations/{i}", panel)
            for i, o in enumerate(attempt.observations)
        )
        # A panel capability (config-kit-map `components`) is recorded all or none: a successful attempt carries
        # each component exactly once, never half a panel.
        if (
            panel
            and attempt.outcome == "success"
            and sorted(o.code for o in attempt.observations) != sorted(panel)
        ):
            raise IdentityMismatch("/observations", "panel_incomplete")
        return quantities

    def _validate_value(
        self,
        request: MeasurementRequest,
        binding: AcquisitionBinding,
        o: ObservationInput,
        at: str,
        panel: tuple[str, ...] = (),
    ) -> Quantity:
        if o.patient_id != request.patient_id:
            raise IdentityMismatch(at + "/patient_id", "patient_mismatch")
        if o.episode_id is not None and o.episode_id != request.episode_id:
            raise IdentityMismatch(at + "/episode_id", "episode_mismatch")
        if o.measurement_request_id is not None and o.measurement_request_id != request.request_id:
            raise IdentityMismatch(at + "/measurement_request_id", "request_mismatch")
        if o.origin_mode != request.origin_mode:
            raise IdentityMismatch(at + "/origin_mode", ErrorCode.ORIGIN_MISMATCH.value)
        if o.code not in (panel or (request.capability_code,)):
            raise IdentityMismatch(at + "/code", "code_not_requested")
        if o.acquisition_method not in request.methods or o.acquisition_method != binding.method:
            raise IdentityMismatch(at + "/acquisition_method", "method_not_assigned")
        if binding.device_id is not None and o.device_id != binding.device_id:
            raise IdentityMismatch(at + "/device_id", "device_not_assigned")
        if len(o.context) > self._limits.max_context_entries:
            raise InvalidInput(at + "/context", "measurement.max_context_entries")
        for key, value in o.context:
            self._text(key, at + "/context")
            self._text(value, at + "/context/" + key)
        try:
            unit = self._prov.units.parse(o.unit)
        except ProblemError as error:
            raise MeasurementError(
                ErrorCode.INVALID_UNIT, field=at + "/unit", detail_code="not_ucum"
            ) from error
        return Quantity(o.value, unit)

    def _text(self, value: str, field: str) -> None:
        if not value or len(value.encode("utf-8")) > self._limits.max_text_bytes:
            raise InvalidInput(field, "measurement.max_text_bytes")

    # ------------------------------------------------------------------------------------------------ gaps

    def record(self, uow: UnitOfWork, episode_id: str, capability_or_fact: str, consequence: str) -> str:
        """GapProposalPort: controller gap in the caller's apply unit of work. Validates the episode scope, the
        code and the actual lack in the current kit; never invents a gap for an available capability."""
        try:
            return self._record_gap(uow, episode_id, capability_or_fact, consequence)
        except DomainRefusal as error:
            raise problem_of(error) from error

    def _record_gap(self, uow: UnitOfWork, episode_id: str, capability_or_fact: str, consequence: str) -> str:
        episodes = self._prov.episodes
        fact = None if episodes is None else episodes.get(uow, episode_id)
        if episodes is None or fact is None or not episodes.visible_to(uow, episode_id, uow.actor):
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        self._text(consequence, "/consequence")
        mission = self._mission(uow)
        if self._prov.kit_gate is None:
            raise _problem(ErrorCode.CAPABILITY_UNAVAILABLE)
        outcome = self._prov.kit_gate.check(
            uow,
            capability_code=capability_or_fact,
            environment_id=mission.active_environment_id,
            condition_ids=(),
        )
        if outcome.kit_check.can:
            raise InvalidValue("/capability_code", "capability_available")
        existing = self._store.open_gap(uow.connection, episode_id, capability_or_fact)
        if existing is not None:
            return existing.gap_id
        capability = self._caps.get(uow, capability_or_fact)
        plan = None
        if self._prov.gap_plans is not None:
            plan = self._prov.gap_plans.plan_for(
                condition_ids=(),
                environment_id=mission.active_environment_id,
                subject_person_id=fact.patient_id,
            )
        gap = CapabilityGap(
            gap_id=self._p.ids.new("gap"),
            episode_id=episode_id,
            capability_code=capability_or_fact,
            reason="no_capability",
            consequence=consequence,
            alternatives=(),
            body_region=None if capability is None else capability.body_region,
            state="open",
            plan_id=None if plan is None else plan.plan_id,
            check_another_way_step_ids=() if plan is None else plan.check_another_way_step_ids,
            source_request_id=None,
            release_id=uow.release.release_id,
            created_at=uow.now,
        )
        self._store.insert_gap(uow.connection, gap)
        self._audit(
            uow, "gap_opened", {"gap_id": gap.gap_id, "episode_id": episode_id}, {"reason": gap.reason}
        )
        return gap.gap_id

    def allows(self, view: View, *, request_id: str, operator_id: str, patient_id: str) -> bool:
        """MeasurementAssignmentPort: the operator is bound to an accepted or running request for this subject."""
        request = self._store.get_request(view.connection, request_id)
        return (
            request is not None
            and request.patient_id == patient_id
            and request.operator_id == operator_id
            and request.state in ("accepted", "in_progress")
        )

    # ------------------------------------------------------------------------------------------------ shared

    def _replay(
        self, uow: UnitOfWork, session: SessionContext, identity: CommandIdentity
    ) -> CommandOutcome | None:
        """Exact replay never re-executes. Visibility is rechecked under the current grant; within the replay
        window the original status and the request as it stood at the original revision are returned, after it
        only the closed accepted_command receipt (D6). A mismatched identity raises DUPLICATE_COMMAND."""
        stored = self._p.ledger.lookup(uow, identity)
        if stored is None:
            return None
        request = (
            None
            if stored.resource_ref is None
            else self._store.get_request(uow.connection, stored.resource_ref)
        )
        revision = _stored_revision(stored.safe_body)
        if request is None or revision is None:
            raise _problem(ErrorCode.INTERNAL_ERROR)
        if session.kind != "adapter" and not can_read(uow, self._prov.consent, request, session):
            raise _problem(ErrorCode.UNAUTHORIZED_SCOPE)
        age = uow.now - stored.receipt.accepted_at
        if age > timedelta(milliseconds=self._limits.replay_window_ms):
            receipt = stored.receipt
            raise ProblemError(
                ErrorCode.DUPLICATE_COMMAND,
                ErrorCode.DUPLICATE_COMMAND.value,
                current_state={
                    "kind": "accepted_command",
                    "command_key": receipt.command_key,
                    "operation_id": receipt.operation_id,
                    "target": receipt.target,
                    "receipt_id": receipt.receipt_id,
                    "accepted_at": format_instant(receipt.accepted_at),
                },
            )
        original = self._store.get_request_at(uow.connection, request.request_id, revision)
        if original is None:
            raise _problem(ErrorCode.INTERNAL_ERROR)
        return self._outcome(uow, session, original, stored.status, _REFUSALS.get(stored.result_code))

    def _record(
        self, uow: UnitOfWork, identity: CommandIdentity, status: int, request: MeasurementRequest, code: str
    ) -> None:
        """The ledger keeps the status and the revision the response showed, never request text: a replay
        rebuilds that revision from E05's own history (SYSTEM 5.8)."""
        receipt = self._p.receipts.accepted(uow, identity)
        body = canonical_json({"revision": request.revision})
        self._p.ledger.record(uow, identity, StoredResult(receipt, status, request.request_id, code, body))

    def _outcome(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        request: MeasurementRequest,
        status: int,
        refusal: Refusal | None,
    ) -> CommandOutcome:
        viewer = None if session.kind == "adapter" else viewer_of(session)
        return CommandOutcome(
            status,
            request_wire(request, viewer),
            request.request_id,
            request.revision,
            uow.release.release_id,
            refusal,
        )

    def _changed(
        self,
        uow: UnitOfWork,
        request: MeasurementRequest,
        before: RequestState | None,
        cause: str,
        attempt_id: str | None,
        *,
        operation: Literal["create", "version", "append"],
    ) -> None:
        """Transition row, audit, public event and replication declaration: always together, in this UOW."""
        self._store.insert_transition(
            uow.connection,
            Transition(
                request.request_id,
                request.revision,
                uow.actor,
                before,
                request.state,
                cause,
                attempt_id,
                uow.now,
            ),
            request,
        )
        refs = {"request_id": request.request_id}
        if attempt_id is not None:
            refs["attempt_id"] = attempt_id
        self._audit(uow, "request_" + cause, refs, {"state": request.state, "revision": request.revision})
        audience = Audience(AUDIENCE_RULE, request.request_id, PROJECTION, POLICY_VERSION)
        uow.emit(
            OutboxEvent(
                type=EventType.MEASUREMENT_REQUEST_CHANGED,
                occurred_at=uow.now,
                scope=EventScope("person", request.patient_id, None),
                config_release_id=request.release_id,
                correlation_id=uow.correlation_id,
                data_json=canonical_json(request_wire(request, None)),
                audience=audience,
            )
        )
        uow.replicate(
            ReplicationChange(
                export_projection=None,
                record_type="measurement_request",
                record_id=request.request_id,
                operation=operation,
                payload_json=canonical_json(
                    {"request_id": request.request_id, "revision": request.revision, "state": request.state}
                ),
                causal_refs=(),
                release_id=request.release_id,
                audience=audience,
            )
        )

    def _audit(
        self, uow: UnitOfWork, event: str, refs: dict[str, str], meta: dict[str, str | int | bool]
    ) -> None:
        uow.audit(
            AuditEntry(
                event_id=self._p.ids.new("mev"),
                event_type="measurement." + event,
                event_version=1,
                actor=uow.actor,
                refs=refs,
                correlation_id=uow.correlation_id,
                causation_id=None,
                safe_metadata=meta,
            )
        )


__all__ = [
    "AttemptInput",
    "CommandOutcome",
    "Limits",
    "MeasurementService",
    "ObservationInput",
    "Platform",
    "Providers",
]
