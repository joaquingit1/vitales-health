"""Ports E05 consumes and provides (SYSTEM 4.2 row `measurement`).

Providers on main export the exact classes used by the registry. Tests use small boundary fakes. Ports marked
interim have no published provider declaration yet: they are the least surprising shape the pending request allows
and are listed as gaps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.kitmap.application.ports import CapabilityWords as CapabilityWords
from crew_health.kitmap.application.ports import GateOutcome as GateOutcome
from crew_health.kitmap.application.ports import GateRow as GateRow
from crew_health.kitmap.application.ports import KitCapabilitiesPort as KitCapabilitiesPort
from crew_health.kitmap.application.ports import KitCapability as KitCapability
from crew_health.kitmap.application.ports import KitCheckFacts as KitCheckFacts
from crew_health.kitmap.application.ports import KitGate as KitGate

# E01 records' classes (SYSTEM 4); measurement's draft is records' AttemptObservationDraft (R-E01-17 pending).
from crew_health.records.application.ports import AttemptObservationDraft as ObservationDraft
from crew_health.records.application.ports import ObservationCommands as ObservationCommands

# The consumer-facing value vocabularies, identical to measurement.domain.model's aliases. Consumers import this file
# only (SYSTEM 6.4), so it cannot reach the domain package; hub/tests/measurement pins the two definitions equal.
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type OperatorRequirement = Literal["self", "crewmate", "crew_medical_officer"]
type GapReason = Literal["no_capability", "device_absent", "operator_absent", "context_inapplicable"]
type CapabilityStatus = Literal["available", "unavailable", "not_applicable"]
type UnavailableReason = Literal[
    "not_in_kit", "device_absent", "operator_absent", "context_inapplicable", "adapter_down"
]

type View = ReadSnapshot | UnitOfWork

# ---------------------------------------------------------------------------------------------------------------
# Consumed
# ---------------------------------------------------------------------------------------------------------------


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
    "CapabilityWords",
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
    "KitCheckFacts",
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
