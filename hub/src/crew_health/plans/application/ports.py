"""Public plan facts and consumer-owned copies of optional provider ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.guidance.application.ports import GuidanceDecision as GuidanceDecision
from crew_health.guidance.application.ports import GuidanceEligibility as GuidanceEligibility
from crew_health.guidance.application.ports import GuidanceRequest as GuidanceRequest
from crew_health.guidance.application.ports import SkillFacts as SkillFacts
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import AcceptedReceipt, CommandIdentity
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork

# Kernel-only composition seams used by their owner-side adapters.
from crew_health.plans.application.kitmap_copies import KitMapContext as KitMapContext
from crew_health.plans.application.kitmap_copies import PlanChange as PlanChange
from crew_health.plans.application.kitmap_copies import PlanColumns as PlanColumns
from crew_health.plans.application.kitmap_copies import PlanColumnsPort as PlanColumnsPort
from crew_health.plans.application.kitmap_copies import PlanningDelta as PlanningDelta
from crew_health.plans.application.kitmap_copies import SkillAddition as SkillAddition
from crew_health.plans.application.kitmap_copies import SupplyAddition as SupplyAddition
from crew_health.plans.application.measurement_copies import GapPlan as GapPlan
from crew_health.plans.application.measurement_copies import GapPlanPort as GapPlanPort
from crew_health.plans.application.teach_copies import GuidedCheckCompleted as GuidedCheckCompleted
from crew_health.plans.application.teach_copies import GuidedCheckFacts as GuidedCheckFacts
from crew_health.plans.application.teach_copies import GuidedCheckQueries as GuidedCheckQueries
from crew_health.plans.application.teach_copies import GuidedCompletionSink as GuidedCompletionSink
from crew_health.plans.application.teach_copies import GuidedStepRef as GuidedStepRef
from crew_health.plans.application.teach_copies import PlanRunReader as PlanRunReader


@dataclass(frozen=True, slots=True)
class SupplyNeed:
    plan_id: str
    step_id: str
    supply_id: str
    per_use: Quantity | None


@dataclass(frozen=True, slots=True)
class RunContext:
    visible: bool
    plan_id: str
    environment_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    pinned_needs: Mapping[str, tuple[SupplyNeed, ...]]


class PlanRunContextReader(Protocol):
    def context(self, session: SessionContext, plan_run_id: str) -> RunContext | None: ...


# Provided by E32 (crew_health.supplies); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class StockLevel:
    supply_id: str
    aboard: bool
    quantity_now: Quantity | None
    unit: Unit


# Provided by E32 (crew_health.supplies); structural copy until it is on main.
class StockReader(Protocol):
    def stock(self, environment_id: str) -> Mapping[str, StockLevel]: ...


# Provided by E32 (crew_health.supplies); structural copy until it is on main.
class SupplyCoverPort(Protocol):
    def uses_after_one(
        self, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
    ) -> Mapping[tuple[str, str], int | None]: ...


# Provided by E32 (crew_health.supplies); structural copy until it is on main.
class SupplyLedgerPort(Protocol):
    def record_step_uses(
        self,
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]: ...


# Provided by E26 (crew_health.help); structural copy until it is on main. SYSTEM 8.1.
@dataclass(frozen=True, slots=True)
class PlanEscalation:
    escalation_id: str
    plan_run_id: str
    step_id: str
    occurrence: int
    expected_run_revision: int
    subject_person_id: str
    flow_id: str | None
    pinned_release_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    client_time: TimeInput
    session: SessionContext
    parent_command: CommandIdentity
    help_command: CommandIdentity


@dataclass(frozen=True, slots=True)
class HelpEscalationReceipt:
    escalation_id: str
    help_session_id: str
    help_revision: int
    pinned_release_id: str
    help_command_receipt: AcceptedReceipt


class PlanEscalationReader(Protocol):
    def read(self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str) -> PlanEscalation: ...


class PlanHelpEntry(Protocol):
    def open_from_plan_step(self, request: PlanEscalation) -> HelpEscalationReceipt: ...
    def receipt(
        self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str
    ) -> HelpEscalationReceipt | None: ...


# Provided by E15 (crew_health.episodes); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class EpisodeRef:
    episode_id: str
    subject_person_id: str


class EpisodeReader(Protocol):
    def visible(self, session: SessionContext, episode_id: str) -> EpisodeRef | None: ...


# Provided by E01 (crew_health.records); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class ObservationRef:
    observation_id: str
    patient_id: str
    episode_id: str | None
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    quality_status: str


class ObservationRefReader(Protocol):
    def get_many(self, observation_ids: Sequence[str]) -> Mapping[str, ObservationRef]: ...


@dataclass(frozen=True, slots=True)
class EscalationIntent:
    escalation_id: str
    plan_run_id: str
    step_id: str
    occurrence: int
    expected_run_revision: int
    subject_person_id: str
    flow_id: str | None
    pinned_release_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    client_time: str
    session_json: str
    parent_key: str
    parent_actor: str
    state: Literal["pending", "recorded"]
    help_session_id: str | None
