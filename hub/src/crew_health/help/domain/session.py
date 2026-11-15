"""HelpSession aggregate: the state machine over the pinned responder-flows.json (CONTRACTS 16, E26 3.2).

Pure: every function takes the time, the pinned documents and the limits it needs. Each applied action
increments `revision` by one and adds exactly one log line; an idempotent repeat changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.help.domain.docs import ConfigPin, FlowDef, PinnedHelpDocs
from crew_health.help.domain.errors import (
    HelpActionNotAllowed,
    HelpInvalidTransition,
    HelpInvalidValue,
    HelpLogFull,
    HelpSessionClosed,
    HelpStaleVersion,
)
from crew_health.help.domain.render import render

ActorKind = Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"]
LogKind = Literal["answer", "step", "crew", "ground", "assistant", "note", "system"]
PathKind = Literal["triage", "flow", "start_over"]
NodeKind = Literal["triage", "flow"]
EntryKind = Literal["button", "red_flag", "warning", "urgent_event_alias", "plan_step"]
SubjectKind = Literal["self", "crewmate", "unknown"]
RegionSource = Literal["none", "derived", "person"]
AssistantStatus = Literal["off", "waiting", "answering", "stopped", "unavailable"]
TurnState = Literal["held", "requested", "running"]
Disposition = Literal[
    "resolved_on_board", "handed_to_crew_medical_officer", "ground_consulted", "false_alarm", "other"
]
OriginMode = Literal["LIVE", "REPLAY", "SIM"]
ActionName = Literal[
    "answer",
    "switch_flow",
    "tick_step",
    "untick_step",
    "call_crew",
    "join",
    "leave",
    "start_over",
    "set_body_region",
    "set_place",
    "add_note",
    "ask_assistant",
    "close",
]
# CONTRACTS 16 "Concurrency": these commute, so a stale If-Match is applied to the current revision.
COMMUTING: frozenset[str] = frozenset({"tick_step", "untick_step", "call_crew", "join", "leave", "add_note"})
_ACTION_ORDER: tuple[ActionName, ...] = (
    "answer",
    "switch_flow",
    "call_crew",
    "join",
    "leave",
    "start_over",
    "set_body_region",
    "set_place",
    "add_note",
    "ask_assistant",
    "close",
    "tick_step",
    "untick_step",
)


@dataclass(frozen=True, slots=True)
class ActorRef:
    kind: ActorKind
    id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class Who:
    """The authenticated actor of a command, resolved by the application from SessionContext only."""

    ref: ActorRef
    person_id: str | None


@dataclass(frozen=True, slots=True)
class LogLine:
    at: datetime
    text: str
    by: ActorRef | None
    kind: LogKind
    client_at: datetime | None
    hub_at: datetime


@dataclass(frozen=True, slots=True)
class PathEntry:
    node_id: str
    kind: PathKind
    answer_index: int | None
    answer_label: str | None
    at: datetime
    by: ActorRef
    client_at: datetime | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class Tick:
    flow_id: str
    step_index: int
    ticked_at: datetime
    ticked_by: ActorRef
    client_at: datetime | None


@dataclass(frozen=True, slots=True)
class Untick:
    """When a step was last unticked (event time), so an older tick arriving late cannot undo it (AGENTS 6)."""

    flow_id: str
    step_index: int
    at: datetime


@dataclass(frozen=True, slots=True)
class Helper:
    person_id: str
    display_name: str
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantState:
    status: AssistantStatus
    conversation_id: str | None
    last_text: str | None
    citations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingTurn:
    help_turn_id: str
    asked_by: ActorRef
    asked_at: datetime
    text: str
    state: TurnState
    epoch: int


@dataclass(frozen=True, slots=True)
class CloseInfo:
    disposition: Disposition
    note: str | None
    by: ActorRef


@dataclass(frozen=True, slots=True)
class HelpLimits:
    """Operational bounds from hub settings (SYSTEM 5.11, E26 5.4); never content."""

    log_soft_cap_bytes: int
    log_hard_cap_bytes: int
    path_max_entries: int
    note_max_chars: int
    place_max_chars: int
    offline_path_max: int


@dataclass(frozen=True, slots=True)
class HelpSession:
    help_session_id: str
    revision: int
    state: Literal["open", "closed"]
    opened_at: datetime
    client_opened_at: datetime | None
    opened_by: ActorRef
    entry_kind: EntryKind
    entry_ref_id: str | None
    subject_kind: SubjectKind
    subject_person_id: str | None
    subject_display_name: str | None
    place: str | None
    body_region: str | None
    body_region_source: RegionSource
    node_kind: NodeKind
    node_id: str
    node_revision: int
    context_epoch: int
    helpers: tuple[Helper, ...]
    path: tuple[PathEntry, ...]
    log: tuple[LogLine, ...]
    ticks: tuple[Tick, ...]
    visited_flows: tuple[str, ...]
    crew_called_at: datetime | None
    broadcast_id: str
    station_session_ids: tuple[str, ...]
    assistant: AssistantState
    turn: PendingTurn | None
    ground_message_ids: tuple[str, ...]
    closed_at: datetime | None
    close: CloseInfo | None
    release_id: str
    config_pins: tuple[ConfigPin, ...]
    mission_id: str | None
    origin_mode: OriginMode | None
    environment_id: str | None
    log_bytes: int
    unticks: tuple[Untick, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def helper_ids(self) -> tuple[str, ...]:
        return tuple(h.person_id for h in self.helpers)

    def is_ticked(self, flow_id: str, step_index: int) -> Tick | None:
        for tick in self.ticks:
            if tick.flow_id == flow_id and tick.step_index == step_index:
                return tick
        return None

    def unticked_at(self, flow_id: str, step_index: int) -> datetime | None:
        for mark in self.unticks:
            if mark.flow_id == flow_id and mark.step_index == step_index:
                return mark.at
        return None


@dataclass(frozen=True, slots=True)
class HelpCommand:
    action: ActionName
    client_time: datetime
    node_id: str | None = None
    answer_index: int | None = None
    flow_id: str | None = None
    step_index: int | None = None
    body_region: str | None = None
    place: str | None = None
    note: str | None = None
    text: str | None = None
    disposition: Disposition | None = None
    help_turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class Opening:
    help_session_id: str
    broadcast_id: str
    now: datetime
    client_opened_at: datetime | None
    opener: Who
    opener_session_id: str
    entry_kind: EntryKind
    entry_ref_id: str | None
    subject_kind: SubjectKind
    subject_person_id: str | None
    subject_display_name: str | None
    place: str | None
    flow_id: str | None
    mission_id: str | None
    origin_mode: OriginMode | None
    environment_id: str | None


def level_for(session: HelpSession, docs: PinnedHelpDocs) -> tuple[Literal["act", "emergency"], str]:
    """I-H6: the level is a pure function of the node and the pinned presentation."""
    presentation = docs.presentation
    if session.node_kind == "flow":
        row = presentation.flow_level(session.node_id)
        if row is not None:
            return row.level, row.basis
    # default_flow_level carries no source in the document; CONTRACTS 11 labels such values team default.
    return presentation.default_flow_level, "team default"


def subject_name(session: HelpSession, docs: PinnedHelpDocs) -> str:
    return session.subject_display_name or docs.ui.unknown


def open_session(opening: Opening, docs: PinnedHelpDocs, limits: HelpLimits) -> HelpSession:
    flows = docs.flows
    start = docs.presentation.start_node_id
    node_kind: NodeKind = "triage"
    node_id = start
    if opening.flow_id is not None:
        if flows.flow(opening.flow_id) is None:
            raise HelpInvalidValue("unknown flow", field="/flow_id")
        node_kind, node_id = "flow", opening.flow_id
    helpers: tuple[Helper, ...] = ()
    opener = opening.opener
    if (
        opener.person_id is not None
        and opening.subject_kind in ("crewmate", "unknown")
        and opener.person_id != opening.subject_person_id
    ):
        # D-E26-1: a person who presses for someone else is the first helper (V71).
        helpers = (Helper(opener.person_id, opener.ref.display_name or docs.ui.unknown, opening.now),)
    session = HelpSession(
        help_session_id=opening.help_session_id,
        revision=1,
        state="open",
        opened_at=opening.now,
        client_opened_at=opening.client_opened_at,
        opened_by=opener.ref,
        entry_kind=opening.entry_kind,
        entry_ref_id=opening.entry_ref_id,
        subject_kind=opening.subject_kind,
        subject_person_id=opening.subject_person_id,
        subject_display_name=opening.subject_display_name,
        place=opening.place,
        body_region=None,
        body_region_source="none",
        node_kind=node_kind,
        node_id=node_id,
        node_revision=1,
        context_epoch=1,
        helpers=helpers,
        path=(),
        log=(),
        ticks=(),
        visited_flows=(node_id,) if node_kind == "flow" else (),
        crew_called_at=None,
        broadcast_id=opening.broadcast_id,
        station_session_ids=(opening.opener_session_id,) if opener.ref.kind == "station" else (),
        assistant=AssistantState("off", None, None, ()),
        turn=None,
        ground_message_ids=(),
        closed_at=None,
        close=None,
        release_id=docs.release_id,
        config_pins=docs.pins,
        mission_id=opening.mission_id,
        origin_mode=opening.origin_mode,
        environment_id=opening.environment_id,
        log_bytes=0,
    )
    session = _derive_region(session, docs)
    text = render(
        docs.presentation.log_template("opened"),
        {"opener_name": opener.ref.display_name, "subject_name": opening.subject_display_name},
        docs.ui.unknown,
    )
    line = LogLine(opening.now, text, opener.ref, "system", opening.client_opened_at, opening.now)
    return replace(session, log=(line,), log_bytes=_bytes(text))


def apply(
    session: HelpSession,
    command: HelpCommand,
    who: Who,
    docs: PinnedHelpDocs,
    limits: HelpLimits,
    *,
    now: datetime,
    if_match: int,
    replayed: bool = False,
) -> HelpSession:
    """Apply one action. Returns the session unchanged for an idempotent repeat."""
    if who.ref.kind == "assistant":
        # AGENTS rule 4: the assistant can never act on a help session (node, ticks, level, close, broadcast).
        raise HelpActionNotAllowed("the assistant cannot act on a help session")
    if not session.is_open:
        raise HelpSessionClosed()
    _check_version(session, command, if_match)
    at = _event_time(session, command.client_time, now)
    _check_caps(session, command.action, limits)
    handler = _HANDLERS[command.action]
    return handler(_Step(session, command, who, docs, limits, now, at, replayed))


@dataclass(frozen=True, slots=True)
class _Step:
    session: HelpSession
    command: HelpCommand
    who: Who
    docs: PinnedHelpDocs
    limits: HelpLimits
    now: datetime
    at: datetime
    replayed: bool

    def line(self, text: str, kind: LogKind, by: ActorRef | None = None) -> LogLine:
        return LogLine(
            self.at, text, self.who.ref if by is None else by, kind, self.command.client_time, self.now
        )

    def words(self, key: str, values: dict[str, str | None]) -> str:
        return render(self.docs.presentation.log_template(key), values, self.docs.ui.unknown)


def _check_version(session: HelpSession, command: HelpCommand, if_match: int) -> None:
    if if_match == session.revision:
        return
    stale = {"revision": session.revision, "node_id": session.node_id, "state": session.state}
    if if_match > session.revision or command.action not in COMMUTING:
        raise HelpStaleVersion(stale)
    if command.action in ("tick_step", "untick_step") and command.flow_id is None:
        # E26 3.2 tick rule 2: an index without its flow may name another flow's step after a node change.
        if if_match < session.node_revision:
            raise HelpStaleVersion(stale)


def _event_time(session: HelpSession, client_time: datetime, now: datetime) -> datetime:
    """Client times are kept as sent; the displayed time is clamped only for ordering (E26 3.2)."""
    floor = session.opened_at
    if session.client_opened_at is not None and session.client_opened_at < floor:
        floor = session.client_opened_at
    if client_time > now:
        return now
    if client_time < floor:
        return floor
    return client_time


def _check_caps(session: HelpSession, action: ActionName, limits: HelpLimits) -> None:
    if session.log_bytes >= limits.log_hard_cap_bytes and action != "close":
        raise HelpLogFull()
    if session.log_bytes >= limits.log_soft_cap_bytes and action in ("add_note", "ask_assistant"):
        raise HelpLogFull()
    if len(session.path) >= limits.path_max_entries and action in ("answer", "switch_flow", "start_over"):
        raise HelpLogFull()


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _commit(step: _Step, line: LogLine, session: HelpSession) -> HelpSession:
    return replace(
        session,
        revision=step.session.revision + 1,
        log=(*session.log, line),
        log_bytes=session.log_bytes + _bytes(line.text),
    )


def _enter(step: _Step, session: HelpSession, kind: NodeKind, node_id: str) -> HelpSession:
    """Move to a node: new node revision and context epoch; turns follow the new context (E26 3.6)."""
    revision = step.session.revision + 1
    epoch = session.context_epoch + 1
    turn: PendingTurn | None = None
    if session.turn is not None and session.turn.state == "held":
        # A question asked at triage waits for a flow; an active turn is cancelled by any context change.
        turn = replace(session.turn, state="requested" if kind == "flow" else "held", epoch=epoch)
    visited = session.visited_flows
    if kind == "flow" and node_id not in visited:
        visited = (*visited, node_id)
    moved = replace(
        session,
        node_kind=kind,
        node_id=node_id,
        node_revision=revision,
        context_epoch=epoch,
        visited_flows=visited,
        turn=turn,
        assistant=_assistant_after_turn_change(session.assistant, session.turn, turn),
    )
    return _derive_region(moved, step.docs)


def _assistant_after_turn_change(
    assistant: AssistantState, before: PendingTurn | None, after: PendingTurn | None
) -> AssistantState:
    if before is not None and before.state in ("requested", "running") and after is None:
        if assistant.status == "answering":
            return replace(assistant, status="waiting")
    return assistant


def _derive_region(session: HelpSession, docs: PinnedHelpDocs) -> HelpSession:
    """I-H17, CONTRACTS 16: a person's choice stands; otherwise the flow's single region or null."""
    if session.body_region_source == "person":
        return session
    if session.node_kind == "triage":
        return replace(session, body_region=None, body_region_source="none")
    flow = docs.flows.flow(session.node_id)
    regions = flow.body_regions if flow is not None else ()
    region = regions[0] if len(regions) == 1 else None
    return replace(session, body_region=region, body_region_source="derived")


def _answer(step: _Step) -> HelpSession:
    session, command = step.session, step.command
    if session.node_kind != "triage" or command.node_id != session.node_id:
        raise HelpInvalidTransition("the session is not at that question")
    node = step.docs.flows.triage_node(session.node_id)
    index = command.answer_index
    if node is None or index is None or not 0 <= index < len(node.answers):
        raise HelpInvalidValue("unknown answer", field="/answer_index")
    answer = node.answers[index]
    template = step.docs.presentation.answer_template(node.id, index)
    text = (
        answer.label
        if template is None
        else render(template, {"subject_name": subject_name(session, step.docs)}, step.docs.ui.unknown)
    )
    entry = PathEntry(
        node.id, "triage", index, answer.label, step.at, step.who.ref, command.client_time, step.replayed
    )
    kind: NodeKind = "flow" if step.docs.flows.flow(answer.goes_to) is not None else "triage"
    moved = _enter(step, replace(session, path=(*session.path, entry)), kind, answer.goes_to)
    return _commit(step, step.line(text, "answer"), moved)


def _switch_flow(step: _Step) -> HelpSession:
    flow_id = step.command.flow_id
    flow = step.docs.flows.flow(flow_id) if flow_id is not None else None
    if flow is None:
        raise HelpInvalidValue("unknown flow", field="/flow_id")
    session = step.session
    entry = PathEntry(
        flow.id, "flow", None, None, step.at, step.who.ref, step.command.client_time, step.replayed
    )
    moved = _enter(step, replace(session, path=(*session.path, entry)), "flow", flow.id)
    text = step.words("flow_switched", {"flow_title": flow.title})
    return _commit(step, step.line(text, "system"), moved)


def _tick_target(step: _Step) -> tuple[FlowDef, int]:
    session, command = step.session, step.command
    flow_id = command.flow_id
    if flow_id is None:
        if session.node_kind != "flow":
            raise HelpInvalidTransition("the session is not at a flow")
        flow_id = session.node_id
    elif flow_id not in session.visited_flows:
        raise HelpInvalidTransition("that flow is not in this session's path")
    flow = step.docs.flows.flow(flow_id)
    index = command.step_index
    if flow is None or index is None or not 0 <= index < len(flow.immediate_steps):
        raise HelpInvalidValue("unknown step", field="/step_index")
    return flow, index


def _tick(step: _Step) -> HelpSession:
    """A late tick older than the step's last untick changes nothing: the newer state stands (AGENTS 6)."""
    flow, index = _tick_target(step)
    session = step.session
    if session.is_ticked(flow.id, index) is not None:
        return session
    unticked = session.unticked_at(flow.id, index)
    if unticked is not None and step.at < unticked:
        return session
    tick = Tick(flow.id, index, step.at, step.who.ref, step.command.client_time)
    ticks = tuple(sorted((*session.ticks, tick), key=lambda t: (t.flow_id, t.step_index)))
    text = step.words("step_done", {"step": flow.immediate_steps[index]})
    return _commit(step, step.line(text, "step"), replace(session, ticks=ticks))


