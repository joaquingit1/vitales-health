"""Pure projections of the help aggregates to their contract shapes (help-session, crew-broadcast, ground-message).

Plain mappings only; the API validates them through the generated models before they leave the hub.
"""

from __future__ import annotations

from datetime import UTC, datetime

from crew_health.help.domain.broadcast import BroadcastView
from crew_health.help.domain.docs import FlowDef, PinnedHelpDocs
from crew_health.help.domain.ground import CatchUpView, GroundMessage
from crew_health.help.domain.session import ActorRef, HelpSession, level_for

type Wire = dict[str, object]


def instant(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def opt_instant(at: datetime | None) -> str | None:
    return instant(at) if at is not None else None


def actor(ref: ActorRef) -> Wire:
    return {"kind": ref.kind, "id": ref.id, "display_name": ref.display_name}


def citation_text(flow: FlowDef, docs: PinnedHelpDocs) -> str:
    """The citation line rendered from source titles; an unknown source stays visibly unknown."""
    titles = [docs.flows.source_title(s) or docs.ui.unknown for s in flow.source_ids]
    return "; ".join(titles) if titles else docs.ui.unknown


def _flow(session: HelpSession, flow: FlowDef, docs: PinnedHelpDocs) -> Wire:
    steps: list[Wire] = []
    for index, text in enumerate(flow.immediate_steps):
        tick = session.is_ticked(flow.id, index)
        steps.append(
            {
                "index": index,
                "text": text,
                "ticked_at": instant(tick.ticked_at) if tick is not None else None,
                "ticked_by": actor(tick.ticked_by) if tick is not None else None,
            }
        )
    return {
        "title": flow.title,
        "plain": flow.plain,
        "immediate_steps": steps,
        "questions": list(flow.questions),
        "measurements": [
            {"what": m.what, "how": m.how, "instrument": m.instrument} for m in flow.measurements
        ],
        "treatment_steps": [{"step": t.step, "who": t.who, "kit": t.kit} for t in flow.treatment_steps],
        "call_ground_when": flow.call_ground_when,
        "environment_note": flow.environment_note(session.environment_id),
        "body_regions": list(flow.body_regions),
        "citation": {"text": citation_text(flow, docs), "source_ids": list(flow.source_ids)},
    }


def _node(session: HelpSession, docs: PinnedHelpDocs) -> Wire:
    if session.node_kind == "triage":
        node = docs.flows.triage_node(session.node_id)
        answers = node.answers if node is not None else ()
        return {
            "kind": "triage",
            "id": session.node_id,
            "question": node.question if node is not None else None,
            "answers": [{"index": i, "label": a.label} for i, a in enumerate(answers)],
            "flow": None,
        }
    flow = docs.flows.flow(session.node_id)
    return {
        "kind": "flow",
        "id": session.node_id,
        "question": None,
        "answers": [],
        "flow": _flow(session, flow, docs) if flow is not None else None,
    }


def help_session(session: HelpSession, docs: PinnedHelpDocs, allowed: tuple[str, ...]) -> Wire:
    level, basis = level_for(session, docs)
    return {
        "help_session_id": session.help_session_id,
        "revision": session.revision,
        "state": session.state,
        "opened_at": instant(session.opened_at),
        "client_opened_at": opt_instant(session.client_opened_at),
        "opened_by": actor(session.opened_by),
        "entry": {"kind": session.entry_kind, "ref_id": session.entry_ref_id},
        "subject": {
            "kind": session.subject_kind,
            "person_id": session.subject_person_id,
            "display_name": session.subject_display_name,
        },
        "place": session.place,
        "body_region": session.body_region,
        "helpers": [
            {"person_id": h.person_id, "display_name": h.display_name, "joined_at": instant(h.joined_at)}
            for h in session.helpers
        ],
        "config_pins": [
            {"name": p.name, "version": p.version} for p in sorted(docs.pins, key=lambda p: p.name)
        ],
        "level": level,
        "level_basis": basis,
        "node": _node(session, docs),
        "path": [
            {
                "node_id": p.node_id,
                "kind": p.kind,
                "answer_index": p.answer_index,
                "answer_label": p.answer_label,
                "at": instant(p.at),
                "by": actor(p.by),
            }
            for p in session.path
        ],
        "log": [
            {
                "at": instant(line.at),
                "text": line.text,
                "by": actor(line.by) if line.by else None,
                "kind": line.kind,
            }
            for line in session.log
        ],
        "crew_called_at": opt_instant(session.crew_called_at),
        "broadcast_id": session.broadcast_id,
        "assistant": {
            "status": session.assistant.status,
            "conversation_id": session.assistant.conversation_id,
            "last_text": session.assistant.last_text,
        },
        "ground_message_ids": list(session.ground_message_ids),
        "fixed_buttons": [{"id": i, "label": label} for i, label in docs.presentation.fixed_buttons],
        "closed_at": opt_instant(session.closed_at),
        "close": (
            {
                "disposition": session.close.disposition,
                "note": session.close.note,
                "by": actor(session.close.by),
            }
            if session.close is not None
            else None
        ),
        "allowed_actions": list(allowed),
    }


def broadcast(view: BroadcastView) -> Wire:
    return {
        "broadcast_id": view.broadcast_id,
        "help_session_id": view.help_session_id,
        "state": view.state,
        "level": view.level,
        "subject": {"person_id": view.subject[0], "display_name": view.subject[1]} if view.subject else None,
        "helpers": [{"person_id": p, "display_name": n} for p, n in view.helpers],
        "roles": [
            {
                "role_assignment_id": r.role_assignment_id,
                "role_id": r.role_id,
                "label": r.label,
                "person": {"person_id": r.person_id, "display_name": r.person_name},
                "state": r.state,
            }
            for r in view.roles
        ],
        "place": view.place,
        "text": view.text,
        "started_at": instant(view.started_at),
        "ended_at": opt_instant(view.ended_at),
    }


def catch_up(view: CatchUpView) -> Wire:
    return {
        "what_happened": list(view.what_happened),
        "what_was_done": list(view.what_was_done),
        "being_watched": list(view.being_watched),
        "advice_welcome": list(view.advice_welcome),
    }


def ground_message(
    message: GroundMessage,
    *,
    status: str,
    leaves: datetime | None,
    catch: CatchUpView,
    allowed: tuple[str, ...],
) -> Wire:
    reply = message.reply
    return {
        "ground_message_id": message.ground_message_id,
        "revision": message.revision,
        "help_session_id": message.help_session_id,
        "state": message.state,
        "lines": list(message.lines),
        "text": message.text,
        "queued_by": actor(message.queued_by),
        "queued_at": instant(message.queued_at),
        "sent_at": opt_instant(message.sent_at),
        "expected_arrival_at": opt_instant(message.expected_arrival_at),
        "acknowledged_at": opt_instant(message.acknowledged_at),
        "one_way_delay_s": message.one_way_delay_s,
        "status_text": status,
        "simulated": message.simulated,
        "reply": {"text": reply.text, "by": actor(reply.by), "at": instant(reply.at)}
        if reply is not None
        else None,
        "leaves_at": opt_instant(leaves),
        "catch_up": catch_up(catch),
        "session_revision": message.session_revision,
        "text_sha256": message.text_sha256,
        "allowed_actions": list(allowed),
    }
