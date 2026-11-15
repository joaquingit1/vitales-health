"""ActOnHelpSession (E26 4.1, CONTRACTS 16).

The command commits and returns (I-H2): the assistant start and cancel go to the help worker (E26 3.6, 4.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from crew_health.help.application.support import (
    DocsCache,
    HelpDeps,
    bounds,
    identity,
    publish_session,
    record_command,
    record_rejection,
    recordable,
    replayed_rejection,
    session_wire,
    viewer_of,
    who_of,
)
from crew_health.help.domain.access import Viewer, can_read, check_act
from crew_health.help.domain.docs import PinnedHelpDocs
from crew_health.help.domain.errors import HelpError, HelpNotFound, HelpUnauthorized, HelpVersionRequired
from crew_health.help.domain.session import (
    ActionName,
    Disposition,
    HelpCommand,
    HelpSession,
    PendingTurn,
    Who,
    apply,
)
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.uow import SessionContext

ACT_OPERATION = "actOnHelpSession"
_CURRENT_STATE_CODES = frozenset({"STALE_VERSION", "INVALID_TRANSITION", "SESSION_CLOSED"})


@dataclass(frozen=True, slots=True)
class ActRequest:
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
    body: dict[str, object] | None = None


def _active(turn: PendingTurn | None) -> PendingTurn | None:
    return turn if turn is not None and turn.state in ("requested", "running") else None


class ActOnHelp:
    def __init__(self, deps: HelpDeps, cache: DocsCache) -> None:
        self._deps, self._cache = deps, cache

    def act(
        self,
        ctx: SessionContext,
        help_session_id: str,
        request: ActRequest,
        *,
        if_match: int | None,
        key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        """Commits and returns (I-H2): no optional port is called inside the transaction or after it here."""
        if if_match is None:
            raise HelpVersionRequired()
        if not viewer_of(ctx).may_use_help:
            raise HelpUnauthorized()
        deps = self._deps
        body = request.body if request.body is not None else {"action": request.action}
        command = identity(ctx, key, ACT_OPERATION, help_session_id, {"if_match": if_match, "body": body})
        who = who_of(deps, ctx, deps.clock.now())
        try:
            before, after, wire = self._commit(
                ctx, help_session_id, request, if_match, command, who, correlation_id
            )
        except HelpError as error:
            if recordable(error):
                record_rejection(deps, ctx, command, help_session_id, error, correlation_id)
            if error.code in _CURRENT_STATE_CODES:
                error.current_state = self._current_state(ctx, help_session_id)
            raise
        self._after_commit(before, after)
        return wire

    def _commit(
        self,
        ctx: SessionContext,
        help_session_id: str,
        request: ActRequest,
        if_match: int,
        command: CommandIdentity,
        who: Who,
        correlation_id: str,
    ) -> tuple[HelpSession | None, HelpSession, dict[str, object]]:
        deps = self._deps
        viewer = viewer_of(ctx)
        with deps.uows.begin(actor=ctx.actor, command=command, correlation_id=correlation_id) as uow:
            session = deps.sessions.get(uow, help_session_id)
            if session is None:
                raise HelpNotFound()
            docs = self._cache.pinned(session)
            # SYSTEM 5.8: the ledger is read before any mutable business precondition, so a retry replays.
            stored = deps.commands.lookup(uow, command)
            if stored is not None:
                return None, session, _replay(stored, session, docs, viewer, who)
            check_act(session, viewer, request.action)
            turn_id = deps.ids.new("hlt") if request.action == "ask_assistant" else None
            changed = apply(
                session,
                _command(request, turn_id),
                who,
                docs,
                bounds(deps).limits,
                now=uow.now,
                if_match=if_match,
            )
            if changed != session:
                deps.sessions.save(uow, changed, session.revision)
                deps.sessions.record_action(uow, changed, request.action, who.ref.id, command.key)
                publish_session(deps, uow, changed, docs)
            record_command(deps, uow, command, 200, help_session_id)
            return session, changed, session_wire(changed, docs, who)

    def _current_state(self, ctx: SessionContext, help_session_id: str) -> dict[str, object] | None:
        """The object as GET would return it, only to a reader of it (closure ends helper grants)."""
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            session = deps.sessions.get(snap, help_session_id)
            if session is None or not can_read(session, viewer_of(ctx)):
                return None
            return session_wire(session, self._cache.pinned(session), who_of(deps, ctx, snap.now))

    def _after_commit(self, before: HelpSession | None, after: HelpSession) -> None:
        """Hands the assistant calls to the help worker (E26 4.5); the command has already returned its state."""
        if before is None:
            return
        work = self._deps.work
        old, new = _active(before.turn), _active(after.turn)
        if old is not None and (new is None or new.help_turn_id != old.help_turn_id):
            work.cancel(old.help_turn_id)
        if (
            new is not None
            and new.state == "requested"
            and (old is None or old.help_turn_id != new.help_turn_id)
        ):
            work.wake()


def _replay(
    stored: StoredResult, session: HelpSession, docs: PinnedHelpDocs, viewer: Viewer, who: Who
) -> dict[str, object]:
    """A retried command returns its recorded outcome, after the current grant is checked again (SYSTEM 5.8).

    The grant is the one `join` uses: a reader of the session, or any crew person while it is open.
    """
    if not (can_read(session, viewer) or (session.is_open and viewer.is_crew_person)):
        raise HelpUnauthorized()
    rejection = replayed_rejection(stored)
    if rejection is not None:
        raise rejection
    return session_wire(session, docs, who)


def _command(request: ActRequest, turn_id: str | None) -> HelpCommand:
    return HelpCommand(
        action=request.action,
        client_time=request.client_time,
        node_id=request.node_id,
        answer_index=request.answer_index,
        flow_id=request.flow_id,
        step_index=request.step_index,
        body_region=request.body_region,
        place=request.place,
        note=request.note,
        text=request.text,
        disposition=request.disposition,
        help_turn_id=turn_id,
    )