def _untick(step: _Step) -> HelpSession:
    """Event times decide, clamped for ordering (E26 3.2): an untick older than the tick it would clear, such as
    a journaled offline untick delivered after a newer tick, changes nothing (AGENTS 6)."""
    flow, index = _tick_target(step)
    session = step.session
    tick = session.is_ticked(flow.id, index)
    if tick is None or step.at < tick.ticked_at:
        return session
    ticks = tuple(t for t in session.ticks if (t.flow_id, t.step_index) != (flow.id, index))
    marks = tuple(m for m in session.unticks if (m.flow_id, m.step_index) != (flow.id, index))
    unticks = tuple(
        sorted((*marks, Untick(flow.id, index, step.at)), key=lambda m: (m.flow_id, m.step_index))
    )
    text = step.words("step_undone", {"step": flow.immediate_steps[index]})
    return _commit(step, step.line(text, "step"), replace(session, ticks=ticks, unticks=unticks))


def _call_crew(step: _Step) -> HelpSession:
    if step.session.crew_called_at is not None:
        return step.session
    return _commit(
        step, step.line(step.words("crew_called", {}), "crew"), replace(step.session, crew_called_at=step.at)
    )


def _person(step: _Step) -> str:
    person = step.who.person_id
    if person is None or step.who.ref.kind != "person":
        raise HelpActionNotAllowed("this action needs a person session")
    return person


