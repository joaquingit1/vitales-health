"""OpenHelpSession (E26 4.1, CONTRACTS 16 "Opening", SYSTEM 8.1).

Never waits for the model, a device, the network or the ground: only storage and the pinned configuration are on
the path. One session per open subject (unique index plus dedupe), one per idempotency key, also after a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from crew_health.contracts_gen.enums import ConfigName
from crew_health.help.application.ports import MissionFacts
from crew_health.help.application.repositories import OpenSessionExists
from crew_health.help.application.support import (
    DocsCache,
    HelpDeps,
    audit,
    bounds,
    display_name,
    identity,
    optional_call,
    publish_session,
    record_command,
    roster_has,
    session_wire,
    viewer_of,
    who_of,
)
from crew_health.help.domain.docs import PinnedHelpDocs, parse_flows
from crew_health.help.domain.errors import (
    HelpActionNotAllowed,
    HelpConfigInvalid,
    HelpError,
    HelpInvalidValue,
    HelpNotFound,
    HelpStorageFault,
    HelpUnauthorized,
)
from crew_health.help.domain.replay import ClientFlows, OfflineAnswer, replay
from crew_health.help.domain.session import (
    EntryKind,
    HelpCommand,
    HelpSession,
    Opening,
    SubjectKind,
    Who,
    apply,
    attach_station,
    open_session,
)
from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork

OPEN_OPERATION = "openHelpSession"


@dataclass(frozen=True, slots=True)
class OpenRequest:
    subject_kind: SubjectKind
    subject_person_id: str | None
    entry_kind: EntryKind
    entry_ref_id: str | None
    place: str | None
    client_opened_at: datetime
    offline_path: tuple[OfflineAnswer, ...]
    flow_id: str | None
    client_release_id: str | None


@dataclass(frozen=True, slots=True)
class _Resolved:
    subject_kind: SubjectKind
    subject_person_id: str | None
    unchecked: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Facts:
    """Optional reads done before the open transaction (E26 4.5), each bounded by `help.port_timeout_ms`."""

    subject_name: str | None
    mission: MissionFacts | None


def _subject(deps: HelpDeps, ctx: SessionContext, request: OpenRequest, now: datetime) -> _Resolved:
    """CONTRACTS 16: `self` is the session person; `crewmate` needs a roster person; `unknown` has none."""
    person = ctx.person_id if ctx.kind == "crew" else None
    kind, subject = request.subject_kind, request.subject_person_id
    if kind == "self":
        if person is None or (subject is not None and subject != person):
            raise HelpInvalidValue("self needs the signed-in person", field="/subject/person_id")
        return _Resolved("self", person, ())
    if kind == "unknown":
        if subject is not None:
            raise HelpInvalidValue("an unknown subject has no person", field="/subject/person_id")
        return _Resolved("unknown", None, ())
    if subject is None:
        raise HelpInvalidValue("a crewmate needs a person", field="/subject/person_id")
    if subject == person:
        return _Resolved("self", person, ())
    known = roster_has(deps, subject, now)
    if known is False:
        raise HelpInvalidValue("not on the crew roster", field="/subject/person_id")
    return _Resolved("crewmate", subject, ("roster",) if known is None else ())


def _entry(deps: HelpDeps, ctx: SessionContext, request: OpenRequest) -> tuple[str, ...]:
    kind, ref, flow = request.entry_kind, request.entry_ref_id, request.flow_id
    if kind in ("button", "urgent_event_alias"):
        if ref is not None or flow is not None:
            raise HelpInvalidValue("a button entry has no reference or flow", field="/entry/ref_id")
        return ()
    if ref is None:
        raise HelpInvalidValue("this entry needs its reference", field="/entry/ref_id")
    if kind == "warning":
        if flow is not None:
            raise HelpInvalidValue("flow_id only with a red flag", field="/flow_id")
        queries = deps.warnings
        visible = (
            optional_call(deps, "warnings", lambda: queries.visible(ctx, ref), None)
            if queries is not None
            else None
        )
        if visible is False:
            raise HelpUnauthorized()
        return ("warning",) if visible is None else ()
    if flow is None:
        raise HelpInvalidValue("a red flag entry needs its flow", field="/flow_id")
    return ()


def _client_flows(
    releases: ConfigReleaseReader | None, docs: PinnedHelpDocs, client: str | None
) -> ClientFlows:
    if client is None or client == docs.release_id:
        return ClientFlows(True, None)
    if releases is None:
        return ClientFlows(False, None)
    try:
        raw = releases.release(client).document(ConfigName.RESPONDER_FLOWS).raw
        return ClientFlows(False, parse_flows(raw))
    except ProblemError, HelpConfigInvalid:
        return ClientFlows(False, None)


class OpenHelp:
    def __init__(self, deps: HelpDeps, cache: DocsCache, releases: ConfigReleaseReader | None) -> None:
        self._deps, self._cache, self._releases = deps, cache, releases

    def open(
        self, ctx: SessionContext, request: OpenRequest, *, key: str, operation: str, correlation_id: str
    ) -> tuple[dict[str, object], bool]:
        if not viewer_of(ctx).may_use_help:
            raise HelpUnauthorized()
        if ctx.actor.kind == "assistant":
            # AGENTS rule 4: the assistant never starts or joins help, even inside a person's session.
            raise HelpActionNotAllowed("the assistant cannot open help")
        return self._open(ctx, request, key=key, operation=operation, correlation_id=correlation_id)

    def open_for_engine(
        self, engine: str, request: OpenRequest, *, key: str, operation: str, correlation_id: str
    ) -> str:
        """Internal entries (E11 red flag, SYSTEM 8.1): opener `engine`, never a person or a station."""
        ctx = SessionContext(f"engine:{engine}", "adapter", Actor("engine", engine), None, frozenset())
        wire, _ = self._open(ctx, request, key=key, operation=operation, correlation_id=correlation_id)
        return str(wire["help_session_id"])

    def _open(
        self, ctx: SessionContext, request: OpenRequest, *, key: str, operation: str, correlation_id: str
    ) -> tuple[dict[str, object], bool]:
        deps = self._deps
        now = deps.clock.now()
        resolved = _subject(deps, ctx, request, now)
        unchecked = resolved.unchecked + _entry(deps, ctx, request)
        command = identity(ctx, key, operation, "help-sessions", _body(request))
        who = who_of(deps, ctx, now)
        mission = deps.mission
        facts = _Facts(
            display_name(deps, resolved.subject_person_id, now),
            optional_call(deps, "mission", lambda: mission.current(), None) if mission is not None else None,
        )
        with deps.uows.begin(actor=ctx.actor, command=command, correlation_id=correlation_id) as uow:
            stored = deps.commands.lookup(uow, command)
            if stored is not None and stored.resource_ref is not None:
                return self._replayed(uow, stored.resource_ref, who), False
            docs = self._cache.active(uow)
            session, created = self._open_or_join(uow, ctx, request, resolved, who, docs, command, facts)
            for check in unchecked:
                audit(
                    deps,
                    uow,
                    "help.ref_unchecked",
                    {"help_session_id": session.help_session_id},
                    {"check": check},
                )
            return session_wire(session, docs, who), created

    def _replayed(self, uow: UnitOfWork, session_id: str, who: Who) -> dict[str, object]:
        session = self._deps.sessions.get(uow, session_id)
        if session is None:
            raise HelpNotFound()
        return session_wire(session, self._cache.pinned(session), who)

    def _open_or_join(
        self,
        uow: UnitOfWork,
        ctx: SessionContext,
        request: OpenRequest,
        resolved: _Resolved,
        who: Who,
        docs: PinnedHelpDocs,
        command: CommandIdentity,
        facts: _Facts,
    ) -> tuple[HelpSession, bool]:
        deps = self._deps
        if request.flow_id is not None and request.entry_kind == "red_flag":
            if request.flow_id not in docs.presentation.red_flag_flow_ids():
                raise HelpInvalidValue("not a red flag flow", field="/flow_id")
        existing = None
        if resolved.subject_person_id is not None:
            existing = deps.sessions.get_open_for_subject(uow, resolved.subject_person_id)
        if existing is not None:
            joined = self._join(uow, ctx, request, existing, who)
            record_command(deps, uow, command, 201, joined.help_session_id)
            return joined, False
        session = self._new(uow, ctx, request, resolved, who, docs, facts)
        try:
            deps.sessions.insert(uow, session)
        except OpenSessionExists as error:
            # Unreachable under BEGIN IMMEDIATE after the dedupe read: an integrity fault, never swallowed.
            raise HelpStorageFault("open session index conflict") from error
        deps.sessions.record_action(uow, session, "open", who.ref.id, command.key)
        audit(
            deps,
            uow,
            "help.opened",
            {"help_session_id": session.help_session_id},
            {"entry": request.entry_kind},
        )
        publish_session(deps, uow, session, docs)
        record_command(deps, uow, command, 201, session.help_session_id)
        return session, True

    def _join(
        self, uow: UnitOfWork, ctx: SessionContext, request: OpenRequest, session: HelpSession, who: Who
    ) -> HelpSession:
        """R-E26-1: a second press returns the open session; a person pressing joins as a helper (I-H4), and its
        offline path is replayed against that session (E26 3.2). A red flag or plan step entry never moves it."""
        deps = self._deps
        docs = self._cache.pinned(session)
        if who.ref.kind == "station":
            changed = attach_station(session, ctx.session_id)
        elif (
            who.person_id is None
            or who.person_id == session.subject_person_id
            or who.person_id in session.helper_ids()
        ):
            changed = session
        else:
            command = HelpCommand("join", uow.now)
            try:
                changed = apply(
                    session, command, who, docs, bounds(deps).limits, now=uow.now, if_match=session.revision
                )
            except HelpError:
                # A full log never blocks the press: the existing session is returned unchanged.
                changed = session
        joined = changed.revision != session.revision
        if request.offline_path and request.entry_kind in ("button", "urgent_event_alias", "warning"):
            changed = self._replay(uow, request, changed, who, docs)
        if changed != session:
            deps.sessions.save(uow, changed, session.revision)
            if changed.revision != session.revision:
                deps.sessions.record_action(uow, changed, "join" if joined else "replay", who.ref.id, None)
                publish_session(deps, uow, changed, docs)
        return changed

    def _replay(
        self, uow: UnitOfWork, request: OpenRequest, session: HelpSession, who: Who, docs: PinnedHelpDocs
    ) -> HelpSession:
        client = _client_flows(self._releases, docs, request.client_release_id)
        limits = bounds(self._deps).limits
        result = replay(session, request.offline_path, who, docs, limits, client=client, now=uow.now)
        if result.stop_reason is not None:
            meta: dict[str, str | int | bool] = {
                "reason": result.stop_reason,
                "applied": result.applied_count,
                "index": result.stop_index if result.stop_index is not None else result.applied_count,
            }
            if result.stop_node_id is not None:
                meta["node_id"] = result.stop_node_id
            meta["at_node_id"] = result.session.node_id
            audit(self._deps, uow, "help.replay_stopped", {"help_session_id": session.help_session_id}, meta)
        return result.session

    def _new(
        self,
        uow: UnitOfWork,
        ctx: SessionContext,
        request: OpenRequest,
        resolved: _Resolved,
        who: Who,
        docs: PinnedHelpDocs,
        facts: _Facts,
    ) -> HelpSession:
        deps = self._deps
        mission = facts.mission
        opening = Opening(
            help_session_id=deps.ids.new("hlp"),
            broadcast_id=deps.ids.new("brd"),
            now=uow.now,
            client_opened_at=request.client_opened_at,
            opener=who,
            opener_session_id=ctx.session_id,
            entry_kind=request.entry_kind,
            entry_ref_id=request.entry_ref_id,
            subject_kind=resolved.subject_kind,
            subject_person_id=resolved.subject_person_id,
            subject_display_name=facts.subject_name,
            place=request.place,
            flow_id=request.flow_id,
            mission_id=mission.mission_id if mission is not None else None,
            origin_mode=mission.mission_mode if mission is not None else None,
            environment_id=mission.active_environment_id if mission is not None else None,
        )
        limits = bounds(deps).limits
        session = open_session(opening, docs, limits)
        if not request.offline_path:
            return session
        return self._replay(uow, request, session, who, docs)


def _body(request: OpenRequest) -> dict[str, object]:
    return {
        "subject": {"kind": request.subject_kind, "person_id": request.subject_person_id},
        "entry": {"kind": request.entry_kind, "ref_id": request.entry_ref_id},
        "place": request.place,
        "client_opened_at": request.client_opened_at.isoformat(),
        "offline_path": [[a.node_id, a.answer_index, a.at.isoformat()] for a in request.offline_path],
        "flow_id": request.flow_id,
    }
