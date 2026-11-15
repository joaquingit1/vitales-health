"""Repository port for E05's own tables. Only measurement adapters implement it (SYSTEM 4.1, 5.7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import Actor
from crew_health.measurement.domain.model import Attempt, CapabilityGap, MeasurementRequest, RequestState


@dataclass(frozen=True, slots=True)
class Transition:
    request_id: str
    revision: int
    actor: Actor
    from_state: RequestState | None
    to_state: RequestState
    cause: str
    attempt_id: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class AcquisitionBinding:
    """The adapter, method and device an acquisition generation was assigned at start; nothing else may write."""

    request_id: str
    generation: int
    operator_id: str
    adapter_id: str
    method: str
    device_id: str | None
    fenced: bool


class MeasurementStore(Protocol):
    def get_request(self, conn: StorageSession, request_id: str) -> MeasurementRequest | None: ...
    def get_request_at(
        self, conn: StorageSession, request_id: str, revision: int
    ) -> MeasurementRequest | None: ...
    def insert_request(self, conn: StorageSession, request: MeasurementRequest) -> None: ...
    def update_request(
        self, conn: StorageSession, request: MeasurementRequest, *, expected_revision: int
    ) -> None: ...
    def insert_attempt(
        self,
        conn: StorageSession,
        request_id: str,
        attempt: Attempt,
        *,
        recorded_at: datetime,
        request_revision: int,
    ) -> None: ...
    def insert_transition(
        self, conn: StorageSession, transition: Transition, request: MeasurementRequest
    ) -> None: ...
    def insert_gap(self, conn: StorageSession, gap: CapabilityGap) -> None: ...
    def get_gap(self, conn: StorageSession, gap_id: str) -> CapabilityGap | None: ...
    def gap_for_request(self, conn: StorageSession, request_id: str) -> CapabilityGap | None: ...
    def open_gap(
        self, conn: StorageSession, episode_id: str, capability_code: str
    ) -> CapabilityGap | None: ...
    def start_acquisition(
        self, conn: StorageSession, binding: AcquisitionBinding, *, at: datetime
    ) -> None: ...
    def acquisition(
        self, conn: StorageSession, request_id: str, generation: int
    ) -> AcquisitionBinding | None: ...
    def fence_acquisition(
        self, conn: StorageSession, request_id: str, generation: int, *, at: datetime
    ) -> None: ...