def _join(step: _Step) -> HelpSession:
    person = _person(step)
    session = step.session
    if person == session.subject_person_id:
        raise HelpActionNotAllowed("the subject cannot join as a helper")
    if person in session.helper_ids():
        raise HelpActionNotAllowed("already helping")
    name = step.who.ref.display_name or step.docs.ui.unknown
    helpers = (*session.helpers, Helper(person, name, step.at))
    return _commit(
        step,
        step.line(step.words("joined", {"helper_name": name}), "crew"),
        replace(session, helpers=helpers),
    )


def _leave(step: _Step) -> HelpSession:
    person = _person(step)
    session = step.session
    if person not in session.helper_ids():
        raise HelpActionNotAllowed("not helping")
    name = step.who.ref.display_name or step.docs.ui.unknown
    helpers = tuple(h for h in session.helpers if h.person_id != person)
    return _commit(
        step, step.line(step.words("left", {"helper_name": name}), "crew"), replace(session, helpers=helpers)
    )


def _start_over(step: _Step) -> HelpSession:
    session = step.session
    start = step.docs.presentation.start_node_id
    entry = PathEntry(
        start, "start_over", None, None, step.at, step.who.ref, step.command.client_time, step.replayed
    )
    moved = _enter(step, replace(session, path=(*session.path, entry)), "triage", start)
    return _commit(step, step.line(step.words("started_over", {}), "system"), moved)


