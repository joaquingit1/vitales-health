"""Outbound port of the consent module, implemented in `consent/adapters` (E02 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crew_health.consent.domain.grants import Grant
from crew_health.kernel.storage import StorageSession


class ConsentStore(Protocol):
    def insert(self, connection: StorageSession, grant: Grant) -> None: ...
    def grant(self, connection: StorageSession, grant_id: str) -> Grant | None: ...
    def matching(
        self,
        connection: StorageSession,
        *,
        subject_person_id: str,
        recipient: str,
        purpose: str,
        object_ref: str,
        mission_id: str,
        origin_mode: str,
        limit: int,
    ) -> tuple[Grant, ...]: ...
    def active_count(self, connection: StorageSession, subject_person_id: str) -> int: ...
    def for_subject(
        self, connection: StorageSession, subject_person_id: str, *, active: bool, limit: int
    ) -> tuple[Grant, ...]: ...
    def save_terminal(self, connection: StorageSession, grant: Grant, *, expected_revision: int) -> bool: ...
    def fence_finite(self, connection: StorageSession, *, at: datetime, keep_epoch: str | None) -> int: ...
