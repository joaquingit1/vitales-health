"""Ports of the roles module (SYSTEM 4.2 row `roles`, 8.2 and 8.4).

Consumed ports are structural copies of the providers' protocols until those modules are on main; composition binds
the real providers later and tests use small fakes. Provided ports are the ones other modules import from here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.help.application.ports import HelpSessionReader as HelpSessionReader
from crew_health.help.application.ports import HelpSessionSnapshot as HelpSessionSnapshot
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum


# Provided by E02 (crew_health.identity); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class RosterMember:
    person_id: str
    display_name: str
    roster_index: int
    medical_officer_rank: int | None


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RosterPort(Protocol):
    def crew(self, at: datetime) -> Sequence[RosterMember]: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
# planned_end is request Q9 (E31 design 14): not yet on E23's OverlayFact; an adapter maps it as None until then.
@dataclass(frozen=True, slots=True)
class OverlayFact:
    overlay_id: str
    person_id: str
    kind: str
    state: str
    planned_start: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    planned_end: datetime | None = None


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class OverlayPort(Protocol):
    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class LocationPort(Protocol):
    def location_of(self, person_id: str, at: datetime) -> str | None: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionStatePort(Protocol):
    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None: ...
    def day_end(self, instant: datetime) -> datetime: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class DayLabelPort(Protocol):
    def inline_label_at(self, instant: datetime) -> str: ...


# Provided by E28 (crew_health.plans); structural copy until it is on main (E28 4.2 PlanRunQueryPort.state).
@dataclass(frozen=True, slots=True)
class PlanRunSnapshot:
    plan_run_id: str
    subject_person_id: str
    state: Literal["open", "closed"]
    open_skill_steps: tuple[tuple[str, str], ...]  # (step_id, skill_id)
    role_rules_version: str
    skills_catalog_version: str


# Provided by E28 (crew_health.plans); structural copy until it is on main. SYSTEM 4.2 calls it PlanRunReader.
class PlanRunReader(Protocol):
    def state(self, plan_run_id: str) -> PlanRunSnapshot | None: ...


# Provided by E32 (crew_health.supplies); structural copy until it is on main (E31 design 4.2).
class SupplyAboardPort(Protocol):
    def aboard(self, supply_ids: Collection[str], environment_id: str) -> Mapping[str, bool]: ...


# ---------------------------------------------------------------------------------------------- provided by E31


@dataclass(frozen=True, slots=True)
class DoseAuthorityContext:
    proposal_id: str
    proposal_revision: int
    medicine_id: str
    lot_id: str
    recommended_dose: Quantity
    label_dose: Quantity
    compensated: bool
    engine_context_fingerprint: str
    plan_run_id: str | None
    plan_step_id: str | None
    condition_id: str | None
    pinned_release_id: str


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    kind: Literal["crew_medical_officer", "acting_medical_officer", "none"]  # internal enum
    role_assignment_id: str | None
    reason: str


class ClinicalAuthorityQuery(Protocol):
    """SYSTEM 8.4; also answers MedicalOfficerQuery.acting (SYSTEM 4.4)."""

    def authority(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        subject_person_id: str,
        action: Literal[
            "urgency_disposition", "dose_confirmation", "record_review", "clinician_assessed"
        ],  # internal enum
        dose: DoseAuthorityContext | None,
        at: datetime,
    ) -> AuthorityDecision: ...


@dataclass(frozen=True, slots=True)
class RoleAssignmentRef:
    role_assignment_id: str
    help_session_id: str | None
    plan_run_id: str | None
    role_id: str
    person_id: str
    state: Literal["proposed", "accepted", "changed"]  # internal enum
    acting_medical_officer: bool


class RoleQueryPort(Protocol):
    def plan_run_person_ids(self, plan_run_id: str) -> frozenset[str]: ...
    def medical_officer_assignments(self, subject_person_id: str) -> Sequence[RoleAssignmentRef]: ...


class SkillHolderPort(Protocol):
    """E28 4.1: holders (fresh or due) of one skill, in roster order, mission practice mode only."""

    def holders(self, skill_id: str, at: datetime) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class DrillRef:
    drill_id: str
    scenario_id: str
    skill_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    origin_mode: Literal["LIVE", "SIM"]  # internal enum


class DrillReader(Protocol):
    """E30 X12: None unless the drill is running."""

    def running_drill(self, drill_id: str) -> DrillRef | None: ...


@dataclass(frozen=True, slots=True)
class AssignmentRecord:
    """E30 X12 reads these fields by name (E30 design lines 64, 493, 1192)."""

    role_assignment_id: str
    person_id: str | None
    state: Literal["proposed", "accepted", "changed"]  # internal enum
    skill_id: str | None
    plan_run_id: str | None
    help_session_id: str | None
    retired: bool


class RoleAssignmentReader(Protocol):
    """E30 X12: one assignment's current state; None when no such assignment."""

    def get(self, role_assignment_id: str) -> AssignmentRecord | None: ...
