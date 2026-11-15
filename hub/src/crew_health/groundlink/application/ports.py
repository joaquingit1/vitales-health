"""SYSTEM 4.2 public link and approved transport seams."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork

type LinkStatus = Literal["up", "delayed", "down"]
type GapKind = Literal["planned_line_of_sight", "relay_outage", "conjunction", "equipment", "unknown"]
type LinkSource = Literal["schedule", "measured", "demo_switch"]
type RefKind = Literal["ground_message", "handoff"]


@dataclass(frozen=True, slots=True)
class GroundLinkView:
    status: LinkStatus
    one_way_delay_s: float | None
    next_contact_at: datetime | None
    last_contact_at: datetime | None
    source: LinkSource
    environment_id: str
    delay_text: str
    delay_source_ids: tuple[str, ...]
    gap_kind: GapKind | None
    gap_planned: bool


@dataclass(frozen=True, slots=True)
class LinkGap:
    started_at: datetime
    ended_at: datetime | None
    gap_kind: GapKind
    gap_planned: bool


class GroundLinkQuery(Protocol):
    """SYSTEM 4.2: local reads only; an unknown environment stays missing."""

    def current(self, view: ReadSnapshot) -> GroundLinkView | None: ...
    def last_gap(self, view: ReadSnapshot) -> LinkGap | None: ...


@dataclass(frozen=True, slots=True)
class OutboxItem:
    ref_kind: RefKind
    ref_id: str
    content_sha256: str
    queued_at: datetime
    simulated: bool


@dataclass(frozen=True, slots=True)
class BundleStatus:
    ref_kind: RefKind
    ref_id: str
    state: Literal["queued", "sending", "sent", "arrived"]
    queued_at: datetime
    sent_at: datetime | None
    arrived_at: datetime | None
    attempts: int
    simulated: bool


class BundleOutbox(Protocol):
    """CONTRACTS 12: approved items wait until contact; never deleted."""

    def enqueue(self, uow: UnitOfWork, item: OutboxItem) -> str: ...
    def status(self, view: ReadSnapshot, ref_kind: RefKind, ref_id: str) -> BundleStatus | None: ...
    def delivered(
        self, view: ReadSnapshot, *, since: datetime | None, limit: int
    ) -> tuple[BundleStatus, ...]: ...


@dataclass(frozen=True, slots=True)
class InboundItem:
    event_id: str
    ref_kind: RefKind
    ref_id: str
    kind: Literal["receipt", "reply"]
    sent_by_ground_at: datetime
    text: str | None
    by: Actor


class BundleInbox(Protocol):
    """EI5: replies reach their owner as new records."""

    def receive(self, item: InboundItem) -> Literal["accepted", "duplicate"]: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionFacts(Protocol):
    @property
    def mission_id(self) -> str: ...
    @property
    def mission_mode(self) -> Literal["LIVE", "REPLAY", "SIM"]: ...
    @property
    def active_environment_id(self) -> str: ...


class MissionStatePort(Protocol):
    def current(self, uow: ReadSnapshot | None = None) -> MissionFacts | None: ...