def _set_body_region(step: _Step) -> HelpSession:
    region = step.command.body_region
    ui = step.docs.ui
    if region is not None and ui.body_region_label(region) is None:
        raise HelpInvalidValue("unknown body region", field="/body_region")
    label = ui.unknown if region is None else ui.body_region_label(region)
    text = step.words("note", {"note": label})
    return _commit(
        step, step.line(text, "note"), replace(step.session, body_region=region, body_region_source="person")
    )


def _set_place(step: _Step) -> HelpSession:
    place = step.command.place
    if place is None or not 1 <= len(place) <= step.limits.place_max_chars or not place.strip():
        raise HelpInvalidValue("place must be 1 to the configured maximum characters", field="/place")
    return _commit(
        step, step.line(step.words("note", {"note": place}), "note"), replace(step.session, place=place)
    )


def _bounded_text(step: _Step, value: str | None, field: str) -> str:
    if value is None or not 1 <= len(value) <= step.limits.note_max_chars or not value.strip():
        raise HelpInvalidValue("text must be 1 to the configured maximum characters", field=field)
    return value


def _add_note(step: _Step) -> HelpSession:
    note = _bounded_text(step, step.command.note, "/note")
    return _commit(step, step.line(step.words("note", {"note": note}), "note"), step.session)


