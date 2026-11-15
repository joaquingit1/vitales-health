"""The message for the ground: preview, Tell the ground, reads and the E29 lifecycle (E26 3.3, SYSTEM 8.7).

The press never waits for the link or a composer: it stores the snapshot, the lines, the text and its SHA in one
transaction. Catch-up, `leaves_at` and `status_text` are computed on read; without E29 the snapshot fallback keeps
what was done and leaves unknown facts unknown.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from crew_health.contracts_gen.enums import EventType
from crew_health.help.application.ports import LinkSnapshot
from crew_health.help.application.support import (
    GROUND_AUDIENCE,
    DocsCache,
    HelpDeps,
    audit,
    bounds,
    epoch_at,
    identity,
    optional_call,
    publish_session,
    record_command,
    viewer_of,
    who_of,
)
from crew_health.help.domain import views
from crew_health.help.domain.access import can_read
from crew_health.help.domain.docs import PinnedHelpDocs
from crew_health.help.domain.errors import (
    HelpActionNotAllowed,
    HelpError,
    HelpNotFound,
    HelpSessionClosed,
    HelpUnauthorized,
)
from crew_health.help.domain.ground import (
    CatchUpView,
    EventOutcome,
    GroundEvent,
    GroundMessage,
    HelpLogSnapshot,
    LinkFacts,
    apply_event,
    build_snapshot,
    compose_lines,
    fallback_catch_up,
    leaves_at,
    status_text,
    text_of,
)
from crew_health.help.domain.render import render
from crew_health.help.domain.session import ActorRef, HelpSession, Who, append_line
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    SessionContext,
    UnitOfWork,
)

QUEUE_OPERATION = "queueGroundMessage"
_UNKNOWN_LINK = LinkFacts("unknown", None)
_ENGINE = Actor("engine", "groundlink")


class GroundMessages:
    def __init__(self, deps: HelpDeps, cache: DocsCache) -> None:
        self._deps, self._cache = deps, cache

    def _link(self) -> LinkSnapshot | None:
        composer = self._deps.composer
        return optional_call(self._deps, "composer", composer.link, None) if composer is not None else None

    def _catch_up(self, snapshot: HelpLogSnapshot, docs: PinnedHelpDocs) -> CatchUpView:
        composer = self._deps.composer
        fallback = fallback_catch_up(snapshot, docs)
        if composer is None:
            return fallback

        def compose() -> CatchUpView:
            lines = composer.for_help(snapshot)
            view = CatchUpView(
                tuple(lines.what_happened),
                tuple(lines.what_was_done),
                tuple(lines.being_watched),
                tuple(lines.advice_welcome),
            )
            every = (*view.what_happened, *view.what_was_done, *view.being_watched, *view.advice_welcome)
            if not view.what_happened or not all(isinstance(t, str) and t for t in every):
                return fallback
            return view

        return optional_call(self._deps, "composer", compose, fallback)

    def _readable_open(self, ctx: SessionContext, session: HelpSession | None) -> HelpSession:
        if session is None:
            raise HelpNotFound()
        if not can_read(session, viewer_of(ctx)):
            raise HelpUnauthorized()
        if not session.is_open:
            raise HelpSessionClosed()
        return session

    def preview(self, ctx: SessionContext, help_session_id: str) -> dict[str, object]:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            session = self._readable_open(ctx, deps.sessions.get(snap, help_session_id))
            docs = self._cache.pinned(session)
            now = snap.now
        link = self._link()
        if link is None:
            # GroundMessagePreview requires the full ground link object, which only E29 provides; the press works.
            raise HelpError(
                "INTERNAL_ERROR", "the ground link is not available for a preview", retryable=True
            )
        snapshot = build_snapshot(session, docs, at=now, epoch=epoch_at(deps, now))
        lines = compose_lines(snapshot, docs, None)
        text, sha = text_of(lines)
        facts = LinkFacts(link.status, link.next_contact_at)
        draft = _draft(
            session, snapshot, lines, text, sha, ActorRef("engine", "help", None), now, None, link.simulated
        )
        return {
            "help_session_id": help_session_id,
            "session_revision": session.revision,
            "lines": list(lines),
            "text": text,
            "text_sha256": sha,
            "catch_up": views.catch_up(self._catch_up(snapshot, docs)),
            "ground_link": dict(link.contract),
            "leaves_at": views.opt_instant(leaves_at("queued", facts)),
            "status_text": status_text(draft, facts, docs, now=now, epoch=epoch_at(deps, now)),
            "computed_at": views.instant(now),
            "persisted": False,
        }

    def queue(
        self,
        ctx: SessionContext,
        help_session_id: str,
        extra_note: str | None,
        previewed_sha256: str | None,
        *,
        key: str,
        correlation_id: str,
    ) -> tuple[dict[str, object], bool]:
        """The press stores everything in one transaction with no port call (E26 4.5); E29's link and catch-up
        are read before it and after it, each bounded, and only shape the response."""
        deps = self._deps
        body = {"extra_note": extra_note, "previewed_sha256": previewed_sha256}
        command = identity(ctx, key, QUEUE_OPERATION, help_session_id, body)
        who = who_of(deps, ctx, deps.clock.now())
        if who.ref.kind == "assistant":
            raise HelpActionNotAllowed("the assistant cannot tell the ground")
        link = self._link()
        epoch = epoch_at(deps, deps.clock.now())
        with deps.uows.begin(actor=ctx.actor, command=command, correlation_id=correlation_id) as uow:
            stored = deps.commands.lookup(uow, command)
            if stored is not None and stored.resource_ref is not None:
                loaded = self._load(uow, stored.resource_ref)
                created = False
            else:
                loaded = self._press(
                    uow, ctx, help_session_id, (extra_note, previewed_sha256), who, link, epoch, key
                )
                record_command(deps, uow, command, 201, loaded[0].ground_message_id)
                created = True
            now = uow.now
        message, docs = loaded
        return self._wire(message, docs, now, link, epoch, self._catch_up(message.snapshot, docs)), created

    def _press(
        self,
        uow: UnitOfWork,
        ctx: SessionContext,
        help_session_id: str,
        request: tuple[str | None, str | None],
        who: Who,
        link: LinkSnapshot | None,
        epoch: datetime | None,
        key: str,
    ) -> tuple[GroundMessage, PinnedHelpDocs]:
        deps = self._deps
        extra_note, previewed_sha256 = request
        session = self._readable_open(ctx, deps.sessions.get(uow, help_session_id))
        if extra_note is not None and len(extra_note) > bounds(deps).limits.note_max_chars:
            raise HelpError("INVALID_VALUE", "extra note too long", field="/extra_note")
        docs = self._cache.pinned(session)
        now = uow.now
        snapshot = build_snapshot(session, docs, at=now, epoch=epoch)
        lines = compose_lines(snapshot, docs, extra_note)
        text, sha = text_of(lines)
        simulated = link.simulated if link is not None else session.origin_mode == "SIM"
        message = _draft(session, snapshot, lines, text, sha, who.ref, now, previewed_sha256, simulated)
        message = _with_id(message, deps.ids.new("gm"))
        deps.messages.insert(uow, message)
        line = render(docs.presentation.log_template("ground_message_queued"), {}, docs.ui.unknown)
        changed = append_line(
            session, line, who.ref, "ground", now, ground_message_id=message.ground_message_id
        )
        deps.sessions.save(uow, changed, session.revision)
        deps.sessions.record_action(uow, changed, "tell_ground", who.ref.id, key)
        publish_session(deps, uow, changed, docs)
        audit(deps, uow, "ground_message.queued", {"ground_message_id": message.ground_message_id}, {})
        wire = self._wire(message, docs, now, link, epoch, fallback_catch_up(snapshot, docs))
        _emit_message(uow, message, wire, ground=False)
        return message, docs

    def get(self, ctx: SessionContext, ground_message_id: str) -> dict[str, object]:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            message = deps.messages.get(snap, ground_message_id)
            if message is None:
                raise HelpNotFound()
            if ctx.kind == "ground":
                # CONTRACTS 20: the ground reads what reached it; an outage never leaks a queued message.
                if message.arrived_at is None:
                    raise HelpNotFound()
            else:
                session = deps.sessions.get(snap, message.help_session_id)
                if session is None or not can_read(session, viewer_of(ctx)):
                    raise HelpUnauthorized()
            message, docs = self._load(snap, ground_message_id)
            now = snap.now
        return self._read_wire(message, docs, now)

    def _load(
        self, view: ReadSnapshot | UnitOfWork, ground_message_id: str
    ) -> tuple[GroundMessage, PinnedHelpDocs]:
        deps = self._deps
        message = deps.messages.get(view, ground_message_id)
        session = deps.sessions.get(view, message.help_session_id) if message is not None else None
        if message is None or session is None:
            raise HelpNotFound()
        return message, self._cache.pinned(session)

    def _read_wire(self, message: GroundMessage, docs: PinnedHelpDocs, now: datetime) -> dict[str, object]:
        """The read path: link, mission clock and catch-up come from their providers, bounded, outside storage."""
        return self._wire(
            message,
            docs,
            now,
            self._link(),
            epoch_at(self._deps, now),
            self._catch_up(message.snapshot, docs),
        )

    def _wire(
        self,
        message: GroundMessage,
        docs: PinnedHelpDocs,
        now: datetime,
        link: LinkSnapshot | None,
        epoch: datetime | None,
        catch: CatchUpView,
    ) -> dict[str, object]:
        facts = LinkFacts(link.status, link.next_contact_at) if link is not None else _UNKNOWN_LINK
        return views.ground_message(
            message,
            status=status_text(message, facts, docs, now=now, epoch=epoch),
            leaves=leaves_at(message.state, facts),
            catch=catch,
            allowed=(),
        )

    def apply_event(self, ground_message_id: str, event: GroundEvent) -> EventOutcome:
        """GroundMessageLifecyclePort body: one transaction per incoming event, keyed by its event ID."""
        deps = self._deps
        epoch = epoch_at(deps, deps.clock.now())
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            message = deps.messages.get(uow, ground_message_id)
            session = deps.sessions.get(uow, message.help_session_id) if message is not None else None
            if message is None or session is None:
                raise HelpNotFound()
            changed, outcome, lines = apply_event(message, event, max_events=bounds(deps).ground_events_max)
            if outcome == "duplicate":
                audit(
                    deps, uow, "ground_message.event_duplicate", {"ground_message_id": ground_message_id}, {}
                )
                return outcome
            deps.messages.save(uow, changed, message.revision)
            docs = self._cache.pinned(session)
            self._session_lines(uow, session, docs, lines, event)
            # No port call inside the transaction: readers get E29's link and catch-up on read.
            wire = self._wire(changed, docs, uow.now, None, epoch, fallback_catch_up(changed.snapshot, docs))
            _emit_message(uow, changed, wire, ground=False)
            if changed.arrived_at is not None:
                _emit_message(uow, changed, wire, ground=True)
            return outcome

    def _session_lines(
        self,
        uow: UnitOfWork,
        session: HelpSession,
        docs: PinnedHelpDocs,
        lines: list[tuple[str, str | None]],
        event: GroundEvent,
    ) -> None:
        if not lines:
            return
        current = session
        for key, text in lines:
            words = render(docs.presentation.log_template(key), {"text": text}, docs.ui.unknown)
            by = event.by if key == "ground_reply" else None
            current = append_line(current, words, by, "ground", uow.now)
        self._deps.sessions.save(uow, current, session.revision)
        publish_session(self._deps, uow, current, docs)


def _draft(
    session: HelpSession,
    snapshot: HelpLogSnapshot,
    lines: tuple[str, ...],
    text: str,
    sha: str,
    by: ActorRef,
    now: datetime,
    previewed: str | None,
    simulated: bool,
) -> GroundMessage:
    return GroundMessage(
        ground_message_id="draft",
        revision=1,
        help_session_id=session.help_session_id,
        state="queued",
        lines=lines,
        text=text,
        text_sha256=sha,
        queued_by=by,
        queued_at=now,
        sent_at=None,
        expected_arrival_at=None,
        acknowledged_at=None,
        arrived_at=None,
        one_way_delay_s=None,
        simulated=simulated,
        replies=(),
        session_revision=session.revision,
        snapshot=snapshot,
        previewed_sha256=previewed,
        event_ids=(),
        buffered=(),
    )


def _with_id(message: GroundMessage, ground_message_id: str) -> GroundMessage:
    return replace(message, ground_message_id=ground_message_id)


def _emit_message(uow: UnitOfWork, message: GroundMessage, wire: dict[str, object], *, ground: bool) -> None:
    """Crew on every change; `ground` only for changes at or after arrival (R-E26-10)."""
    scope = EventScope("ground" if ground else "crew", None, message.snapshot.environment_id)
    uow.emit(
        OutboxEvent(
            type=EventType.GROUND_MESSAGE_CHANGED,
            occurred_at=uow.now,
            scope=scope,
            config_release_id=uow.release.release_id,
            correlation_id=uow.correlation_id,
            data_json=canonical_json(wire),
            audience=Audience(
                GROUND_AUDIENCE,
                message.ground_message_id,
                AudienceProjectionKey("help", "ground_message", 1),
                "1",
            ),
        )
    )
