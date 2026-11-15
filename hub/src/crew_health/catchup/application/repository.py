"""Catch-up owns its approved composition, never the transport's tables."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.catchup.application.ports import QuarantineRecord
from crew_health.catchup.domain.compose import Composition
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


@dataclass(frozen=True, slots=True)
class Message:
    ground_message_id: str
    help_session_id: str
    session_revision: int
    queued_at: datetime
    title: str
    simulated: bool
    state: str = "pending"
    pinned_versions: str | None = None
    composed_at: datetime | None = None
    subject_person_id: str | None = None


class CatchUpRepository(Protocol):
    def message(self, view: ReadSnapshot, ref_id: str) -> Message | None: ...
    def for_revision(self, view: ReadSnapshot, session_id: str, revision: int) -> Message | None: ...
    def insert(self, uow: UnitOfWork, message: Message) -> None: ...
    def lines(self, view: ReadSnapshot, ref_id: str) -> Composition: ...
    def freeze(
        self, uow: UnitOfWork, ref_id: str, lines: Composition, pinned: str, subject_person_id: str | None
    ) -> Composition: ...
    def quarantine(self, uow: UnitOfWork, record: QuarantineRecord, event_type: str) -> None: ...
    def quarantined(self, view: ReadSnapshot, *, limit: int) -> tuple[QuarantineRecord, ...]: ...
