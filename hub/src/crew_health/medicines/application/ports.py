"""Public ports of crew_health.medicines (E32 4.3) and the ports it consumes. Imports nothing from medicines.domain
beyond its value types' module, so other modules can import it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

type View = UnitOfWork | ReadSnapshot


@dataclass(frozen=True, slots=True)
class Partition:
    """Every medicines read filters to the current mission, its mode and the environment (INV-M7)."""

    mission_id: str
    origin_mode: str
    environment_id: str


@dataclass(frozen=True, slots=True)
class RecordRow:
    record_id: str
    kind: str
    mission_id: str
    origin_mode: str
    subject_person_id: str | None
    lot_id: str | None
    environment_id: str | None
    at: datetime
    by_id: str
    command_key: str | None
    body: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Movement:
    movement_id: str
    partition: Partition
    lot_id: str
    kind: str
    delta: Decimal  # negative for a draw
    unit: str
    administration_id: str | None
    by_id: str
    command_key: str | None


class MedicinesRepository(Protocol):
    def add_record(self, uow: UnitOfWork, row: RecordRow) -> None: ...
    def record(self, view: View, record_id: str) -> RecordRow | None: ...
    def by_command_key(self, view: View, command_key: str) -> RecordRow | None: ...
    def records(
        self,
        view: View,
        kind: str,
        partition: Partition,
        *,
        limit: int,
        subject_person_id: str | None = None,
        lot_id: str | None = None,
    ) -> tuple[RecordRow, ...]: ...
    def terminal(self, view: View, proposal_id: str) -> tuple[str, str] | None: ...
    def add_terminal(self, uow: UnitOfWork, proposal_id: str, state: str, reason: str) -> None: ...
    def add_movement(self, uow: UnitOfWork, movement: Movement) -> None: ...
    def movement_by_key(self, view: View, command_key: str) -> str | None: ...
    def net_movements(self, view: View, partition: Partition, *, limit: int) -> Mapping[str, Decimal]: ...
    def add_ingredients(
        self, uow: UnitOfWork, administration_id: str, amounts: Mapping[str, tuple[Decimal, str]]
    ) -> None: ...
    def ingredients(self, view: View, administration_id: str) -> Mapping[str, tuple[Decimal, str]]: ...
    def correction(self, view: View, administration_id: str) -> str | None: ...
    def add_correction(
        self, uow: UnitOfWork, administration_id: str, reason: str, by_id: str, key: str
    ) -> None: ...


# ---- provided (E32 4.3) ----


class MedicationRecordReader(Protocol):
    """For E11 SubjectSummary: names only, scope checked."""

    def active_medicine_names(self, session: SessionContext, person_id: str) -> tuple[str, ...]: ...


# ---- consumed ----


class ActingOfficerPort(Protocol):
    """E31's cascade: whether `person_id` is the acting medical officer for `subject_person_id` now (the subject
    excluded). Absent provider: nobody is acting, so only the crew medical officer confirms."""

    def is_acting(self, view: View, person_id: str, subject_person_id: str) -> bool: ...
