"""Assistant turns and runtime status on a help session (E26 3.6).

Every function here may change only `assistant`, `turn`, `log`, `log_bytes` and `revision`. It never touches the
node, the path, the ticks, the level, the helpers, the broadcast or the session state (AGENTS rule 4); the property
tests in hub/tests/help assert this for every input.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal

from crew_health.help.domain.docs import PinnedHelpDocs
from crew_health.help.domain.render import render
from crew_health.help.domain.session import ActorRef, AssistantStatus, HelpSession, LogLine

TurnOutcome = Literal["applied", "stale", "late"]
StartStatus = Literal["waiting", "stopped", "unavailable"]

ASSISTANT_ACTOR = ActorRef("assistant", "assistant", None)


def _bump(session: HelpSession, line: LogLine | None) -> HelpSession:
    if line is None:
        return replace(session, revision=session.revision + 1)
    return replace(
        session,
        revision=session.revision + 1,
        log=(*session.log, line),
        log_bytes=session.log_bytes + len(line.text.encode("utf-8")),
    )


def runtime_status(
    session: HelpSession, status: AssistantStatus, docs: PinnedHelpDocs, now: datetime
) -> HelpSession:
    """Apply a runtime status; `stopped` logs the configured stopped text once per transition."""
    if not session.is_open or session.assistant.status == status:
        return session
    turn = session.turn
    if status in ("stopped", "unavailable") and turn is not None and turn.state == "running":
        turn = None
    changed = replace(session, assistant=replace(session.assistant, status=status), turn=turn)
    line = None
    if status == "stopped":
        line = LogLine(now, docs.presentation.assistant_stopped_text, None, "assistant", None, now)
    return _bump(changed, line)


def turn_started(
    session: HelpSession,
    help_turn_id: str,
    status: StartStatus,
    conversation_id: str | None,
    docs: PinnedHelpDocs,
    now: datetime,
) -> HelpSession:
    """Result of `start_help_turn` after commit. A turn no longer requested is left alone."""
    turn = session.turn
    if not session.is_open or turn is None or turn.help_turn_id != help_turn_id or turn.state != "requested":
        return session
    if status == "waiting":
        assistant = replace(session.assistant, status="answering", conversation_id=conversation_id)
        return _bump(replace(session, turn=replace(turn, state="running"), assistant=assistant), None)
    return runtime_status(replace(session, turn=None), status, docs, now)


def turn_failed(session: HelpSession, help_turn_id: str, now: datetime) -> HelpSession:
    turn = session.turn
    if not session.is_open or turn is None or turn.help_turn_id != help_turn_id:
        return session
    return _bump(
        replace(session, turn=None, assistant=replace(session.assistant, status="unavailable")), None
    )


def turn_completed(
    session: HelpSession,
    help_turn_id: str,
    epoch: int,
    display_text: str | None,
    citations: tuple[str, ...],
    docs: PinnedHelpDocs,
    now: datetime,
) -> tuple[HelpSession, TurnOutcome]:
    """Show a guarded turn result. `display_text` comes only from the guard (configured texts), never the model."""
    turn = session.turn
    if turn is None or turn.help_turn_id != help_turn_id or turn.state not in ("requested", "running"):
        return session, "late"
    if not session.is_open or turn.epoch != epoch or session.context_epoch != epoch:
        return session, "stale"
    assistant = replace(session.assistant, status="waiting")
    line = None
    if display_text is not None:
        assistant = replace(assistant, last_text=display_text, citations=citations)
        text = render(docs.presentation.log_template("assistant"), {"text": display_text}, docs.ui.unknown)
        line = LogLine(now, text, ASSISTANT_ACTOR, "assistant", None, now)
    return _bump(replace(session, turn=None, assistant=assistant), line), "applied"
