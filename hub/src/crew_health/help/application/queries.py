"""Reads: one session, a page of sessions, the subject summary and the active broadcasts (E26 4.1, 6).

Being in a help session is not a clinical record grant: the summary is the limited panel of SYSTEM 8.3, for the
opener, current helpers and subject while the session is open, every read audited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.help.application.ports import NamedList, ProfileFacts, ReadingView
from crew_health.help.application.support import (
    DocsCache,
    HelpDeps,
    audit,
    bounds,
    optional_call,
    session_wire,
    viewer_of,
    who_of,
)
from crew_health.help.domain import views
from crew_health.help.domain.access import can_read, can_read_summary
from crew_health.help.domain.errors import HelpNotFound, HelpStorageFault, HelpUnauthorized
from crew_health.kernel.uow import SessionContext

_UNKNOWN_FACTS = ProfileFacts(NamedList("unknown", ()), NamedList("unknown", ()))


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[dict[str, object], ...]
    next_cursor: str | None


class HelpQueries:
    def __init__(self, deps: HelpDeps, cache: DocsCache) -> None:
        self._deps, self._cache = deps, cache

    def get(self, ctx: SessionContext, help_session_id: str) -> dict[str, object]:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            session = deps.sessions.get(snap, help_session_id)
            if session is None:
                raise HelpNotFound()
            if not can_read(session, viewer_of(ctx)):
                raise HelpUnauthorized()
            return session_wire(session, self._cache.pinned(session), who_of(deps, ctx, snap.now))

    def page(
        self, ctx: SessionContext, state: str | None, cursor: str | None, limit: int | None
    ) -> SessionPage:
        deps = self._deps
        viewer = viewer_of(ctx)
        if not viewer.may_use_help:
            return SessionPage((), None)
        size = min(limit or bounds(deps).page_max, bounds(deps).page_max)
        wanted: Literal["open", "closed"] | None = None
        if state == "open":
            wanted = "open"
        elif state == "closed":
            wanted = "closed"
        with deps.snapshots.snapshot() as snap:
            rows = tuple(deps.sessions.page(snap, wanted, cursor, size))
            who = who_of(deps, ctx, snap.now)
            items = tuple(session_wire(s, self._cache.pinned(s), who) for s in rows if can_read(s, viewer))
        next_cursor = rows[-1].help_session_id if len(rows) == size else None
        return SessionPage(items, next_cursor)

    def broadcasts(self, ctx: SessionContext) -> tuple[dict[str, object], ...]:
        deps = self._deps
        if not viewer_of(ctx).may_use_help:
            raise HelpUnauthorized()
        with deps.snapshots.snapshot() as snap:
            return tuple(
                views.broadcast(b) for b in deps.sessions.active_broadcasts(snap, bounds(deps).page_max)
            )

    def subject_summary(
        self, ctx: SessionContext, help_session_id: str, correlation_id: str
    ) -> dict[str, object]:
        """Readings and profile facts are read outside the write transaction (E26 4.5), which only audits."""
        deps = self._deps
        viewer = viewer_of(ctx)
        with deps.snapshots.snapshot() as snap:
            session = deps.sessions.get(snap, help_session_id)
            if session is None:
                raise HelpNotFound()
            if not can_read_summary(session, viewer):
                raise HelpUnauthorized()
            now = snap.now
        person = session.subject_person_id
        if person is None:
            raise HelpNotFound()
        docs = self._cache.pinned(session)
        readings, future = self._readings(
            person, docs.presentation.readings_codes, docs.presentation.readings_max_age_minutes, now
        )
        profile = deps.profile
        facts = (
            optional_call(deps, "profile", lambda: profile.allergies_and_medicines(person), _UNKNOWN_FACTS)
            if profile
            else _UNKNOWN_FACTS
        )
        with deps.uows.begin(actor=ctx.actor, correlation_id=correlation_id) as uow:
            current = deps.sessions.get(uow, help_session_id)
            if current is None or not can_read_summary(current, viewer):
                # The grant ended while the panel was read (closed, or the reader left): nothing is served.
                raise HelpUnauthorized()
            refs = {"help_session_id": help_session_id}
            audit(deps, uow, "help.subject_viewed", refs, {"readings": len(readings)})
            if future:
                audit(deps, uow, "help.reading_future_excluded", refs, {"count": future})
        return {
            "help_session_id": help_session_id,
            "subject": {
                "person_id": person,
                "display_name": session.subject_display_name or docs.ui.unknown,
            },
            "as_of": views.instant(now),
            "readings": [_reading(r, now) for r in readings],
            "allergies": _named(facts.allergies),
            "medicines": _named(facts.medicines),
        }

    def _readings(
        self, person: str, codes: tuple[str, ...], max_age: int, now: datetime
    ) -> tuple[tuple[ReadingView, ...], int]:
        port = self._deps.observations
        if port is None:
            # E26 4.1: the panel never shows an empty list for a port it could not read.
            raise HelpStorageFault("readings are not available")
        since = now - timedelta(minutes=max_age)
        try:
            rows: Sequence[ReadingView] = port.latest_accepted(person, codes, since, now, 1)
        except Exception as error:  # noqa: BLE001 - a records read failure is reported, never an empty panel
            raise HelpStorageFault("readings could not be read") from error
        latest: dict[str, ReadingView] = {}
        future = 0
        for row in rows:
            if row.observed_at > now:
                future += 1
                continue
            if row.observed_at < since or row.code not in codes:
                continue
            key = (row.observed_at, row.received_at, row.observation_id)
            current = latest.get(row.code)
            if current is None or key > (current.observed_at, current.received_at, current.observation_id):
                latest[row.code] = row
        return tuple(latest[c] for c in codes if c in latest), future


def _reading(row: ReadingView, now: datetime) -> dict[str, object]:
    minutes = max(0, int((now - row.observed_at).total_seconds() // 60))
    return {
        "code": row.code,
        "label": row.label,
        "value_text": row.value_text,
        "observed_at": views.instant(row.observed_at),
        "origin_mode": row.origin_mode,
        "source_label": row.source_label,
        "body_region": row.body_region,
        "age_minutes": minutes,
    }


def _named(values: NamedList) -> dict[str, object]:
    return {"state": values.state, "items": [{"name": n} for n in values.names]}
