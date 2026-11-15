"""The help worker (E26 3.6, 4.5): every optional call that follows a commit runs here, never on a command.

Turn rows in `requested` are durable: each pass dispatches them to E13. Turn cancels and broadcast role refreshes
come from a bounded in-memory list; when it is full, the next pass refreshes every open session instead, and at
startup every open session is refreshed, so a crash or a full list only delays work. A cancel that does not fit is
dropped: E13's late result for a cancelled turn is ignored by the epoch and turn checks (`turn_completed`).
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from crew_health.contracts_gen.enums import EventType
from crew_health.help.application.optional import OptionalCalls
from crew_health.help.application.ports import AssistantStart
from crew_health.help.application.ports import BroadcastRole as PortRole
from crew_health.help.application.support import (
    BROADCAST_AUDIENCE,
    DocsCache,
    HelpDeps,
    bounds,
    emit,
    publish_session,
)
from crew_health.help.domain import views
from crew_health.help.domain.broadcast import BroadcastRole, compose_broadcast
from crew_health.help.domain.session import PendingTurn
from crew_health.help.domain.turns import turn_failed, turn_started
from crew_health.kernel.uow import Actor

_ENGINE = Actor("engine", "help")
_LOGGER = "crew_health.help"


class HelpWorker:
    """One thread (E26 4.5). `run_once` is one bounded pass; tests drive it directly, production starts the loop."""

    def __init__(self, deps: HelpDeps, cache: DocsCache) -> None:
        self._deps, self._cache = deps, cache
        limits = bounds(deps)
        self._calls = OptionalCalls(limits.worker_port_timeout_ms / 1000)
        self._recovered = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="help-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._deps.work.wake()

    def _loop(self) -> None:
        interval = bounds(self._deps).worker_interval_ms / 1000
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001  # the loop survives; the durable rows keep the work
                logging.getLogger(_LOGGER).error("help worker pass failed: %s", type(error).__name__)
            self._deps.work.wait(interval)

    def run_once(self) -> None:
        deps = self._deps
        batch_max = bounds(deps).worker_batch
        if not self._recovered:
            self._recover()
            self._recovered = True
        work = deps.work.take(batch_max)
        for turn_id in work.cancels:
            self._cancel(turn_id)
        self._dispatch_requested(batch_max)
        dirty = work.dirty
        if work.sweep_all:
            dirty = tuple(dict.fromkeys((*dirty, *self._open_ids())))
        for help_session_id in dirty:
            self._refresh_broadcast(help_session_id)

    def _cancel(self, help_turn_id: str) -> None:
        assistant = self._deps.assistant
        if assistant is not None:
            self._calls.call("assistant", lambda: assistant.cancel_help_turn(help_turn_id), None)

    def _open_ids(self) -> tuple[str, ...]:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            rows = deps.sessions.open_sessions(snap, bounds(deps).max_open_sessions)
            return tuple(s.help_session_id for s in rows)

    def _recover(self) -> None:
        """At startup: running turns past the timeout fail; every open broadcast is refreshed (E26 3.6)."""
        deps = self._deps
        limit = bounds(deps)
        with deps.snapshots.snapshot() as snap:
            sessions = tuple(deps.sessions.open_sessions(snap, limit.max_open_sessions))
            now = snap.now
        for session in sessions:
            turn = session.turn
            if turn is None or turn.state != "running":
                continue
            if now - turn.asked_at > timedelta(seconds=limit.turn_timeout_s):
                self._fail(turn.help_turn_id)
        deps.work.sweep_all()

    def _fail(self, help_turn_id: str) -> None:
        deps = self._deps
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            session = deps.sessions.session_for_turn(uow, help_turn_id)
            if session is None:
                return
            changed = turn_failed(session, help_turn_id, uow.now)
            if changed != session:
                deps.sessions.save(uow, changed, session.revision)
                publish_session(deps, uow, changed, self._cache.pinned(session))

    def _dispatch_requested(self, limit: int) -> None:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            sessions = tuple(deps.sessions.open_sessions(snap, bounds(deps).max_open_sessions))
        pending = [
            (s.help_session_id, s.turn)
            for s in sessions
            if s.turn is not None and s.turn.state == "requested"
        ]
        for help_session_id, turn in pending[:limit]:
            self.dispatch(help_session_id, turn)

    def dispatch(self, help_session_id: str, turn: PendingTurn) -> None:
        """Call E13 outside any transaction; absent, failing or slow means `unavailable`."""
        deps = self._deps
        assistant = deps.assistant
        unavailable = AssistantStart("unavailable", None)
        start = unavailable
        if assistant is not None:
            kind = turn.asked_by.kind
            actor = Actor("person" if kind == "person" else "station", turn.asked_by.id)
            start = self._calls.call(
                "assistant",
                lambda: assistant.start_help_turn(help_session_id, turn.help_turn_id, actor, turn.text, None),
                unavailable,
            )
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            session = deps.sessions.get(uow, help_session_id)
            if session is None:
                return
            docs = self._cache.pinned(session)
            changed = turn_started(
                session, turn.help_turn_id, start.status, start.conversation_id, docs, uow.now
            )
            if changed != session:
                deps.sessions.save(uow, changed, session.revision)
                publish_session(deps, uow, changed, docs)

    def _roles(self, help_session_id: str) -> tuple[BroadcastRole, ...]:
        source = self._deps.roles
        if source is None:
            return ()
        empty: tuple[PortRole, ...] = ()
        rows = self._calls.call("roles", lambda: tuple(source.roles_for_session(help_session_id)), empty)
        return tuple(
            BroadcastRole(r.role_assignment_id, r.role_id, r.label, r.person_id, r.display_name, r.state)
            for r in rows
        )

    def _refresh_broadcast(self, help_session_id: str) -> None:
        """Roles come from E31 after commit (E26 4.5); absent, failing or slow means no roles (fail closed)."""
        deps = self._deps
        roles = self._roles(help_session_id)
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            session = deps.sessions.get(uow, help_session_id)
            if session is None or not session.is_open:
                return
            docs = self._cache.pinned(session)
            after = compose_broadcast(session, docs, roles)
            if deps.sessions.broadcast_for(uow, help_session_id) == after:
                return
            deps.sessions.save_broadcast(uow, after)
            emit(
                deps,
                uow,
                EventType.HELP_BROADCAST_CHANGED,
                views.broadcast(after),
                BROADCAST_AUDIENCE,
                after.broadcast_id,
                "broadcast",
                session.environment_id,
            )