def _ask_assistant(step: _Step) -> HelpSession:
    _person(step)
    text = _bounded_text(step, step.command.text, "/text")
    turn_id = step.command.help_turn_id
    if turn_id is None:
        raise HelpInvalidValue("turn identifier missing", field="/text")
    session = step.session
    state: TurnState = "requested" if session.node_kind == "flow" else "held"
    turn = PendingTurn(turn_id, step.who.ref, step.at, text, state, session.context_epoch)
    line = step.line(step.words("note", {"note": text}), "assistant")
    return _commit(step, line, replace(session, turn=turn))


def _close(step: _Step) -> HelpSession:
    disposition = step.command.disposition
    if disposition is None:
        raise HelpInvalidValue("disposition required", field="/disposition")
    note = step.command.note
    if note is not None:
        _bounded_text(step, note, "/note")
    word = step.docs.presentation.disposition_word(disposition)
    session = step.session
    return _commit(
        step,
        step.line(step.words("closed", {"disposition": word}), "system"),
        replace(
            session,
            state="closed",
            closed_at=step.at,
            close=CloseInfo(disposition, note, step.who.ref),
            context_epoch=session.context_epoch + 1,
            turn=None,
            assistant=_assistant_after_turn_change(session.assistant, session.turn, None),
        ),
    )


