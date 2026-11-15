"""The storage port of the participation module: what the application needs, implemented by the SQLite adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.storage import StorageSession
from crew_health.participation.domain.crew_goal import CrewConsent
from crew_health.participation.domain.derivation import AwardEvent
from crew_health.participation.domain.types import CompletionFact, CompletionRecord, ViewOrigin

type IntakeResult = Literal["queued", "duplicate", "conflict"]
type ProjectionState = Literal["pending", "applied", "nothing", "parked"]


@dataclass(frozen=True, slots=True)
class IntentRow:
    seq: int
    key: str
    payload: str
    attempts: int


class ParticipationStore(Protocol):
    def enqueue(
        self,
        conn: StorageSession,
        *,
        key: str,
        payload: str,
        payload_sha256: str,
        origin_mode: str,
        patient_id: str,
        at: datetime,
    ) -> IntakeResult: ...

    def pending(self, conn: StorageSession, limit: int) -> tuple[IntentRow, ...]: ...

    def intent_state(self, conn: StorageSession, key: str) -> ProjectionState | None: ...

    def count_state(self, conn: StorageSession, state: ProjectionState, patient_id: str | None) -> int: ...

    def mark(
        self,
        conn: StorageSession,
        seq: int,
        state: ProjectionState,
        attempts: int,
        error: str | None,
        at: datetime,
    ) -> None: ...

    def requeue_parked(self, conn: StorageSession, at: datetime) -> int: ...

    def insert_completion(self, conn: StorageSession, record: CompletionRecord, intent_seq: int) -> None: ...

    def facts(
        self, conn: StorageSession, *, mission_id: str, origin: ViewOrigin, patient_id: str, limit: int
    ) -> tuple[CompletionFact, ...]: ...

    def checked_days(
        self,
        conn: StorageSession,
        *,
        mission_id: str,
        origin: ViewOrigin,
        first_day: int,
        last_day: int,
        limit: int,
    ) -> Mapping[str, frozenset[int]]: ...

    def ledger(
        self, conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin
    ) -> tuple[AwardEvent, ...]: ...

    def append_award(
        self,
        conn: StorageSession,
        *,
        award_id: str,
        patient_id: str,
        mission_id: str,
        origin: ViewOrigin,
        event: AwardEvent,
        at: datetime,
    ) -> None: ...

    def consent(
        self, conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin
    ) -> CrewConsent: ...

    def consents(
        self, conn: StorageSession, mission_id: str, origin: ViewOrigin, limit: int
    ) -> Mapping[str, CrewConsent]: ...

    def append_consent(
        self,
        conn: StorageSession,
        *,
        patient_id: str,
        mission_id: str,
        origin: ViewOrigin,
        consent: CrewConsent,
        at: datetime,
        policy_version: str,
    ) -> None: ...

    def consent_effective_at(
        self, conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin
    ) -> str | None: ...
