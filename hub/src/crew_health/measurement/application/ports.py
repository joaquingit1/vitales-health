"""Ports E05 consumes (structural copies) and provides (SYSTEM 4.2 row `measurement`).

Consumed ports are copied from the provider's design with their exact names and parameters. Value types the
provider owns are mirrored as frozen dataclasses with the provider's field names. Composition binds the real
providers; tests use small fakes. Ports marked interim have no published provider declaration yet: they are the
least surprising shape the pending request allows and are listed as gaps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.measurement.domain.model import (
    CapabilityStatus,
    GapReason,
    KitCheck,
    OperatorRequirement,
    OriginMode,
    UnavailableReason,
)

type View = ReadSnapshot | UnitOfWork

# ---------------------------------------------------------------------------------------------------------------
# Consumed
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateRow:
    condition_id: str
    plan_id: str | None


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """E27 4.2: kit_check (contracts.KitCheck), capability words, rows (condition_id, plan_id) in request order."""

    kit_check: KitCheck
    capability_words: tuple[str, ...]
    rows: tuple[GateRow, ...]


# Provided by E27 (crew_health.kitmap); structural copy until it is on main. SYSTEM 4.4 name `KitGate` prevails
# over E27's `KitGatePort`. Raises a ProblemError with INVALID_VALUE (E27 KitGateInvalid) for an unknown
# capability or condition ID.
class KitGate(Protocol):
    def check(
        self, uow: UnitOfWork, *, capability_code: str, environment_id: str, condition_ids: Sequence[str]
    ) -> GateOutcome: ...


@dataclass(frozen=True, slots=True)
class KitCapability:
    """E27 3.1: E27's half of contracts.Capability; E05 adds the device overlay."""

    code: str
    label: str
    body_region: str | None
    operator_requirement: OperatorRequirement
    expected_duration_seconds: int | None
    methods: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    applicable_environments: tuple[str, ...]
    kit_status: Literal["available", "unavailable"]
    kit_reason: Literal["not_in_kit"] | None
    components: tuple[str, ...] = ()  # a panel's indicator codes (config-kit-map), empty for one value


# Provided by E27 (crew_health.kitmap.application.ports.KitCapabilitiesPort). A structural copy: the provider's
# KitCapability lives in kitmap.domain, which this module may not import.
class KitCapabilitiesPort(Protocol):
    def capabilities(self, snapshot: ReadSnapshot, environment_id: str) -> tuple[KitCapability, ...]: ...


@dataclass(frozen=True, slots=True)
class GateResult:
    """E11 3.7 result: first failing check wins. Field names mirror E11's reason column (no published DTO)."""

    passed: bool
    reason: (
        Literal[
            "no_capability", "context_inapplicable", "device_absent", "operator_absent", "consent_missing"
        ]
        | None
    )


# Provided by E11 (crew_health.pathways); structural copy until it is on main.
class RequestGatePort(Protocol):
    def check(
        self, uow: UnitOfWork, capability_code: str, subject_id: str, operator_id: str | None
    ) -> GateResult: ...


