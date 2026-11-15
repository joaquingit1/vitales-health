"""Ports of the teach module (SYSTEM 4.2 row "Teach and check"; E30 4.1).

Provided here: `GuidedCheckQueries` and the domain event `GuidedCheckCompleted` (SYSTEM 8.1).
Consumed: `ObservationCommands` (E01 records), `PlanRunReader` (E28), `HelpSessionReader` (E26),
`GuidanceEligibility` (E27, SYSTEM 8.2), the camera reference over a count window through E05's measurement
requests and attempts, and E02's person directory, record routing and role gate. Every consumed protocol below is a
structural copy; composition binds the real provider when it lands on main.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from crew_health.guidance.application.ports import ContentRef as ContentRef
from crew_health.guidance.application.ports import GuidanceDecision as GuidanceDecision
from crew_health.guidance.application.ports import GuidanceEligibility as GuidanceEligibility
from crew_health.guidance.application.ports import GuidanceRequest as GuidanceRequest
from crew_health.guidance.application.ports import Requirements as Requirements
from crew_health.help.application.ports import HelpContext as HelpContext
from crew_health.help.application.ports import HelpSessionReader as HelpSessionReader
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.application.ports import AnswerCommands as AnswerCommands
from crew_health.records.application.ports import AnswerDraft as AnswerDraft
from crew_health.records.application.ports import ObservationCommands as ObservationCommands
from crew_health.records.application.ports import ObservationDraft as ObservationDraft
from crew_health.roles.application.ports import RoleAssignmentReader as RoleAssignmentReader

if TYPE_CHECKING:
    from crew_health.teach.domain.guided_check import GuidedCheck

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum


# Provided by E30 ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuidedCheckCompleted:
    """SYSTEM 8.1: published inside E30's finish transaction for every result; E28 and E31 handle it there.
    observation_ids is empty when the count went to another record (LIVE presenter on a SIM persona, 3.6)."""

    event_type: ClassVar[str] = "teach.guided_check_completed"
    guided_check_id: str
    plan_run_id: str | None
    step_id: str | None
    outcome: Literal["agree", "disagree", "no_reference"]  # internal enum
    observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuidedCheckFacts:
    guided_check_id: str
    skill_id: str
    plan_run_id: str | None
    subject_person_id: str
    helper_person_id: str
    measured_record_id: str
    state: str
    result: str | None
    origin_mode: OriginMode
    observation_ids: tuple[str, ...]


class GuidedCheckQueries(Protocol):
    """E28 reads the check in the same unit of work as GuidedCheckReader.get (E28 R28-22)."""

    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheckFacts | None: ...


# Records (E01): the provider's classes (SYSTEM 4: records provides ObservationCommands and AnswerCommands).

# Plans (E28) --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuidedStepRef:
    plan_run_id: str
    subject_person_id: str
    episode_id: str | None
    step_id: str
    environment_id: str
    skills_catalog_version: str
    ui_copy_version: str
    run_open: bool
    step_runnable: bool


# Provided by E28 (crew_health.plans); structural copy until it is on main (E28 4.2 PlanRunReaderService).
class PlanRunReader(Protocol):
    def guided_step(self, plan_run_id: str, skill_id: str) -> GuidedStepRef | None: ...


# Roles (E31) --------------------------------------------------------------------------------------------------------


# Camera reference over the count window (E05 measurement requests and attempts) ----------------------------------


@dataclass(frozen=True, slots=True)
class ReferenceWindowRequest:
    guided_check_id: str
    attempt_no: int
    record_id: str
    capability_code: str
    window_start: datetime
    window_end: datetime
    required_origin: OriginMode
    persist: bool
    ticket_ttl: timedelta


@dataclass(frozen=True, slots=True)
class ReferenceTicket:
    ticket_id: str | None
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class ReferenceOutcome:
    kind: Literal["pending", "reading", "no_reading", "unavailable"]  # internal enum
    observation_id: str | None
    value: Quantity | None
    reason: str | None
    origin_mode: OriginMode | None
    clock_uncertainty_ms: int | None
    cameras: int  # informative only; correlated cameras are one source (OD2)


# Provided by E05 (crew_health.measurement) over E06's camera; structural copy until it is on main. Method names
# are E30 4.1's CameraReferencePort: E05 R-E05-2 has not yet published the measurement attempt methods.
class CameraReferencePort(Protocol):
    def begin_window(self, request: ReferenceWindowRequest, *, timeout: timedelta) -> ReferenceTicket: ...
    def outcome(self, ticket_id: str, *, wait: timedelta) -> ReferenceOutcome: ...
    def abandon(self, ticket_id: str, *, timeout: timedelta) -> None: ...


# Identity (E02) -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersonRef:
    person_id: str
    display_name: str
    on_roster: bool


# Provided by E02 (crew_health.identity); structural copy until it is on main (E30 4.1 PersonDirectory).
class PersonDirectory(Protocol):
    def person(self, person_id: str) -> PersonRef | None: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main (E30 4.1 RecordRouting, X10).
class RecordRouting(Protocol):
    def record_origin(self, person_id: str) -> OriginMode: ...
    def record_for(
        self, person_id: str, origin: OriginMode
    ) -> str: ...  # raises a ProblemError when unavailable


# Provided by E02 (crew_health.identity) as require_roles(operation_id); structural copy of its effect: the session
# for a request's headers, or a ProblemError (UNAUTHENTICATED, UNAUTHORIZED_SCOPE).
class RequestAuthenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext: ...


# Owned by this module -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredSample:
    sample_id: str
    help_session_id: str
    skill_id: str
    check_id: str
    at: datetime
    rate_per_min: Decimal | None
    reason: str | None
    in_band: bool | None
    direction: str | None
    text: str
    experimental: bool


class GuidedCheckRepository(Protocol):
    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheck | None: ...
    def add(self, uow: UnitOfWork, check: GuidedCheck) -> None: ...
    def save(self, uow: UnitOfWork, check: GuidedCheck, *, expected_revision: int) -> None: ...
    def count_active(self, uow: UnitOfWork, subject_id: str | None, skill_id: str | None) -> int: ...


class CoachingSampleRepository(Protocol):
    def add(self, uow: UnitOfWork, sample: StoredSample, *, release_id: str) -> None: ...
    def page(
        self, view: ReadSnapshot, help_session_id: str, *, after: tuple[datetime, str] | None, limit: int
    ) -> Sequence[StoredSample]: ...
    def last_at(
        self, view: ReadSnapshot | UnitOfWork, help_session_id: str, check_id: str
    ) -> datetime | None: ...
