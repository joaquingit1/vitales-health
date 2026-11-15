"""Shared application plumbing: dependencies, pinned documents, identities, events and audit for help."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from crew_health.contracts_gen.enums import ERROR_STATUS, ConfigName, ErrorCode, EventType
from crew_health.help.application.optional import OptionalCalls
from crew_health.help.application.ports import (
    BroadcastRolesSource,
    CrewMember,
    GroundLogComposer,
    HelpAssistantPort,
    MeasurementGate,
    MissionStatePort,
    ObservationReadPort,
    ProfileFactsPort,
    RosterPort,
    TextRulesPort,
    WarningQueries,
)
from crew_health.help.application.repositories import GroundMessageRepository, HelpSessionRepository
from crew_health.help.application.settings import HelpBounds, read_bounds
from crew_health.help.application.work import HelpWork
from crew_health.help.domain import views
from crew_health.help.domain.access import Viewer, can_read
from crew_health.help.domain.broadcast import BroadcastView, compose_broadcast
from crew_health.help.domain.docs import ConfigPin, PinnedHelpDocs, pin_documents
from crew_health.help.domain.errors import HelpConfigInvalid, HelpError, HelpErrorCode
from crew_health.help.domain.session import ActorRef, HelpSession, Who, allowed_actions
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.config import ConfigDocumentView, ConfigReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)

HELP_DOCS: tuple[ConfigName, ConfigName, ConfigName] = (
    ConfigName.RESPONDER_FLOWS,
    ConfigName.HELP_PRESENTATION,
    ConfigName.UI_COPY,
)
SESSION_AUDIENCE = "help.session_reader"
BROADCAST_AUDIENCE = "help.broadcast_reader"
GROUND_AUDIENCE = "help.ground_message_reader"
POLICY_VERSION = "1"


@dataclass(frozen=True, slots=True)
class HelpDeps:
    """Required: storage, configuration, settings, ids, clock, the command ledger. Everything else is optional."""

    uows: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    config: ConfigReader
    settings: HubSettings
    ids: IdFactory
    clock: Clock
    commands: IdempotencyStore
    receipts: CommandReceipts
    sessions: HelpSessionRepository
    messages: GroundMessageRepository
    mission_clock: MissionClock | None = None
    mission: MissionStatePort | None = None
    roster: RosterPort | None = None
    roles: BroadcastRolesSource | None = None
    assistant: HelpAssistantPort | None = None
    warnings: WarningQueries | None = None
    composer: GroundLogComposer | None = None
    text_rules: TextRulesPort | None = None
    measurement_gate: MeasurementGate | None = None
    observations: ObservationReadPort | None = None
    profile: ProfileFactsPort | None = None
    calls: OptionalCalls = field(default_factory=OptionalCalls)
    work: HelpWork = field(default_factory=HelpWork)


def optional_call[T](deps: HelpDeps, name: str, call: Callable[[], T], fallback: T) -> T:
    """Optional providers never break or hold up help: a failure or a slow answer yields the designed fallback.

    Bounded by `help.port_timeout_ms` (E26 4.5). Only for calls into other modules' optional ports, and never inside
    a write transaction; help storage errors are never wrapped here.
    """
    return deps.calls.call(name, call, fallback)


class DocsCache:
    """Parsed pinned documents, keyed by release and versions; bounded by `help.docs_cache_entries`."""

    def __init__(self, config: ConfigReader, maximum: int) -> None:
        self._config = config
        self._maximum = maximum
        self._entries: OrderedDict[tuple[str, ...], PinnedHelpDocs] = OrderedDict()

    def _parse(self, release_id: str, documents: tuple[ConfigDocumentView, ...]) -> PinnedHelpDocs:
        key = (release_id, *(d.version for d in documents))
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached
        flows, presentation, ui = documents
        pins = tuple(ConfigPin(d.name.value, d.version) for d in documents)
        docs = pin_documents(
            release_id, flows_raw=flows.raw, presentation_raw=presentation.raw, ui_raw=ui.raw, pins=pins
        )
        self._entries[key] = docs
        while len(self._entries) > self._maximum:
            self._entries.popitem(last=False)
        return docs

    def active(self, view: ReadSnapshot | UnitOfWork) -> PinnedHelpDocs:
        release = view.release
        try:
            documents = tuple(release.document(name) for name in HELP_DOCS)
        except ProblemError as error:
            raise HelpConfigInvalid((f"active release: {error.code.value}",)) from error
        if len(documents) != 3:
            raise HelpConfigInvalid(("active release: help documents missing",))
        return self._parse(release.release_id, (documents[0], documents[1], documents[2]))

    def pinned(self, session: HelpSession) -> PinnedHelpDocs:
        versions = {pin.name: pin.version for pin in session.config_pins}
        try:
            documents = tuple(self._config.document(name, versions[name.value]) for name in HELP_DOCS)
        except (KeyError, ProblemError) as error:
            raise HelpConfigInvalid(("pinned help documents unreadable",)) from error
        return self._parse(session.release_id, (documents[0], documents[1], documents[2]))


def viewer_of(session: SessionContext) -> Viewer:
    return Viewer(
        session.session_id, session.kind, session.person_id, frozenset(r.value for r in session.roles)
    )


def display_name(deps: HelpDeps, person_id: str | None, at: datetime) -> str | None:
    roster = deps.roster
    if person_id is None or roster is None:
        return None
    empty: tuple[CrewMember, ...] = ()
    members = optional_call(deps, "roster", lambda: tuple(roster.crew(at)), empty)
    for member in members:
        if member.person_id == person_id:
            return member.display_name
    return None


def roster_has(deps: HelpDeps, person_id: str, at: datetime) -> bool | None:
    """True or False from the roster; None when the roster is absent or failing (checked later, audited)."""
    roster = deps.roster
    if roster is None:
        return None
    members = optional_call(deps, "roster", lambda: tuple(roster.crew(at)), None)
    if members is None:
        return None
    return any(m.person_id == person_id for m in members)


def who_of(deps: HelpDeps, session: SessionContext, at: datetime) -> Who:
    """The command's actor from the authenticated actor kind, never from the session kind alone.

    The assistant acting inside a crew person's session stays the assistant, so the domain refuses it (rule 4).
    """
    actor = session.actor
    if actor.kind == "person" and session.kind == "crew" and session.person_id is not None:
        name = display_name(deps, session.person_id, at)
        return Who(ActorRef("person", session.person_id, name), session.person_id)
    if actor.kind == "station" and session.kind == "station":
        return Who(ActorRef("station", actor.id, None), None)
    return Who(ActorRef(actor.kind, actor.id, None), None)


def bounds(deps: HelpDeps) -> HelpBounds:
    return read_bounds(deps.settings)


def epoch_at(deps: HelpDeps, at: datetime) -> datetime | None:
    clock = deps.mission_clock
    if clock is None:
        return None
    version = optional_call(deps, "mission_clock", lambda: clock.version_at(at), None)
    return version.epoch if version is not None else None


def identity(session: SessionContext, key: str, operation: str, target: str, body: object) -> CommandIdentity:
    """The key covers the operation, its target resource and the body (AGENTS 6)."""
    parameters = canonical_json({"operation": operation, "target": target, "body": body})
    return command_identity(
        key=key,
        actor_ref=f"{session.actor.kind}:{session.actor.id}",
        operation=operation,
        target=target,
        parameters_json=parameters,
        body_fingerprint=hashlib.sha256(parameters).hexdigest(),
        fingerprint_kind="sha256",
    )


ACCEPTED = "accepted"
# Rejections a retry must see again (E26 3.2); retryable faults, scope and lookup errors are never recorded.
_UNRECORDED = frozenset({"NOT_FOUND", "UNAUTHORIZED_SCOPE", "VERSION_REQUIRED", "DUPLICATE_COMMAND"})


def record_command(
    deps: HelpDeps, uow: UnitOfWork, command: CommandIdentity, status: int, resource: str
) -> AcceptedReceipt:
    """An applied command; the result code is the ledger's `accepted` (SYSTEM 5.8, E01 CommandLedger)."""
    receipt = deps.receipts.accepted(uow, command)
    deps.commands.record(uow, command, StoredResult(receipt, status, resource, ACCEPTED, None))
    return receipt


