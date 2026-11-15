"""Event audience rules for help (SYSTEM 8.3): who may receive each help event.

`help.session_changed` reaches only readers of that session; every crew and station session gets the broadcast;
a ground message reaches its session's readers, and a ground session only once the message arrived.
"""

from __future__ import annotations

from crew_health.help.adapters.sqlite_repository import SqliteGroundMessages, SqliteHelpSessions
from crew_health.help.domain.access import Viewer, can_read
from crew_health.kernel.uow import Audience, ReadSnapshot, SessionContext


def _viewer(session: SessionContext) -> Viewer:
    return Viewer(
        session.session_id, session.kind, session.person_id, frozenset(r.value for r in session.roles)
    )


class SessionReaderRule:
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        found = SqliteHelpSessions().get(snapshot, audience.object_ref)
        return found is not None and can_read(found, _viewer(session))


class BroadcastRule:
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return _viewer(session).may_use_help


class GroundMessageRule:
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        message = SqliteGroundMessages().get(snapshot, audience.object_ref)
        if message is None:
            return False
        if session.kind == "ground":
            return message.arrived_at is not None
        found = SqliteHelpSessions().get(snapshot, message.help_session_id)
        return found is not None and can_read(found, _viewer(session))
