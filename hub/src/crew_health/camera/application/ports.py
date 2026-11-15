"""Ports of the camera module, the hub side of the E06 camera worker (coordinator decision A7).

Provided (A1: the consumers' classes are not importable yet, so these are structural copies whose names and
parameters equal main's text exactly; a test compares them with `inspect.signature`, and composition binds them to
the consumers' classes under follow-up F1):
- `AdapterAssignmentPort`, `AdapterReadinessPort` and `AdapterAssignment`, declared by E05
  (crew_health.measurement.application.ports).
- `CameraReferencePort` with `ReferenceWindowRequest`, `ReferenceTicket` and `ReferenceOutcome`, declared by E30
  (crew_health.teach.application.ports).
Consumed:
- `WorkerTransport`, the authenticated HTTP link to the worker process (SYSTEM 3, DEPLOYMENT 7.3).
- `MissionStatePort` with `MissionFacts`, declared by E23 and copied by E05 (the mission's origin mode decides which
  worker may serve a request, M4); structural copy of E05's text.
- `ObservationCommands` (E01 records, R-E01-17; E30's structural copy) to record a persisted reference reading (M8).
- `AttemptReports`: E05 recordAttempt for a camera attempt that ended on the hub side (refused, lost, unreachable),
  so the request shows "Not measured" with the reason (SYSTEM 3 camera row). F1 composition binds it to E05's
  MeasurementService.record_attempt under this adapter's session; until something binds it the camera is never
  assigned.
E01's `TaskQueue` and `TaskRunner` (kernel.tasks) carry every worker call (M6); without them the camera is never
assigned either.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum

# Declared by E05 (crew_health.measurement.application.ports); structural copy until F1 makes it importable. -------


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


# Declared by E30 (crew_health.teach.application.ports); structural copy until F1 makes it importable. ----------


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


class CameraReferencePort(Protocol):
    def begin_window(self, request: ReferenceWindowRequest, *, timeout: timedelta) -> ReferenceTicket: ...
    def outcome(self, ticket_id: str, *, wait: timedelta) -> ReferenceOutcome: ...
    def abandon(self, ticket_id: str, *, timeout: timedelta) -> None: ...


# Consumed -------------------------------------------------------------------------------------------------------


# Declared by E23, copied by E05 (crew_health.measurement.application.ports); structural copy of E05's text.
@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


class MissionStatePort(Protocol):
    def current(self, uow: View | None = None) -> MissionFacts | None: ...


# E01 records (R-E01-17); same shape as E30's structural copy (teach.application.ports), with the camera's method.
@dataclass(frozen=True, slots=True)
class CameraObservationDraft:
    patient_id: str
    operator_id: str
    episode_id: str | None
    code: str
    value: Quantity
    observed_at: datetime
    entered_at: datetime
    origin_mode: OriginMode
    acquisition_method: Literal["camera_estimate"]  # the only method this module writes
    quality_status: Literal["accepted"]
    quality_rule_version: str
    body_region: str | None
    context: Mapping[str, str]


class ObservationCommands(Protocol):
    def canonical_unit(self, code: str) -> Unit: ...
    def append_observation(
        self, uow: UnitOfWork, draft: CameraObservationDraft, *, idempotency_key: str
    ) -> str: ...


type ReportResult = Literal["recorded", "refused", "unreachable"]  # internal enum


class AttemptReports(Protocol):
    """recordAttempt of a failed camera attempt with no observation, `reason` a reason code. Idempotent per key;
    `refused` means E05 answered and will not take it (for example the worker's own attempt took the sequence)."""

    def failed(
        self, request_id: str, *, sequence: int, at: datetime, reason: str, idempotency_key: str
    ) -> ReportResult: ...


@dataclass(frozen=True, slots=True)
class WorkerReply:
    status: int
    body: bytes


class WorkerUnreachable(Exception):
    """Connection refused, timed out or broken: the worker's state is unknown, never assumed."""


class WorkerTransport(Protocol):
    """One bounded, authenticated call into the camera worker (SYSTEM 3: every call into a worker has a timeout)."""

    def call(
        self, method: Literal["GET", "POST"], path: str, body: bytes | None, *, timeout_s: float
    ) -> WorkerReply: ...


__all__ = [
    "AdapterAssignment",
    "AdapterAssignmentPort",
    "AdapterReadinessPort",
    "AttemptReports",
    "CameraObservationDraft",
    "CameraReferencePort",
    "MissionFacts",
    "MissionStatePort",
    "ObservationCommands",
    "OriginMode",
    "ReferenceOutcome",
    "ReferenceTicket",
    "ReferenceWindowRequest",
    "ReportResult",
    "View",
    "WorkerReply",
    "WorkerTransport",
    "WorkerUnreachable",
]
