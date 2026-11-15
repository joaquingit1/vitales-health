"""Explicit JSON codec for the help aggregates. Every stored field is written and read back by name."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast, get_args

from crew_health.help.domain.broadcast import BroadcastRole, BroadcastView
from crew_health.help.domain.docs import ConfigPin
from crew_health.help.domain.errors import HelpStorageFault
from crew_health.help.domain.ground import (
    EventKind,
    GroundEvent,
    GroundMessage,
    GroundReply,
    GroundState,
    HelpLogSnapshot,
    TickLine,
)
from crew_health.help.domain.session import (
    ActorKind,
    ActorRef,
    AssistantState,
    AssistantStatus,
    CloseInfo,
    Disposition,
    EntryKind,
    Helper,
    HelpSession,
    LogKind,
    LogLine,
    NodeKind,
    OriginMode,
    PathEntry,
    PathKind,
    PendingTurn,
    RegionSource,
    SubjectKind,
    Tick,
    TurnState,
    Untick,
)

type J = dict[str, object]

_ACTOR_KINDS: tuple[ActorKind, ...] = get_args(ActorKind)
_SESSION_STATES: tuple[Literal["open", "closed"], ...] = ("open", "closed")
_ENTRY_KINDS: tuple[EntryKind, ...] = get_args(EntryKind)
_SUBJECT_KINDS: tuple[SubjectKind, ...] = get_args(SubjectKind)
_REGION_SOURCES: tuple[RegionSource, ...] = get_args(RegionSource)
_NODE_KINDS: tuple[NodeKind, ...] = get_args(NodeKind)
_PATH_KINDS: tuple[PathKind, ...] = get_args(PathKind)
_LOG_KINDS: tuple[LogKind, ...] = get_args(LogKind)
_ASSISTANT_STATUSES: tuple[AssistantStatus, ...] = get_args(AssistantStatus)
_TURN_STATES: tuple[TurnState, ...] = get_args(TurnState)
_DISPOSITIONS: tuple[Disposition, ...] = get_args(Disposition)
_ORIGIN_MODES: tuple[OriginMode, ...] = get_args(OriginMode)
_EVENT_KINDS: tuple[EventKind, ...] = get_args(EventKind)
_GROUND_STATES: tuple[GroundState, ...] = get_args(GroundState)
_BROADCAST_STATES: tuple[Literal["active", "ended"], ...] = ("active", "ended")
_LEVELS: tuple[Literal["act", "emergency"], ...] = ("act", "emergency")
_ROLE_STATES: tuple[Literal["proposed", "accepted", "changed"], ...] = ("proposed", "accepted", "changed")


def _one_of[T: str](value: object, allowed: tuple[T, ...]) -> T:
    """A stored enum value read back as its type; anything else is a storage fault, never a guess."""
    for option in allowed:
        if value == option:
            return option
    raise HelpStorageFault("stored help value outside its enum")


def _opt_one_of[T: str](value: object, allowed: tuple[T, ...]) -> T | None:
    return None if value is None else _one_of(value, allowed)


def _t(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: object) -> datetime:
    return datetime.fromisoformat(cast(str, value))


def _odt(value: object) -> datetime | None:
    return _dt(value) if value is not None else None


def _os(value: object) -> str | None:
    return cast(str, value) if value is not None else None


def _list(value: object) -> list[J]:
    return cast(list[J], value)


def actor(ref: ActorRef) -> J:
    return {"kind": ref.kind, "id": ref.id, "name": ref.display_name}


def actor_from(raw: object) -> ActorRef:
    m = cast(J, raw)
    return ActorRef(_one_of(m["kind"], _ACTOR_KINDS), cast(str, m["id"]), _os(m["name"]))


def _oactor(raw: object) -> ActorRef | None:
    return actor_from(raw) if raw is not None else None


def session_to_json(s: HelpSession) -> str:
    data: J = {
        "id": s.help_session_id,
        "revision": s.revision,
        "state": s.state,
        "opened_at": _t(s.opened_at),
        "client_opened_at": _t(s.client_opened_at),
        "opened_by": actor(s.opened_by),
        "entry_kind": s.entry_kind,
        "entry_ref_id": s.entry_ref_id,
        "subject_kind": s.subject_kind,
        "subject_person_id": s.subject_person_id,
        "subject_display_name": s.subject_display_name,
        "place": s.place,
        "body_region": s.body_region,
        "body_region_source": s.body_region_source,
        "node_kind": s.node_kind,
        "node_id": s.node_id,
        "node_revision": s.node_revision,
        "context_epoch": s.context_epoch,
        "helpers": [
            {"person_id": h.person_id, "name": h.display_name, "at": _t(h.joined_at)} for h in s.helpers
        ],
        "path": [
            {
                "node_id": p.node_id,
                "kind": p.kind,
                "index": p.answer_index,
                "label": p.answer_label,
                "at": _t(p.at),
                "by": actor(p.by),
                "client_at": _t(p.client_at),
                "replayed": p.replayed,
            }
            for p in s.path
        ],
        "log": [
            {
                "at": _t(x.at),
                "text": x.text,
                "by": actor(x.by) if x.by else None,
                "kind": x.kind,
                "client_at": _t(x.client_at),
                "hub_at": _t(x.hub_at),
            }
            for x in s.log
        ],
        "ticks": [
            {
                "flow_id": k.flow_id,
                "index": k.step_index,
                "at": _t(k.ticked_at),
                "by": actor(k.ticked_by),
                "client_at": _t(k.client_at),
            }
            for k in s.ticks
        ],
        "unticks": [{"flow_id": u.flow_id, "index": u.step_index, "at": _t(u.at)} for u in s.unticks],
        "visited_flows": list(s.visited_flows),
        "crew_called_at": _t(s.crew_called_at),
        "broadcast_id": s.broadcast_id,
        "station_session_ids": list(s.station_session_ids),
        "assistant": {
            "status": s.assistant.status,
            "conversation_id": s.assistant.conversation_id,
            "last_text": s.assistant.last_text,
            "citations": list(s.assistant.citations),
        },
        "turn": None
        if s.turn is None
        else {
            "id": s.turn.help_turn_id,
            "by": actor(s.turn.asked_by),
            "at": _t(s.turn.asked_at),
            "text": s.turn.text,
            "state": s.turn.state,
            "epoch": s.turn.epoch,
        },
        "ground_message_ids": list(s.ground_message_ids),
        "closed_at": _t(s.closed_at),
        "close": None
        if s.close is None
        else {"disposition": s.close.disposition, "note": s.close.note, "by": actor(s.close.by)},
        "release_id": s.release_id,
        "pins": [[p.name, p.version] for p in s.config_pins],
        "mission_id": s.mission_id,
        "origin_mode": s.origin_mode,
        "environment_id": s.environment_id,
        "log_bytes": s.log_bytes,
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def session_from_json(raw: str) -> HelpSession:
    m = cast(J, json.loads(raw))
    turn = cast(J | None, m["turn"])
    close = cast(J | None, m["close"])
    assistant = cast(J, m["assistant"])
    return HelpSession(
        help_session_id=cast(str, m["id"]),
        revision=cast(int, m["revision"]),
        state=_one_of(m["state"], _SESSION_STATES),
        opened_at=_dt(m["opened_at"]),
        client_opened_at=_odt(m["client_opened_at"]),
        opened_by=actor_from(m["opened_by"]),
        entry_kind=_one_of(m["entry_kind"], _ENTRY_KINDS),
        entry_ref_id=_os(m["entry_ref_id"]),
        subject_kind=_one_of(m["subject_kind"], _SUBJECT_KINDS),
        subject_person_id=_os(m["subject_person_id"]),
        subject_display_name=_os(m["subject_display_name"]),
        place=_os(m["place"]),
        body_region=_os(m["body_region"]),
        body_region_source=_one_of(m["body_region_source"], _REGION_SOURCES),
        node_kind=_one_of(m["node_kind"], _NODE_KINDS),
        node_id=cast(str, m["node_id"]),
        node_revision=cast(int, m["node_revision"]),
        context_epoch=cast(int, m["context_epoch"]),
        helpers=tuple(
            Helper(cast(str, h["person_id"]), cast(str, h["name"]), _dt(h["at"])) for h in _list(m["helpers"])
        ),
        path=tuple(
            PathEntry(
                cast(str, p["node_id"]),
                _one_of(p["kind"], _PATH_KINDS),
                cast(int | None, p["index"]),
                _os(p["label"]),
                _dt(p["at"]),
                actor_from(p["by"]),
                _odt(p["client_at"]),
                cast(bool, p["replayed"]),
            )
            for p in _list(m["path"])
        ),
        log=tuple(
            LogLine(
                _dt(x["at"]),
                cast(str, x["text"]),
                _oactor(x["by"]),
                _one_of(x["kind"], _LOG_KINDS),
                _odt(x["client_at"]),
                _dt(x["hub_at"]),
            )
            for x in _list(m["log"])
        ),
        ticks=tuple(
            Tick(
                cast(str, k["flow_id"]),
                cast(int, k["index"]),
                _dt(k["at"]),
                actor_from(k["by"]),
                _odt(k["client_at"]),
            )
            for k in _list(m["ticks"])
        ),
        unticks=tuple(
            Untick(cast(str, u["flow_id"]), cast(int, u["index"]), _dt(u["at"]))
            for u in _list(m.get("unticks", []))
        ),
        visited_flows=tuple(cast(list[str], m["visited_flows"])),
        crew_called_at=_odt(m["crew_called_at"]),
        broadcast_id=cast(str, m["broadcast_id"]),
        station_session_ids=tuple(cast(list[str], m["station_session_ids"])),
        assistant=AssistantState(
            _one_of(assistant["status"], _ASSISTANT_STATUSES),
            _os(assistant["conversation_id"]),
            _os(assistant["last_text"]),
            tuple(cast(list[str], assistant["citations"])),
        ),
        turn=None
        if turn is None
        else PendingTurn(
            cast(str, turn["id"]),
            actor_from(turn["by"]),
            _dt(turn["at"]),
            cast(str, turn["text"]),
            _one_of(turn["state"], _TURN_STATES),
            cast(int, turn["epoch"]),
        ),
        ground_message_ids=tuple(cast(list[str], m["ground_message_ids"])),
        closed_at=_odt(m["closed_at"]),
        close=None
        if close is None
        else CloseInfo(
            _one_of(close["disposition"], _DISPOSITIONS), _os(close["note"]), actor_from(close["by"])
        ),
        release_id=cast(str, m["release_id"]),
        config_pins=tuple(ConfigPin(n, v) for n, v in cast(list[list[str]], m["pins"])),
        mission_id=_os(m["mission_id"]),
        origin_mode=_opt_one_of(m["origin_mode"], _ORIGIN_MODES),
        environment_id=_os(m["environment_id"]),
        log_bytes=cast(int, m["log_bytes"]),
    )


def snapshot_to_dict(s: HelpLogSnapshot) -> J:
    return {
        "help_session_id": s.help_session_id,
        "session_revision": s.session_revision,
        "subject_name": s.subject_name,
        "place": s.place,
        "flow_id": s.flow_id,
        "flow_title": s.flow_title,
        "answer_lines": list(s.answer_lines),
        "ticks": [
            {
                "flow_id": t.flow_id,
                "index": t.index,
                "text": t.text,
                "at": _t(t.ticked_at),
                "time": t.time_text,
            }
            for t in s.ticks
        ],
        "crew_called_at": _t(s.crew_called_at),
        "crew_time_text": s.crew_time_text,
        "helping": s.helping,
        "pins": [[p.name, p.version] for p in s.pins],
        "environment_id": s.environment_id,
        "queued_at": _t(s.queued_at),
    }


def snapshot_from_dict(m: Mapping[str, object]) -> HelpLogSnapshot:
    return HelpLogSnapshot(
        help_session_id=cast(str, m["help_session_id"]),
        session_revision=cast(int, m["session_revision"]),
        subject_name=cast(str, m["subject_name"]),
        place=_os(m["place"]),
        flow_id=_os(m["flow_id"]),
        flow_title=_os(m["flow_title"]),
        answer_lines=tuple(cast(list[str], m["answer_lines"])),
        ticks=tuple(
            TickLine(
                cast(str, t["flow_id"]),
                cast(int, t["index"]),
                cast(str, t["text"]),
                _dt(t["at"]),
                _os(t["time"]),
            )
            for t in _list(m["ticks"])
        ),
        crew_called_at=_odt(m["crew_called_at"]),
        crew_time_text=_os(m["crew_time_text"]),
        helping=cast(str, m["helping"]),
        pins=tuple(ConfigPin(n, v) for n, v in cast(list[list[str]], m["pins"])),
        environment_id=_os(m["environment_id"]),
        queued_at=_dt(m["queued_at"]),
    )


def _event(e: GroundEvent) -> J:
    return {
        "id": e.event_id,
        "kind": e.kind,
        "at": _t(e.occurred_at),
        "delay_s": e.delay_s,
        "simulated": e.simulated,
        "arrived_at": _t(e.arrived_at),
        "text": e.text,
        "by": actor(e.by) if e.by else None,
    }


def _event_from(m: J) -> GroundEvent:
    return GroundEvent(
        cast(str, m["id"]),
        _one_of(m["kind"], _EVENT_KINDS),
        _dt(m["at"]),
        cast(float | None, m["delay_s"]),
        cast(bool | None, m["simulated"]),
        _odt(m["arrived_at"]),
        _os(m["text"]),
        _oactor(m["by"]),
    )


def message_to_json(g: GroundMessage) -> str:
    data: J = {
        "id": g.ground_message_id,
        "revision": g.revision,
        "help_session_id": g.help_session_id,
        "state": g.state,
        "lines": list(g.lines),
        "text": g.text,
        "sha": g.text_sha256,
        "queued_by": actor(g.queued_by),
        "queued_at": _t(g.queued_at),
        "sent_at": _t(g.sent_at),
        "expected_arrival_at": _t(g.expected_arrival_at),
        "acknowledged_at": _t(g.acknowledged_at),
        "arrived_at": _t(g.arrived_at),
        "delay_s": g.one_way_delay_s,
        "simulated": g.simulated,
        "replies": [
            {"event_id": r.event_id, "text": r.text, "by": actor(r.by), "at": _t(r.at)} for r in g.replies
        ],
        "session_revision": g.session_revision,
        "snapshot": snapshot_to_dict(g.snapshot),
        "previewed_sha256": g.previewed_sha256,
        "event_ids": list(g.event_ids),
        "buffered": [_event(e) for e in g.buffered],
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def message_from_json(raw: str) -> GroundMessage:
    m = cast(J, json.loads(raw))
    return GroundMessage(
        ground_message_id=cast(str, m["id"]),
        revision=cast(int, m["revision"]),
        help_session_id=cast(str, m["help_session_id"]),
        state=_one_of(m["state"], _GROUND_STATES),
        lines=tuple(cast(list[str], m["lines"])),
        text=cast(str, m["text"]),
        text_sha256=cast(str, m["sha"]),
        queued_by=actor_from(m["queued_by"]),
        queued_at=_dt(m["queued_at"]),
        sent_at=_odt(m["sent_at"]),
        expected_arrival_at=_odt(m["expected_arrival_at"]),
        acknowledged_at=_odt(m["acknowledged_at"]),
        arrived_at=_odt(m["arrived_at"]),
        one_way_delay_s=cast(float | None, m["delay_s"]),
        simulated=cast(bool, m["simulated"]),
        replies=tuple(
            GroundReply(cast(str, r["event_id"]), cast(str, r["text"]), actor_from(r["by"]), _dt(r["at"]))
            for r in _list(m["replies"])
        ),
        session_revision=cast(int, m["session_revision"]),
        snapshot=snapshot_from_dict(cast(J, m["snapshot"])),
        previewed_sha256=_os(m["previewed_sha256"]),
        event_ids=tuple(cast(list[str], m["event_ids"])),
        buffered=tuple(_event_from(e) for e in _list(m["buffered"])),
    )


def broadcast_to_json(b: BroadcastView) -> str:
    data: J = {
        "broadcast_id": b.broadcast_id,
        "help_session_id": b.help_session_id,
        "state": b.state,
        "level": b.level,
        "subject": list(b.subject) if b.subject else None,
        "helpers": [list(h) for h in b.helpers],
        "roles": [
            [r.role_assignment_id, r.role_id, r.label, r.person_id, r.person_name, r.state] for r in b.roles
        ],
        "place": b.place,
        "text": b.text,
        "started_at": _t(b.started_at),
        "ended_at": _t(b.ended_at),
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def broadcast_from_json(raw: str) -> BroadcastView:
    m = cast(J, json.loads(raw))
    subject = cast(list[str] | None, m["subject"])
    return BroadcastView(
        broadcast_id=cast(str, m["broadcast_id"]),
        help_session_id=cast(str, m["help_session_id"]),
        state=_one_of(m["state"], _BROADCAST_STATES),
        level=_one_of(m["level"], _LEVELS),
        subject=(subject[0], subject[1]) if subject else None,
        helpers=tuple((h[0], h[1]) for h in cast(list[list[str]], m["helpers"])),
        roles=tuple(
            BroadcastRole(r[0], r[1], r[2], r[3], r[4], _one_of(r[5], _ROLE_STATES))
            for r in cast(list[list[str]], m["roles"])
        ),
        place=_os(m["place"]),
        text=cast(str, m["text"]),
        started_at=_dt(m["started_at"]),
        ended_at=_odt(m["ended_at"]),
    )
