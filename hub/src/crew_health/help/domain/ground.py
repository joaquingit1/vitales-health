"""The message for the ground (CONTRACTS 16 "Message for the ground", CONTRACTS 26, E26 3.3).

`build_snapshot` and `compose_lines` are shared by the preview and the press, so the queued text equals the preview
at the same revision and pins. The lifecycle applies incoming events from a durable ledger keyed by event ID: a
known event is a duplicate, an event whose precondition does not hold yet is buffered, nothing is discarded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from crew_health.help.domain.broadcast import helping_text
from crew_health.help.domain.docs import ConfigPin, PinnedHelpDocs
from crew_health.help.domain.errors import HelpInvalidValue
from crew_health.help.domain.render import render
from crew_health.help.domain.session import ActorRef, HelpSession, subject_name

GroundState = Literal["queued", "sent", "acknowledged"]
EventKind = Literal["sent", "arrived", "receipt", "reply"]
EventOutcome = Literal["applied", "duplicate", "buffered"]
LinkStatus = Literal["up", "delayed", "down", "unknown"]
_DAY_SECONDS = 86400


def mission_hhmm(at: datetime, epoch: datetime | None) -> str | None:
    """Mission time of day, (t - epoch) mod 86,400 s as HH:MM (CONTRACTS 8); None when the clock is unknown."""
    if epoch is None:
        return None
    seconds = int((at - epoch).total_seconds()) % _DAY_SECONDS
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


@dataclass(frozen=True, slots=True)
class TickLine:
    flow_id: str
    index: int
    text: str
    ticked_at: datetime
    time_text: str | None


@dataclass(frozen=True, slots=True)
class HelpLogSnapshot:
    help_session_id: str
    session_revision: int
    subject_name: str
    place: str | None
    flow_id: str | None
    flow_title: str | None
    answer_lines: tuple[str, ...]
    ticks: tuple[TickLine, ...]
    crew_called_at: datetime | None
    crew_time_text: str | None
    helping: str
    pins: tuple[ConfigPin, ...]
    environment_id: str | None
    queued_at: datetime


def build_snapshot(
    session: HelpSession, docs: PinnedHelpDocs, *, at: datetime, epoch: datetime | None
) -> HelpLogSnapshot:
    flow = docs.flows.flow(session.node_id) if session.node_kind == "flow" else None
    order = {flow_id: i for i, flow_id in enumerate(session.visited_flows)}
    ticks: list[TickLine] = []
    for tick in sorted(session.ticks, key=lambda t: (order.get(t.flow_id, len(order)), t.step_index)):
        tick_flow = docs.flows.flow(tick.flow_id)
        if tick_flow is not None and tick.step_index < len(tick_flow.immediate_steps):
            text = tick_flow.immediate_steps[tick.step_index]
            ticks.append(
                TickLine(
                    tick.flow_id, tick.step_index, text, tick.ticked_at, mission_hhmm(tick.ticked_at, epoch)
                )
            )
    called = session.crew_called_at
    return HelpLogSnapshot(
        help_session_id=session.help_session_id,
        session_revision=session.revision,
        subject_name=subject_name(session, docs),
        place=session.place,
        flow_id=flow.id if flow is not None else None,
        flow_title=flow.title if flow is not None else None,
        answer_lines=tuple(line.text for line in session.log if line.kind == "answer"),
        ticks=tuple(ticks),
        crew_called_at=called,
        crew_time_text=mission_hhmm(called, epoch) if called is not None else None,
        helping=helping_text(session, docs),
        pins=docs.pins,
        environment_id=session.environment_id,
        queued_at=at,
    )


def header_and_answers(snapshot: HelpLogSnapshot, docs: PinnedHelpDocs) -> tuple[str, ...]:
    ground, unknown = docs.presentation.ground, docs.ui.unknown
    header = render(
        ground.header,
        {"subject_name": snapshot.subject_name, "place": snapshot.place, "flow_title": snapshot.flow_title},
        unknown,
    )
    answers = ". ".join(line.rstrip(".") for line in snapshot.answer_lines) or None
    return header, render(ground.answers, {"answers": answers}, unknown)


def crew_line(snapshot: HelpLogSnapshot, docs: PinnedHelpDocs) -> str | None:
    if snapshot.crew_called_at is None:
        return None
    values = {"time": snapshot.crew_time_text, "helping": snapshot.helping}
    return render(docs.presentation.ground.crew, values, docs.ui.unknown)


def compose_lines(snapshot: HelpLogSnapshot, docs: PinnedHelpDocs, extra_note: str | None) -> tuple[str, ...]:
    """Header; answers; ticked steps (only when one is ticked); crew line (only when called); extra note."""
    lines = list(header_and_answers(snapshot, docs))
    if snapshot.ticks:
        steps = " ".join(t.text for t in snapshot.ticks)
        lines.append(render(docs.presentation.ground.steps, {"steps": steps}, docs.ui.unknown))
    crew = crew_line(snapshot, docs)
    if crew is not None:
        lines.append(crew)
    if extra_note is not None:
        if not extra_note.strip():
            raise HelpInvalidValue("extra note is empty", field="/extra_note")
        lines.append(extra_note)
    return tuple(lines)


def text_of(lines: tuple[str, ...]) -> tuple[str, str]:
    text = " ".join(lines)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CatchUpView:
    what_happened: tuple[str, ...]
    what_was_done: tuple[str, ...]
    being_watched: tuple[str, ...]
    advice_welcome: tuple[str, ...]


def fallback_catch_up(snapshot: HelpLogSnapshot, docs: PinnedHelpDocs) -> CatchUpView:
    """Used when E29 is absent or fails: known care is never erased; unknown facts stay unknown (SYSTEM 8.7)."""
    unknown = docs.ui.unknown
    done = [
        render(docs.ui.step_done_line, {"time": t.time_text, "step": t.text}, unknown) for t in snapshot.ticks
    ]
    crew = crew_line(snapshot, docs)
    if crew is not None:
        done.append(crew)
    return CatchUpView(header_and_answers(snapshot, docs), tuple(done), (unknown,), (unknown,))


@dataclass(frozen=True, slots=True)
class GroundReply:
    event_id: str
    text: str
    by: ActorRef
    at: datetime


@dataclass(frozen=True, slots=True)
class GroundEvent:
    event_id: str
    kind: EventKind
    occurred_at: datetime
    delay_s: float | None = None
    simulated: bool | None = None
    arrived_at: datetime | None = None
    text: str | None = None
    by: ActorRef | None = None


@dataclass(frozen=True, slots=True)
class GroundMessage:
    ground_message_id: str
    revision: int
    help_session_id: str
    state: GroundState
    lines: tuple[str, ...]
    text: str
    text_sha256: str
    queued_by: ActorRef
    queued_at: datetime
    sent_at: datetime | None
    expected_arrival_at: datetime | None
    acknowledged_at: datetime | None
    arrived_at: datetime | None
    one_way_delay_s: float | None
    simulated: bool
    replies: tuple[GroundReply, ...]
    session_revision: int
    snapshot: HelpLogSnapshot
    previewed_sha256: str | None
    event_ids: tuple[str, ...]
    buffered: tuple[GroundEvent, ...]

    @property
    def reply(self) -> GroundReply | None:
        if not self.replies:
            return None
        return max(self.replies, key=lambda r: (r.at, r.event_id))


SessionLine = tuple[str, str | None]


def _ready(message: GroundMessage, event: GroundEvent) -> bool:
    if event.kind == "sent":
        return message.state == "queued"
    if event.kind == "receipt":
        return message.state in ("sent", "acknowledged")
    return message.state in ("sent", "acknowledged")


def _apply_one(message: GroundMessage, event: GroundEvent) -> tuple[GroundMessage, list[SessionLine]]:
    lines: list[SessionLine] = []
    arrived = message.arrived_at or event.arrived_at
    if event.kind == "sent":
        delay = event.delay_s
        expected = event.occurred_at + timedelta(seconds=delay) if delay is not None else None
        simulated = message.simulated if event.simulated is None else event.simulated
        message = replace(
            message,
            state="sent",
            sent_at=event.occurred_at,
            expected_arrival_at=expected,
            one_way_delay_s=delay,
            simulated=simulated,
        )
        lines.append(("ground_message_sent", None))
    elif event.kind == "arrived":
        arrived = message.arrived_at or event.occurred_at
    elif event.kind == "receipt":
        if message.state == "sent":
            message = replace(message, state="acknowledged", acknowledged_at=event.occurred_at)
            lines.append(("ground_message_acknowledged", None))
    elif event.text is not None and event.by is not None:
        message = replace(
            message,
            replies=(*message.replies, GroundReply(event.event_id, event.text, event.by, event.occurred_at)),
        )
        lines.append(("ground_reply", event.text))
    return replace(message, arrived_at=arrived), lines


def apply_event(
    message: GroundMessage, event: GroundEvent, *, max_events: int
) -> tuple[GroundMessage, EventOutcome, list[SessionLine]]:
    """Apply one incoming event, then any buffered events whose precondition now holds, in (time, id) order."""
    if event.event_id in message.event_ids:
        return message, "duplicate", []
    if len(message.event_ids) >= max_events:
        raise HelpInvalidValue("too many events for this message", field="/event_id", code="LIMIT_EXCEEDED")
    if event.kind == "reply" and (event.text is None or event.by is None):
        raise HelpInvalidValue("a reply needs its text and author", field="/text")
    message = replace(message, event_ids=(*message.event_ids, event.event_id), revision=message.revision + 1)
    if not _ready(message, event):
        buffered = tuple(sorted((*message.buffered, event), key=lambda e: (e.occurred_at, e.event_id)))
        return replace(message, buffered=buffered), "buffered", []
    message, lines = _apply_one(message, event)
    pending = list(message.buffered)
    progressed = True
    while progressed and pending:
        progressed = False
        for waiting in pending:
            if _ready(message, waiting):
                message, more = _apply_one(message, waiting)
                lines.extend(more)
                pending.remove(waiting)
                progressed = True
                break
    return replace(message, buffered=tuple(pending)), "applied", lines


@dataclass(frozen=True, slots=True)
class LinkFacts:
    status: LinkStatus
    next_contact_at: datetime | None


def leaves_at(message_state: GroundState, link: LinkFacts) -> datetime | None:
    if message_state == "queued" and link.status == "down":
        return link.next_contact_at
    return None


def in_time_text(now: datetime, until: datetime | None, docs: PinnedHelpDocs) -> str | None:
    """CONTRACTS 26: whole hours rounded down when at least one hour, else whole minutes rounded down."""
    if until is None:
        return None
    seconds = max(0, int((until - now).total_seconds()))
    unit, n = ("hours", seconds // 3600) if seconds >= 3600 else ("minutes", seconds // 60)
    template = docs.ui.duration_word(unit)
    return render(template, {"n": str(n)}, docs.ui.unknown) if template is not None else None


def delay_text(delay_s: float | None, docs: PinnedHelpDocs) -> str | None:
    """CONTRACTS 11: the largest unit that gives a whole number."""
    if delay_s is None or delay_s != int(delay_s):
        return None
    seconds = int(delay_s)
    unit, n = "seconds", seconds
    if seconds and seconds % 3600 == 0:
        unit, n = "hours", seconds // 3600
    elif seconds and seconds % 60 == 0:
        unit, n = "minutes", seconds // 60
    template = docs.ui.duration_word(unit)
    return render(template, {"n": str(n)}, docs.ui.unknown) if template is not None else None


def status_text(
    message: GroundMessage, link: LinkFacts, docs: PinnedHelpDocs, *, now: datetime, epoch: datetime | None
) -> str:
    ground, unknown = docs.presentation.ground, docs.ui.unknown
    if message.state == "acknowledged" and message.acknowledged_at is not None:
        return render(ground.acknowledged, {"time": mission_hhmm(message.acknowledged_at, epoch)}, unknown)
    if message.state == "sent" and message.sent_at is not None:
        values = {
            "time": mission_hhmm(message.sent_at, epoch),
            "delay": delay_text(message.one_way_delay_s, docs),
        }
        return render(ground.sent, values, unknown)
    if link.status == "down" and ground.waiting is not None:
        return render(ground.waiting, {"in_time": in_time_text(now, link.next_contact_at, docs)}, unknown)
    return render(ground.queued, {"time": mission_hhmm(message.queued_at, epoch)}, unknown)
