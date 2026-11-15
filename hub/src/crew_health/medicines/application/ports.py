"""Public ports of crew_health.medicines (E32 4.3) and the ports it consumes. Imports nothing from medicines.domain
beyond its value types' module, so other modules can import it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.medicines.domain.model import LotSummary, WeightObservation

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


@dataclass(frozen=True, slots=True)
class RunStart:
    """What a run ran on (E32 5.2): the hub clock at its start, the active bundle and the stored inputs artifact."""

    as_of: datetime
    bundle_sha256: str
    inputs_sha256: str


@dataclass(frozen=True, slots=True)
class RunRow:
    """One potency assessment run: scheduled with its trigger, then started (`start`) by the worker."""

    run_id: str
    mission_id: str
    origin_mode: str
    trigger: str
    task_id: str
    process_epoch: str
    at: datetime
    by_id: str
    command_key: str | None
    start: RunStart | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    result_sha256: str
    engine_version: str
    summaries_json: str


class ArtifactCorrupt(Exception):
    """Stored bytes no longer hash to their key: the run fails `artifact_corrupt`, never uses the bytes."""


class PotencyStore(Protocol):
    """Artifacts, pharmacy bundles and assessment runs (E32 5.2). Every read is bounded; reads re-hash."""

    def put_artifact(self, uow: UnitOfWork, kind: str, data: bytes) -> str: ...
    def artifact(self, view: View, sha256: str) -> bytes | None: ...
    def has_artifact(self, view: View, sha256: str) -> bool: ...
    def prune_artifact(self, uow: UnitOfWork, sha256: str, run_id: str) -> None: ...
    def add_bundle(self, uow: UnitOfWork, bundle_sha256: str, by_id: str) -> None: ...
    def has_bundle(self, view: View, bundle_sha256: str) -> bool: ...
    def activate_bundle(self, uow: UnitOfWork, bundle_sha256: str, by_id: str) -> None: ...
    def active_bundle(self, view: View) -> str | None: ...
    def add_run(self, uow: UnitOfWork, run: RunRow) -> None: ...
    def start_run(self, uow: UnitOfWork, run_id: str, start: RunStart) -> None: ...
    def run(self, view: View, run_id: str) -> RunRow | None: ...
    def run_by_key(self, view: View, command_key: str) -> RunRow | None: ...
    def runs(self, view: View, mission_id: str, origin_mode: str, *, limit: int) -> tuple[RunRow, ...]: ...
    def add_run_event(self, uow: UnitOfWork, run_id: str, status: str, error_code: str | None) -> None: ...
    def run_status(self, view: View, run_id: str) -> tuple[str, str | None]: ...
    def unfinished_runs(self, view: View, *, limit: int) -> tuple[str, ...]: ...
    def add_result(self, uow: UnitOfWork, result: RunResult) -> None: ...
    def result(self, view: View, run_id: str) -> RunResult | None: ...
    def latest_completed(self, view: View, mission_id: str, origin_mode: str) -> RunRow | None: ...
    def referenced_runs(self, view: View, *, limit: int) -> frozenset[str]: ...
    def net_by_partition(self, view: View, *, limit: int) -> Mapping[tuple[str, str, str, str], Decimal]: ...


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """One engine run in the child process (E32 5.3): `completed` with the canonical result bytes, or `failed`
    (engine code, `memory_limit`, `artifact_corrupt`, `engine_unavailable`) or `timed_out`, never an exception."""

    status: Literal["completed", "failed", "timed_out"]
    result: bytes | None
    error_code: str | None
    detail: str


class PotencyEngine(Protocol):
    """The pharmacy pipeline behind a process boundary (AGENTS section 10: consumed, never edited)."""

    def assess(self, bundle: Mapping[str, bytes], inputs: bytes) -> RunOutcome: ...
    def replay(self, bundle: Mapping[str, bytes], inputs: bytes, recorded: bytes) -> RunOutcome: ...
    def summaries(self, run_id: str, result: bytes) -> Mapping[str, LotSummary]: ...


class BundleCheck(Protocol):
    """pharmacy's own `load_bundle` over the files, behind the adapter: None when it loads, else the reason."""

    def check(self, files: Mapping[str, bytes]) -> str | None: ...


class WeightReader(Protocol):
    """The subject's latest accepted body mass observations in this mission and mode, newest first, in kilograms
    (E32 3.5 step 2; records' query port behind adapters.weights). Absent provider or no observation: weight is
    absent and a per-kilogram rule refuses `weight_missing`, never a default weight."""

    def weights(
        self, view: View, person_id: str, *, mission_id: str, origin_mode: str, limit: int
    ) -> tuple[WeightObservation, ...]: ...


# ---- provided (E32 4.3) ----


class MedicationRecordReader(Protocol):
    """For E11 SubjectSummary: names only, scope checked."""

    def active_medicine_names(self, session: SessionContext, person_id: str) -> tuple[str, ...]: ...


# ---- consumed ----


class ActingOfficerPort(Protocol):
    """E31's cascade: whether `person_id` is the acting medical officer for `subject_person_id` now (the subject
    excluded). Absent provider: nobody is acting, so only the crew medical officer confirms."""

    def is_acting(self, view: View, person_id: str, subject_person_id: str) -> bool: ...
