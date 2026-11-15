"""SYSTEM 4.2: groundlink owns its durable transport state."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.groundlink.application.ports import GroundLinkView, InboundItem, LinkGap, RefKind
from crew_health.groundlink.domain.link import DemoOverride
from crew_health.groundlink.domain.queue import Bundle
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


@dataclass(frozen=True, slots=True)
class StatusRecord:
    at: datetime
    link: GroundLinkView


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    event_id: str
    subscriber: str
    event_type: str
    reason: str
    attempts: int
    quarantined_at: datetime


@dataclass(frozen=True, slots=True)
class Ack:
    ack_id: str
    item: InboundItem
    state: Literal["waiting", "due", "delivered"]
    arrival_due_at: datetime | None
    delivered_at: datetime | None
    attempts: int
    next_attempt_at: datetime | None
    last_error: str | None


class GroundLinkRepository(Protocol):
    def quarantine(self, uow: UnitOfWork, record: QuarantineRecord) -> None: ...
    def quarantined(self, view: ReadSnapshot, limit: int) -> tuple[QuarantineRecord, ...]: ...
    def environment(self, view: ReadSnapshot) -> str | None: ...
    def environment_at(self, view: ReadSnapshot) -> datetime | None: ...
    def override(self, view: ReadSnapshot) -> DemoOverride | None: ...
    def last_gap(self, view: ReadSnapshot) -> LinkGap | None: ...
    def history(self, view: ReadSnapshot) -> tuple[StatusRecord, ...]: ...
    def append(
        self,
        uow: UnitOfWork,
        kind: Literal["environment", "override", "clear", "observed"],
        at: datetime,
        link: GroundLinkView,
        override: DemoOverride | None = None,
    ) -> int: ...
    def recorded(self, view: ReadSnapshot, seq: int) -> GroundLinkView | None: ...
    def last_contact(self, view: ReadSnapshot) -> datetime | None: ...
    def bundle(self, view: ReadSnapshot, ref_kind: RefKind, ref_id: str) -> Bundle | None: ...
    def insert_bundle(self, uow: UnitOfWork, bundle: Bundle) -> None: ...
    def save_bundle(self, uow: UnitOfWork, bundle: Bundle) -> None: ...
    def send_candidates(self, view: ReadSnapshot) -> tuple[Bundle, ...]: ...
    def arrival_candidates(self, view: ReadSnapshot) -> tuple[Bundle, ...]: ...
    def delivered(self, view: ReadSnapshot, since: datetime | None, limit: int) -> tuple[Bundle, ...]: ...
    def ack_exists(self, view: ReadSnapshot, event_id: str) -> bool: ...
    def insert_ack(self, uow: UnitOfWork, ack: Ack) -> None: ...
    def save_ack(self, uow: UnitOfWork, ack: Ack) -> None: ...
    def inbound_candidates(self, view: ReadSnapshot) -> tuple[Ack, ...]: ...