def recordable(error: HelpError) -> bool:
    return not error.retryable and error.code not in _UNRECORDED and error.code != "INTERNAL_ERROR"


def record_rejection(
    deps: HelpDeps,
    ctx: SessionContext,
    command: CommandIdentity,
    resource: str,
    error: HelpError,
    correlation_id: str,
) -> None:
    """E26 3.2: a rejected command is recorded too, after its own transaction rolled back, with its error code."""
    code = ErrorCode(error.code)
    with deps.uows.begin(actor=ctx.actor, command=command, correlation_id=correlation_id) as uow:
        if deps.commands.lookup(uow, command) is not None:
            return
        receipt = deps.receipts.accepted(uow, command)
        deps.commands.record(
            uow, command, StoredResult(receipt, ERROR_STATUS[code], resource, code.value, None)
        )
        audit(deps, uow, "help.command_rejected", {"target": resource}, {"code": code.value})


def replayed_rejection(stored: StoredResult) -> HelpError | None:
    """The recorded rejection of a retried command, raised again with its code; None for an applied one."""
    if stored.result_code == ACCEPTED or stored.status < 400:
        return None
    code = cast(HelpErrorCode, ErrorCode(stored.result_code).value)
    return HelpError(code, "this command was refused when it was first sent")


def audit(
    deps: HelpDeps, uow: UnitOfWork, event_type: str, refs: dict[str, str], meta: dict[str, str | int | bool]
) -> None:
    uow.audit(AuditEntry(deps.ids.new("hla"), event_type, 1, uow.actor, refs, uow.correlation_id, None, meta))


