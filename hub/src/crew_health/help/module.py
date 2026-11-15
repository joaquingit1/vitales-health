"""Help module registration (SYSTEM 4.2): settings, id prefixes, audience rules, provided ports and the router.

The service binds late, after every provider resolved, so each optional port is looked up once and may be absent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, Protocol

from fastapi import APIRouter

from crew_health.help.adapters.audience import BroadcastRule, GroundMessageRule, SessionReaderRule
from crew_health.help.adapters.sqlite_repository import SqliteGroundMessages, SqliteHelpSessions
from crew_health.help.api.router import HelpRuntime, RoleGate, build_router
from crew_health.help.application import ports
from crew_health.help.application.service import HelpService
from crew_health.help.application.settings import help_setting_specs
from crew_health.help.application.support import (
    BROADCAST_AUDIENCE,
    GROUND_AUDIENCE,
    LOCAL_AUDIENCE,
    SESSION_AUDIENCE,
    HelpDeps,
)
from crew_health.help.audit_policies import audit_policies
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReader, ConfigReleaseReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import Actor, AudienceRule, ReadSnapshotFactory, SessionContext, UnitOfWorkFactory


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


# Provided by E01 (crew_health.app.registry); structural copy of the methods help uses (SYSTEM 4.2 ModuleRegistry).
# A feature module may not import crew_health.app, so no startup hook is declared here (its StartupPhase type is
# E01's): the help worker starts from the bind callback, once every port resolved.
class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def audit_policy(self, policy: AuditPolicy) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


class _Reader:
    def __init__(self, runtime: HelpRuntime) -> None:
        self._runtime = runtime

    def context(self, session: SessionContext, help_session_id: str) -> ports.HelpContext:
        return self._runtime.service.reader.context(session, help_session_id)

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        return self._runtime.service.reader.readable_by(session, help_session_id)

    def snapshot(self, help_session_id: str) -> ports.HelpSessionSnapshot | None:
        return self._runtime.service.reader.snapshot(help_session_id)

    def qualifying_sessions(self) -> Sequence[ports.HelpFlowRef]:
        return self._runtime.service.reader.qualifying_sessions()


class _Entry:
    def __init__(self, runtime: HelpRuntime) -> None:
        self._runtime = runtime

    def open_from_red_flag(
        self,
        episode_id: str,
        answer_id: str,
        subject_person_id: str,
        flow_id: str,
        client_time: datetime,
        idempotency_key: str,
    ) -> str:
        return self._runtime.service.entry.open_from_red_flag(
            episode_id, answer_id, subject_person_id, flow_id, client_time, idempotency_key
        )


class _Snapshots:
    def __init__(self, runtime: HelpRuntime) -> None:
        self._runtime = runtime

    def for_message(self, ground_message_id: str) -> ports.HelpLogSnapshotView | None:
        return self._runtime.service.snapshots.for_message(ground_message_id)


class _Lifecycle:
    def __init__(self, runtime: HelpRuntime) -> None:
        self._runtime = runtime

    def mark_sent(
        self,
        message_id: str,
        event_id: str,
        sent_at: datetime,
        one_way_delay_s: float | None,
        simulated: bool,
    ) -> ports.EventOutcome:
        return self._runtime.service.lifecycle.mark_sent(
            message_id, event_id, sent_at, one_way_delay_s, simulated
        )

    def record_arrival(self, message_id: str, event_id: str, arrived_at: datetime) -> ports.EventOutcome:
        return self._runtime.service.lifecycle.record_arrival(message_id, event_id, arrived_at)

    def record_receipt(
        self, message_id: str, event_id: str, received_at: datetime, arrived_at: datetime
    ) -> ports.EventOutcome:
        return self._runtime.service.lifecycle.record_receipt(message_id, event_id, received_at, arrived_at)

    def record_reply(
        self, message_id: str, event_id: str, text: str, by: Actor, replied_at: datetime, arrived_at: datetime
    ) -> ports.EventOutcome:
        return self._runtime.service.lifecycle.record_reply(
            message_id, event_id, text, by, replied_at, arrived_at
        )


class _Sink:
    def __init__(self, runtime: HelpRuntime) -> None:
        self._runtime = runtime

    def turn_started(self, help_turn_id: str) -> None:
        self._runtime.service.assistant.turn_started(help_turn_id)

    def turn_completed(self, help_turn_id: str, candidate: ports.HelpTurnCandidate) -> ports.HelpGuardResult:
        return self._runtime.service.assistant.turn_completed(help_turn_id, candidate)

    def turn_failed(self, help_turn_id: str, error_code: str) -> None:
        self._runtime.service.assistant.turn_failed(help_turn_id, error_code)

    def runtime_status(
        self, status: Literal["off", "waiting", "answering", "stopped", "unavailable"]
    ) -> None:
        self._runtime.service.assistant.runtime_status(status)

    def check(self, help_session_id: str, candidate: ports.HelpTurnCandidate) -> ports.HelpGuardResult:
        return self._runtime.service.assistant.check(help_session_id, candidate)


def build_service(resolver: Resolver) -> HelpService:
    deps = HelpDeps(
        uows=resolver.require(UnitOfWorkFactory),  # type: ignore[type-abstract]
        snapshots=resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
        config=resolver.require(ConfigReader),  # type: ignore[type-abstract]
        settings=resolver.require(HubSettings),  # type: ignore[type-abstract]
        ids=resolver.require(IdFactory),  # type: ignore[type-abstract]
        clock=resolver.require(Clock),  # type: ignore[type-abstract]
        commands=resolver.require(IdempotencyStore),  # type: ignore[type-abstract]
        receipts=resolver.require(CommandReceipts),  # type: ignore[type-abstract]
        sessions=SqliteHelpSessions(),
        messages=SqliteGroundMessages(),
        mission_clock=resolver.optional(MissionClock),  # type: ignore[type-abstract]
        mission=resolver.optional(ports.MissionStatePort),  # type: ignore[type-abstract]
        roster=resolver.optional(ports.RosterPort),  # type: ignore[type-abstract]
        roles=resolver.optional(ports.BroadcastRolesSource),  # type: ignore[type-abstract]
        assistant=resolver.optional(ports.HelpAssistantPort),  # type: ignore[type-abstract]
        warnings=resolver.optional(ports.WarningQueries),  # type: ignore[type-abstract]
        composer=resolver.optional(ports.GroundLogComposer),  # type: ignore[type-abstract]
        text_rules=resolver.optional(ports.TextRulesPort),  # type: ignore[type-abstract]
        measurement_gate=resolver.optional(ports.MeasurementGate),  # type: ignore[type-abstract]
        observations=resolver.optional(ports.ObservationReadPort),  # type: ignore[type-abstract]
        profile=resolver.optional(ports.ProfileFactsPort),  # type: ignore[type-abstract]
    )
    return HelpService(deps, resolver.optional(ConfigReleaseReader))  # type: ignore[type-abstract]


def _declare_ports(registry: ModuleRegistry, runtime: HelpRuntime) -> None:
    registry.provide(ports.HelpSessionReader, lambda _: _Reader(runtime))  # type: ignore[type-abstract]
    registry.provide(ports.HelpEntryPort, lambda _: _Entry(runtime))  # type: ignore[type-abstract]
    registry.provide(ports.HelpLogSnapshots, lambda _: _Snapshots(runtime))  # type: ignore[type-abstract]
    registry.provide(ports.GroundMessageLifecyclePort, lambda _: _Lifecycle(runtime))  # type: ignore[type-abstract]
    registry.provide(ports.HelpAssistantSink, lambda _: _Sink(runtime))  # type: ignore[type-abstract]
    registry.provide(ports.HelpTurnGuardPort, lambda _: _Sink(runtime))  # type: ignore[type-abstract]


class _LocalOnly:
    """The audience of help's audit reference declarations: no session is ever admitted to them."""

    def admits(self, snapshot: object, session: SessionContext, audience: object) -> bool:
        return False


