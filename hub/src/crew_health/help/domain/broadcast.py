"""Crew broadcast composition (CONTRACTS 16 "Crew broadcast and readings panel", E26 3.4).

The broadcast never carries readings or any clinical field (V71): subject name, helpers, roles, place, level and
the configured text only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.help.domain.docs import PinnedHelpDocs
from crew_health.help.domain.render import join_names, render
from crew_health.help.domain.session import HelpSession, level_for, subject_name

# R-E26-7: ui-copy has no list separator yet; the interim is this plain separator, not content.
NAME_SEPARATOR = ", "


@dataclass(frozen=True, slots=True)
class BroadcastRole:
    role_assignment_id: str
    role_id: str
    label: str
    person_id: str
    person_name: str
    state: Literal["proposed", "accepted", "changed"]


@dataclass(frozen=True, slots=True)
class BroadcastView:
    broadcast_id: str
    help_session_id: str
    state: Literal["active", "ended"]
    level: Literal["act", "emergency"]
    subject: tuple[str, str] | None
    helpers: tuple[tuple[str, str], ...]
    roles: tuple[BroadcastRole, ...]
    place: str | None
    text: str
    started_at: datetime
    ended_at: datetime | None


def helping_text(session: HelpSession, docs: PinnedHelpDocs) -> str:
    names = join_names(tuple(h.display_name for h in session.helpers), NAME_SEPARATOR)
    presentation = docs.presentation
    if names is None:
        return presentation.nobody_helping
    return render(presentation.helping_template, {"helper_names": names}, docs.ui.unknown)


def compose_broadcast(
    session: HelpSession, docs: PinnedHelpDocs, roles: tuple[BroadcastRole, ...]
) -> BroadcastView:
    name = subject_name(session, docs)
    head = render(
        docs.presentation.broadcast_template, {"subject_name": name, "place": session.place}, docs.ui.unknown
    )
    subject = None
    if session.subject_person_id is not None:
        subject = (session.subject_person_id, name)
    level, _ = level_for(session, docs)
    return BroadcastView(
        broadcast_id=session.broadcast_id,
        help_session_id=session.help_session_id,
        state="active" if session.is_open else "ended",
        level=level,
        subject=subject,
        helpers=tuple((h.person_id, h.display_name) for h in session.helpers),
        roles=roles if session.is_open else (),
        place=session.place,
        text=f"{head} {helping_text(session, docs)}",
        started_at=session.opened_at,
        ended_at=session.closed_at,
    )
