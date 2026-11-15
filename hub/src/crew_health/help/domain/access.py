"""Who may read and act on a help session (E26 3.5, SYSTEM 8.3).

Being in a help session is not a clinical record grant: membership gives the session itself while it is open,
and the limited subject summary while open; nothing else. Identity comes only from the authenticated session.
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.help.domain.errors import HelpUnauthorized
from crew_health.help.domain.session import HelpSession

HELP_ROLES = frozenset({"crew", "crew_medical_officer", "station"})


@dataclass(frozen=True, slots=True)
class Viewer:
    session_id: str
    kind: str
    person_id: str | None
    roles: frozenset[str]

    @property
    def is_station(self) -> bool:
        return self.kind == "station" and "station" in self.roles

    @property
    def is_crew_person(self) -> bool:
        return (
            self.kind == "crew"
            and self.person_id is not None
            and bool(self.roles & {"crew", "crew_medical_officer"})
        )

    @property
    def may_use_help(self) -> bool:
        return self.is_station or self.is_crew_person


def is_member(session: HelpSession, viewer: Viewer) -> bool:
    """Subject, opener, current helpers and the station displays that pressed for this session."""
    if viewer.is_crew_person:
        person = viewer.person_id
        return (
            person == session.subject_person_id
            or (session.opened_by.kind == "person" and session.opened_by.id == person)
            or person in session.helper_ids()
        )
    if viewer.is_station:
        return viewer.session_id in session.station_session_ids
    return False


def can_read(session: HelpSession, viewer: Viewer) -> bool:
    if not viewer.may_use_help:
        return False
    if session.is_open:
        return is_member(session, viewer)
    # Closure ends every helper grant (SYSTEM 8.3); the subject keeps their own record.
    return viewer.is_crew_person and viewer.person_id == session.subject_person_id


def check_act(session: HelpSession, viewer: Viewer, action: str) -> None:
    if not viewer.may_use_help:
        raise HelpUnauthorized()
    if is_member(session, viewer):
        return
    if session.is_open and action == "join" and viewer.is_crew_person:
        return
    raise HelpUnauthorized()


def can_read_summary(session: HelpSession, viewer: Viewer) -> bool:
    """V64: opener, current helpers and subject, only while the session is open."""
    return session.is_open and is_member(session, viewer)