def register(registry: ModuleRegistry) -> None:
    for spec in help_setting_specs():
        registry.setting(spec)
    for record_type, prefix in (
        ("help_session", "hlp"),
        ("help_broadcast", "brd"),
        ("ground_message", "gm"),
        ("help_turn", "hlt"),
        ("help_audit", "hla"),
        ("help_correlation", "hlc"),
    ):
        registry.id_prefix(record_type=record_type, prefix=prefix)
    registry.audience_rule(SESSION_AUDIENCE, SessionReaderRule())
    registry.audience_rule(BROADCAST_AUDIENCE, BroadcastRule())
    registry.audience_rule(GROUND_AUDIENCE, GroundMessageRule())
    registry.audience_rule(LOCAL_AUDIENCE, _LocalOnly())
    for policy in audit_policies():
        registry.audit_policy(policy)
    runtime = HelpRuntime()
    _declare_ports(registry, runtime)
    registry.router(build_router(runtime))

    def bind(resolver: Resolver) -> None:
        gate: RoleGate | None = resolver.optional(RoleGate)  # type: ignore[type-abstract]
        service = build_service(resolver)
        runtime.bind(service, gate)
        # Started once every port resolved; its first pass is the startup recovery of E26 3.6.
        service.worker.start()

    registry.bind("help", bind)