# Provided by E02 (crew_health.consent); structural copy until it is on main.
class ConsentQueries(Protocol):
    def allows(
        self, view: View, *, subject_person_id: str, recipient: str, scope: tuple[str, str]
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
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


# Provided by E15 (crew_health.episodes); structural copy until it is on main.
class EpisodeFacts(Protocol):
    def get(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None: ...
    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool: ...


@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


# Provided by E23 (crew_health.mission); structural copy until it is on main. Always caller bound here.
class MissionStatePort(Protocol):
    def current(self, uow: View | None = None) -> MissionFacts | None: ...


@dataclass(frozen=True, slots=True)
class GapPlan:
    plan_id: str
    check_another_way_step_ids: tuple[str, ...]


# Provided by E28 (crew_health.plans); structural copy until it is on main.
class GapPlanPort(Protocol):
    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None: ...


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """A validated attempt value handed to E01 records in the attempt's unit of work."""

    patient_id: str
    episode_id: str | None
    code: str
    quantity: Quantity
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: str
    device_id: str | None
    context: Mapping[str, str]
    body_region: str | None
    supersedes: str | None
    measurement_request_id: str
    attempt_id: str
    operator_id: str | None


# Provided by E01 (crew_health.records) as ObservationCommands; structural copy until it is on main. Interim:
# E01 has not published the method (R-E01-17, R-E05-2). It appends inside the caller's unit of work, runs E01's
# acceptance pipeline and returns the new Observation ID; a refusal raises ProblemError and rolls back the attempt.
class ObservationCommands(Protocol):
    def append_for_attempt(self, uow: UnitOfWork, draft: ObservationDraft) -> str: ...


# Interim, R-E05-1 (InstrumentReadinessPort inputs): acquisition methods whose adapter process reports ready now
# (E06 camera worker, E07/E08 adapters). None means unknown, which never counts as ready.
class AdapterReadinessPort(Protocol):
    def ready_methods(self, view: View) -> frozenset[str] | None: ...


@dataclass(frozen=True, slots=True)
class AdapterAssignment:
    adapter_id: str
    method: str
    device_id: str | None


# Interim, R-E05-2 (AcquisitionBinding source): the authenticated adapter process, method and device that will run
# an acquisition for one of these methods, chosen at start. None means no assignment: start fails closed.
class AdapterAssignmentPort(Protocol):
    def assign(
        self, view: View, *, capability_code: str, methods: tuple[str, ...]
    ) -> AdapterAssignment | None: ...


# Interim, R-E02-9: E02 `require_roles(operation_id)` is a FastAPI dependency. The router calls this structural
# equivalent so the dependency can be bound late. None means not signed in (401); a wrong role raises 403.
class SessionAuthenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None: ...


# Interim, SYSTEM 5.8: the E01 command boundary fingerprints private bodies with a per install keyed HMAC. Until
# E01's platform middleware owns identity construction, the router asks this port for the keyed digest.
class CommandFingerprint(Protocol):
    def fingerprint(self, canonical_body: bytes) -> str: ...


# ---------------------------------------------------------------------------------------------------------------
# Provided
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityView:
    """contracts.Capability: E27's packed half plus E05's readiness overlay."""

    code: str
    label: str
    methods: tuple[str, ...]
    device_ids: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    operator_requirement: OperatorRequirement
    applicable_environments: tuple[str, ...]
    status: CapabilityStatus
    unavailable_reason: UnavailableReason | None
    expected_duration_seconds: int | None
    body_region: str | None
    components: tuple[str, ...] = ()  # internal: a panel's indicator codes, not on the Capability wire object


class CapabilityQueries(Protocol):
    """Registry view only; never calls RequestGatePort (no recursion with E11)."""

    def get(self, uow: View, code: str) -> CapabilityView | None: ...
    def list(self, uow: View) -> tuple[CapabilityView, ...]: ...


@dataclass(frozen=True, slots=True)
class ProposalInput:
    """A validated create body. Subject is never on the body: a visible episode binds it, else the session."""

    episode_id: str | None
    capability_code: str
    reason: str
    decision_affected: str
    operator_id: str | None
    body_region: str | None
    body_region_given: bool
    condition_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposalResult:
    request_id: str
    state: str
    refusal: Literal["KIT_CANNOT_PERFORM", "CAPABILITY_UNAVAILABLE", "CONTEXT_INAPPLICABLE"] | None
    gap_id: str | None


class MeasurementCommands(Protocol):
    """Commit-only proposal in the caller's unit of work (controller path, R-E05-1 MeasurementProposalPort).
    The requester is the controller's actor; subject comes from the episode, never from model text."""

    def propose(
        self, uow: UnitOfWork, *, requester: Actor, subject_person_id: str, proposal: ProposalInput
    ) -> ProposalResult: ...


class GapProposalPort(Protocol):
    def record(self, uow: UnitOfWork, episode_id: str, capability_or_fact: str, consequence: str) -> str: ...


# E02 2 names this E05 port: `MeasurementAssignmentPort.allows(view, *, request_id, operator_id, patient_id)`.
class MeasurementAssignmentPort(Protocol):
    def allows(self, view: View, *, request_id: str, operator_id: str, patient_id: str) -> bool: ...


__all__ = [
    "AdapterAssignment",
    "AdapterAssignmentPort",
    "AdapterReadinessPort",
    "CapabilityQueries",
    "CapabilityView",
    "ConsentQueries",
    "EpisodeFact",
    "EpisodeFacts",
    "GapPlan",
    "GapPlanPort",
    "GapProposalPort",
    "GapReason",
    "GateOutcome",
    "GateResult",
    "GateRow",
    "KitCapabilitiesPort",
    "KitCapability",
    "KitGate",
    "MeasurementAssignmentPort",
    "MeasurementCommands",
    "MissionFacts",
    "MissionStatePort",
    "ObservationCommands",
    "ObservationDraft",
    "ProposalInput",
    "ProposalResult",
    "RequestGatePort",
    "SessionAuthenticator",
    "CommandFingerprint",
    "View",
]
