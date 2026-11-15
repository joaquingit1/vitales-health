"""CONTRACTS 26: the ground reader and E28's optional plan seam. No domain imports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.uow import ReadSnapshot


@dataclass(frozen=True, slots=True)
class LogItem:
    kind: Literal["ground_message", "handoff"]
    ref_id: str
    title: str
    queued_at: datetime
    delivered_at: datetime | None
    catch_up: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    plan_run_id: str | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class LogGap:
    started_at: datetime
    ended_at: datetime | None
    gap_kind: str
    gap_planned: bool


@dataclass(frozen=True, slots=True)
class GroundLog:
    since: datetime | None
    gap: LogGap | None
    items: tuple[LogItem, ...]
    as_of: datetime


class GroundLogView(Protocol):
    def log(self, since: datetime | None, *, recipient: str) -> GroundLog: ...


@dataclass(frozen=True, slots=True)
class PlanLinkView:
    """The link for E28's ground_log line (E28 4.1 GroundLinkReader), stamped with the read snapshot's time. Only a
    measured link is not simulated."""

    status: Literal["up", "delayed", "down"]
    next_contact_at: datetime | None
    one_way_delay_s: float | None
    simulated: bool
    observed_at: datetime


class PlanGroundLink(Protocol):
    """E29's ground presentation for plans: the plans module sees the link only through this port (EI1, D43). An
    absent or unknown link raises, which plans renders as its Unknown line."""

    def current(self) -> PlanLinkView: ...


GROUND_LOG_SCOPE = ("ground_log", "catchup/approved/1")
"""SYSTEM 5.7.1 stream `catchup/approved/1`: the grant a named ground recipient needs for each subject."""


# Provided by E02 (crew_health.consent); structural copy until it is on main, same shape as measurement's copy.
class ConsentQueries(Protocol):
    def allows(
        self, view: ReadSnapshot, *, subject_person_id: str, recipient: str, scope: tuple[str, str]
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    event_id: str
    subscriber: str
    reason: str
    attempts: int
    quarantined_at: datetime


# Provided by E28 (crew_health.plans); structural copy of E28 4.2 until it is on main.
@dataclass(frozen=True, slots=True)
class PlanCatchUpInputs:
    done_steps: tuple[tuple[datetime, str], ...]
    watch_steps: tuple[tuple[str, str], ...]
    replaces_ground_task: tuple[str, ...]
    ground_log_items: tuple[str, ...]


class PlanCatchUpPort(Protocol):
    def catch_up_inputs(self, plan_run_id: str) -> PlanCatchUpInputs | None: ...
