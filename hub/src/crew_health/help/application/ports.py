"""Ports the help module consumes and its own repositories (E26 4.2, SYSTEM 4.2).

Every consumed port is optional except storage and configuration: help entry works with all of them absent or
failing (AGENTS rule 3). Providers not yet on main are structural copies of their designs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext

OriginMode = Literal["LIVE", "REPLAY", "SIM"]
Level = Literal["act", "emergency"]


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionFacts(Protocol):
    @property
    def mission_id(self) -> str: ...
    @property
    def mission_mode(self) -> OriginMode: ...
    @property
    def active_environment_id(self) -> str: ...


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionStatePort(Protocol):
    def current(self, uow: ReadSnapshot | None = None) -> MissionFacts | None: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class CrewMember(Protocol):
    @property
    def person_id(self) -> str: ...
    @property
    def display_name(self) -> str: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RosterPort(Protocol):
    def crew(self, at: datetime) -> Sequence[CrewMember]: ...


# Provided by E31 (crew_health.roles); structural copy until it is on main.
class BroadcastRole(Protocol):
    @property
    def role_assignment_id(self) -> str: ...
    @property
    def role_id(self) -> str: ...
    @property
    def label(self) -> str: ...
    @property
    def person_id(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def state(self) -> Literal["proposed", "accepted", "changed"]: ...


# Provided by E31 (crew_health.roles); structural copy until it is on main.
class BroadcastRolesSource(Protocol):
    def roles_for_session(self, help_session_id: str) -> Sequence[BroadcastRole]: ...


@dataclass(frozen=True, slots=True)
class AssistantStart:
    status: Literal["waiting", "stopped", "unavailable"]
    conversation_id: str | None


# Provided by E13 (crew_health.assistant); structural copy until it is on main. E13 4.3 plus `help_turn_id`
# (E26 4.2 HelpTurnContext.help_turn_id): E13 echoes it to HelpAssistantSink, which is keyed on it.
class HelpAssistantPort(Protocol):
    def start_help_turn(
        self, help_session_id: str, help_turn_id: str, actor: Actor, text: str, conversation_id: str | None
    ) -> AssistantStart: ...
    def cancel_help_turn(self, help_turn_id: str) -> None: ...


# Provided by E24 (crew_health.rules); structural copy until it is on main.
class WarningQueries(Protocol):
    def visible(self, session: SessionContext, warning_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class LinkSnapshot:
    """The ground link as E29 reports it; `contract` is the GroundLink wire object for the preview."""

    status: Literal["up", "delayed", "down"]
    next_contact_at: datetime | None
    simulated: bool
    contract: Mapping[str, object]


class TickLineView(Protocol):
    @property
    def flow_id(self) -> str: ...
    @property
    def index(self) -> int: ...
    @property
    def text(self) -> str: ...
    @property
    def ticked_at(self) -> datetime: ...
    @property
    def time_text(self) -> str | None: ...


class HelpLogSnapshotView(Protocol):
    """The durable snapshot a ground message was drafted from (SYSTEM 8.7); provided through HelpLogSnapshots."""

    @property
    def help_session_id(self) -> str: ...
    @property
    def session_revision(self) -> int: ...
    @property
    def subject_name(self) -> str: ...
    @property
    def place(self) -> str | None: ...
    @property
    def flow_id(self) -> str | None: ...
    @property
    def flow_title(self) -> str | None: ...
    @property
    def answer_lines(self) -> tuple[str, ...]: ...
    @property
    def ticks(self) -> Sequence[TickLineView]: ...
    @property
    def crew_called_at(self) -> datetime | None: ...
    @property
    def environment_id(self) -> str | None: ...
    @property
    def queued_at(self) -> datetime: ...


class CatchUpLines(Protocol):
    @property
    def what_happened(self) -> tuple[str, ...]: ...
    @property
    def what_was_done(self) -> tuple[str, ...]: ...
    @property
    def being_watched(self) -> tuple[str, ...]: ...
    @property
    def advice_welcome(self) -> tuple[str, ...]: ...


# Provided by E29 (crew_health.catchup); structural copy until it is on main.
class GroundLogComposer(Protocol):
    def link(self) -> LinkSnapshot: ...
    def for_help(self, snapshot: HelpLogSnapshotView) -> CatchUpLines: ...


# Provided by E01 (crew_health.records, TextRulesPort); structural copy until it is on main.
class TextRulesPort(Protocol):
    def amount_pattern(self, release_id: str) -> re.Pattern[str]: ...
    def medicine_terms(self, release_id: str) -> frozenset[str]: ...


# Adapter over E27 GuidanceEligibility (crew_health.guidance, SYSTEM 8.2); structural copy until it is on main.
class MeasurementGate(Protocol):
    def eligible_measurements(
        self, flow_id: str, release_id: str, origin_mode: OriginMode | None
    ) -> frozenset[int]: ...


@dataclass(frozen=True, slots=True)
class ReadingView:
    observation_id: str
    code: str
    label: str
    value_text: str
    observed_at: datetime
    received_at: datetime
    origin_mode: OriginMode
    source_label: str
    body_region: str | None


@dataclass(frozen=True, slots=True)
class NamedList:
    state: Literal["recorded", "none_recorded", "unknown"]
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileFacts:
    allergies: NamedList
    medicines: NamedList


# Provided by E01 (crew_health.records); structural copy until it is on main.
class ObservationReadPort(Protocol):
    def latest_accepted(
        self, person_id: str, codes: Sequence[str], since: datetime, until: datetime, limit_per_code: int
    ) -> Sequence[ReadingView]: ...


# Provider per R-E04-6; structural copy until it is on main.
class ProfileFactsPort(Protocol):
    def allergies_and_medicines(self, person_id: str) -> ProfileFacts: ...


# Ports help provides (SYSTEM 4.2, 4.4, 8.1). Consumers import only this module, which never imports help.domain.


@dataclass(frozen=True, slots=True)
class HelpContext:
    visible: bool
    origin_mode: OriginMode | None
    environment_id: str | None


@dataclass(frozen=True, slots=True)
class HelpSessionSnapshot:
    help_session_id: str
    revision: int
    open: bool
    subject_person_id: str | None
    opener_person_id: str | None
    helper_ids: frozenset[str]
    flow_id: str | None
    ui_copy_version: str
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class HelpFlowRef:
    help_session_id: str
    node_kind: Literal["triage", "flow"]
    node_id: str
    pins: tuple[tuple[str, str], ...]
    subject_person_id: str | None
    origin_mode: OriginMode | None


class HelpSessionReader(Protocol):
    def context(self, session: SessionContext, help_session_id: str) -> HelpContext: ...
    def readable_by(self, session: SessionContext, help_session_id: str) -> bool: ...
    def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None: ...
    def qualifying_sessions(self) -> Sequence[HelpFlowRef]: ...


class HelpEntryPort(Protocol):
    def open_from_red_flag(
        self,
        episode_id: str,
        answer_id: str,
        subject_person_id: str,
        flow_id: str,
        client_time: datetime,
        idempotency_key: str,
    ) -> str: ...


class HelpLogSnapshots(Protocol):
    def for_message(self, ground_message_id: str) -> HelpLogSnapshotView | None: ...


EventOutcome = Literal["applied", "duplicate", "buffered"]


class GroundMessageLifecyclePort(Protocol):
    def mark_sent(
        self,
        message_id: str,
        event_id: str,
        sent_at: datetime,
        one_way_delay_s: float | None,
        simulated: bool,
    ) -> EventOutcome: ...
    def record_arrival(self, message_id: str, event_id: str, arrived_at: datetime) -> EventOutcome: ...
    def record_receipt(
        self, message_id: str, event_id: str, received_at: datetime, arrived_at: datetime
    ) -> EventOutcome: ...
    def record_reply(
        self, message_id: str, event_id: str, text: str, by: Actor, replied_at: datetime, arrived_at: datetime
    ) -> EventOutcome: ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class HelpTurnCandidate:
    """What E13 hands back for a help turn; the guard decides what, if anything, is shown."""

    help_turn_id: str
    text: str | None
    action: Mapping[str, object] | None
    tool_calls: tuple[ToolCall, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HelpGuardResult:
    accepted: bool
    rule_ids: tuple[str, ...]
    field_paths: tuple[str, ...]
    citations: tuple[tuple[str, str], ...]
    display_text: str | None


class HelpAssistantSink(Protocol):
    def turn_started(self, help_turn_id: str) -> None: ...
    def turn_completed(self, help_turn_id: str, candidate: HelpTurnCandidate) -> HelpGuardResult: ...
    def turn_failed(self, help_turn_id: str, error_code: str) -> None: ...
    def runtime_status(
        self, status: Literal["off", "waiting", "answering", "stopped", "unavailable"]
    ) -> None: ...


class HelpTurnGuardPort(Protocol):
    def check(self, help_session_id: str, candidate: HelpTurnCandidate) -> HelpGuardResult: ...