_HANDLERS = {
    "answer": _answer,
    "switch_flow": _switch_flow,
    "tick_step": _tick,
    "untick_step": _untick,
    "call_crew": _call_crew,
    "join": _join,
    "leave": _leave,
    "start_over": _start_over,
    "set_body_region": _set_body_region,
    "set_place": _set_place,
    "add_note": _add_note,
    "ask_assistant": _ask_assistant,
    "close": _close,
}


def allowed_actions(session: HelpSession, who: Who) -> tuple[ActionName, ...]:
    """Computed by the hub for this session; the client never infers them."""
    if not session.is_open or who.ref.kind == "assistant":
        return ()
    person = who.person_id if who.ref.kind == "person" else None
    allowed: set[ActionName] = {
        "switch_flow",
        "call_crew",
        "start_over",
        "set_body_region",
        "set_place",
        "add_note",
        "close",
    }
    if session.node_kind == "triage":
        allowed.add("answer")
    else:
        allowed |= {"tick_step", "untick_step"}
    if person is not None:
        allowed.add("ask_assistant")
        if person in session.helper_ids():
            allowed.add("leave")
        elif person != session.subject_person_id:
            allowed.add("join")
    return tuple(a for a in _ACTION_ORDER if a in allowed)


def attach_station(session: HelpSession, station_session_id: str) -> HelpSession:
    """A station display that pressed for an open session becomes a participant; no state or log change."""
    if station_session_id in session.station_session_ids or not session.is_open:
        return session
    return replace(session, station_session_ids=(*session.station_session_ids, station_session_id))


def append_line(
    session: HelpSession,
    text: str,
    by: ActorRef | None,
    kind: LogKind,
    now: datetime,
    *,
    ground_message_id: str | None = None,
) -> HelpSession:
    """A lifecycle line (ground message queued, sent, acknowledged, reply): revision plus one, one line.

    Lines on a closed session are system lines and never reopen it (E26 3.3, EI5).
    """
    line = LogLine(now, text, by, kind if session.is_open else "system", None, now)
    ids = session.ground_message_ids
    if ground_message_id is not None and ground_message_id not in ids:
        ids = (*ids, ground_message_id)
    return replace(
        session,
        revision=session.revision + 1,
        log=(*session.log, line),
        log_bytes=session.log_bytes + _bytes(text),
        ground_message_ids=ids,
    )
