"""Storage protocol of the roles module; implemented in adapters over the kernel StorageSession."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.roles.domain.drill import Drill, Participation
from crew_health.roles.domain.engine import ContextKind
from crew_health.roles.domain.freshness import Practice
from crew_health.roles.domain.lifecycle import Assignment


@dataclass(frozen=True, slots=True)
class ContextRow:
    kind: ContextKind
    context_id: str
    proposal_seq: int
    subject_person_id: str | None
    open: bool
    as_of: datetime | None
    degraded_at: datetime | None
    revision: int | None = None
    next_check_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UnfilledRow:
    role_id: str
    label: str
    required: bool


@dataclass(frozen=True, slots=True)
class RecomputeRow:
    kind: ContextKind
    context_id: str
    requested_at: datetime
    attempts: int
    version: int
    first_requested_at: datetime


class RolesRepository(Protocol):
    def current(self, kind: ContextKind, context_id: str) -> list[Assignment]: ...
    def get(self, role_assignment_id: str) -> Assignment | None: ...
    def insert(self, assignment: Assignment, proposal_seq: int) -> None: ...
    def save(self, assignment: Assignment, expected_revision: int) -> bool: ...
    def retire(
        self,
        role_assignment_id: str,
        *,
        at: datetime,
        reason: str,
        replaced_by_id: str | None,
        expected_revision: int,
    ) -> bool: ...
    def current_for_person(self, person_id: str, limit: int) -> list[Assignment]: ...
    def current_medical_officer(self, person_id: str, limit: int) -> list[Assignment]: ...
    def current_of_contexts(
        self, kind: ContextKind, context_ids: Collection[str], limit: int
    ) -> list[Assignment]: ...
    def context(self, kind: ContextKind, context_id: str) -> ContextRow | None: ...
    def open_contexts(self, limit: int) -> list[ContextRow]: ...
    def contexts_to_recheck(self, at: datetime, limit: int) -> list[ContextRow]: ...
    def recompute_version(self, kind: ContextKind, context_id: str) -> int | None: ...
    def put_context(self, row: ContextRow) -> None: ...
    def unfilled(self, kind: ContextKind, context_id: str) -> list[UnfilledRow]: ...
    def set_unfilled(self, kind: ContextKind, context_id: str, rows: Sequence[UnfilledRow]) -> None: ...
    def request_recompute(self, kind: ContextKind, context_id: str, *, reason: str, at: datetime) -> None: ...
    def due_recomputes(self, at: datetime, limit: int) -> list[RecomputeRow]: ...
    def finish_recompute(self, row: RecomputeRow) -> None: ...
    def fail_recompute(self, row: RecomputeRow, *, next_attempt_at: datetime, error: str) -> None: ...
    def add_practices(
        self, practices: Sequence[Practice], *, recorded_by: str, release_id: str, at: datetime
    ) -> int: ...
    def latest_practice(
        self,
        person_id: str,
        skill_id: str,
        mission_id: str,
        origin: str,
        at: datetime,
        source: str | None = None,
    ) -> Practice | None: ...
    def drill_practices(
        self, person_id: str, mission_id: str, origin: str, at: datetime, limit: int
    ) -> list[Practice]: ...
    def get_drill(self, drill_id: str) -> Drill | None: ...
    def insert_drill(self, drill: Drill, *, environment_id: str, roster_index: dict[str, int]) -> None: ...
    def save_drill(
        self, drill: Drill, expected_revision: int, *, completed_by: str | None, core_fresh: dict[str, bool]
    ) -> bool: ...
    def running_drills(self, limit: int) -> list[Drill]: ...
    def participations(
        self, person_id: str, mission_id: str, origin: str, limit: int
    ) -> list[Participation]: ...