def session_wire(session: HelpSession, docs: PinnedHelpDocs, who: Who | None) -> dict[str, object]:
    allowed = allowed_actions(session, who) if who is not None else ()
    return views.help_session(session, docs, allowed)


def visible_wire(
    session: HelpSession, docs: PinnedHelpDocs, viewer: Viewer, who: Who
) -> dict[str, object] | None:
    return session_wire(session, docs, who) if can_read(session, viewer) else None


def emit(
    deps: HelpDeps,
    uow: UnitOfWork,
    kind: EventType,
    data: object,
    rule: str,
    ref: str,
    name: str,
    env: str | None,
) -> None:
    uow.emit(
        OutboxEvent(
            type=kind,
            occurred_at=uow.now,
            scope=EventScope("crew", None, env),
            config_release_id=uow.release.release_id,
            correlation_id=uow.correlation_id,
            data_json=canonical_json(data),
            audience=Audience(rule, ref, AudienceProjectionKey("help", name, 1), POLICY_VERSION),
        )
    )


def publish_session(
    deps: HelpDeps, uow: UnitOfWork, session: HelpSession, docs: PinnedHelpDocs
) -> BroadcastView:
    """help.session_changed to its readers; help.broadcast_changed when the broadcast changed (CONTRACTS 16).

    No port call here: the roles are the last ones the worker stored; the worker refreshes them after commit.
    """
    env = session.environment_id
    emit(
        deps,
        uow,
        EventType.HELP_SESSION_CHANGED,
        views.help_session(session, docs, ()),
        SESSION_AUDIENCE,
        session.help_session_id,
        "session",
        env,
    )
    stored = deps.sessions.broadcast_for(uow, session.help_session_id)
    roles = stored.roles if stored is not None else ()
    after = compose_broadcast(session, docs, roles)
    if stored != after:
        deps.sessions.save_broadcast(uow, after)
        emit(
            deps,
            uow,
            EventType.HELP_BROADCAST_CHANGED,
            views.broadcast(after),
            BROADCAST_AUDIENCE,
            after.broadcast_id,
            "broadcast",
            env,
        )
    if deps.roles is not None:
        deps.work.session_changed(session.help_session_id)
    return after
