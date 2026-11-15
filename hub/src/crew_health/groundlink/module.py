# mypy: disable-error-code="type-abstract"
"""SYSTEM 5.12: declarations only at registration; late binding creates the services."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal, cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.adapters.audience import GroundLinkAudience
from crew_health.groundlink.adapters.sqlite_repository import SqliteGroundLinkRepository
from crew_health.groundlink.api.router import RoleGate, build_router
from crew_health.groundlink.application.agent import ContactAgent
from crew_health.groundlink.application.checks import LinkScheduleCheck
from crew_health.groundlink.application.config import LinkConfigCache
from crew_health.groundlink.application.ports import (
    BundleInbox,
    BundleOutbox,
    BundleStatus,
    GroundLinkQuery,
    GroundLinkView,
    InboundItem,
    LinkGap,
    MissionStatePort,
    OutboxItem,
    RefKind,
)
from crew_health.groundlink.application.repository import QuarantineRecord
from crew_health.groundlink.application.runtime import GroundLinkServices
from crew_health.groundlink.application.service import (
    GroundLinkCommands,
    GroundLinkDeps,
    GroundLinkReader,
    Inbox,
    Outbox,
    unavailable,
)
from crew_health.groundlink.application.settings import GroundLinkSettings, groundlink_setting_specs
from crew_health.groundlink.application.subscriber import DurableHandler
from crew_health.groundlink.application.worker import AgentLoop
from crew_health.help.application.ports import GroundMessageLifecyclePort
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    ReadSnapshot,
    ReadSnapshotFactory,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)


def provide_mission(
    registry: ModuleRegistry, factory: Callable[[Resolver], object], *, requires: tuple[type[object], ...]
) -> None:
    """E23's side of the MissionStatePort the ground link requires (SYSTEM 4). A mission provider binds here, so
    nothing outside the link's readers imports its ports (EI1, D43); this hook gives no access to link state."""
    registry.provide(cast(type[object], MissionStatePort), factory, requires=requires)


class GroundLinkRuntime:
    def __init__(self) -> None:
        self._services: GroundLinkServices | None = None
        self.gate: RoleGate | None = None
        self.loop: AgentLoop | None = None
        self.subscribers: dict[str, DurableHandler] = {}

    @property
    def services(self) -> GroundLinkServices:
        if self._services is None:
            raise unavailable()
        return self._services

    @property
    def agent(self) -> ContactAgent:
        return self.services.agent

    def current(self, view: ReadSnapshot) -> GroundLinkView | None:
        return self.services.reader.current(view)

    def last_gap(self, view: ReadSnapshot) -> LinkGap | None:
        return self.services.reader.last_gap(view)

    def enqueue(self, uow: UnitOfWork, item: OutboxItem) -> str:
        return self.services.outbox.enqueue(uow, item)

    def status(self, view: ReadSnapshot, ref_kind: RefKind, ref_id: str) -> BundleStatus | None:
        return self.services.outbox.status(view, ref_kind, ref_id)

    def delivered(
        self, view: ReadSnapshot, *, since: datetime | None, limit: int
    ) -> tuple[BundleStatus, ...]:
        return self.services.outbox.delivered(view, since=since, limit=limit)

    def quarantined(self, view: ReadSnapshot, limit: int) -> tuple[QuarantineRecord, ...]:
        return self.services.deps.repository.quarantined(view, limit)

    def receive(self, item: InboundItem) -> Literal["accepted", "duplicate"]:
        return self.services.inbox.receive(item)


def register(registry: ModuleRegistry) -> None:
    runtime = GroundLinkRuntime()
    for spec in groundlink_setting_specs():
        registry.setting(spec)
    for record_type, prefix in (("gl_bundle", "glb"), ("gl_ack", "gla"), ("gl_correlation", "glc")):
        registry.id_prefix(record_type=record_type, prefix=prefix)
    registry.audience_rule("groundlink.link", GroundLinkAudience())
    registry.loader_check(LinkScheduleCheck())
    registry.router(build_router(runtime))
    registry.provide(GroundLinkRuntime, lambda _: runtime)
    registry.provide(GroundLinkQuery, lambda _: runtime)
    registry.provide(BundleOutbox, lambda _: runtime)
    registry.provide(BundleInbox, lambda _: runtime)

    def environment(uow: UnitOfWork, event: StoredEvent) -> None:
        runtime.subscribers["groundlink.environment"].deliver(
            uow, event, runtime.services.commands.on_environment_activated
        )

    def outbox(uow: UnitOfWork, event: StoredEvent) -> None:
        runtime.subscribers["groundlink.outbox"].deliver(
            uow, event, runtime.services.commands.on_ground_message_changed
        )

    registry.durable_subscriber(
        "groundlink.environment", frozenset({EventType.ENVIRONMENT_ACTIVATED}), environment
    )
    registry.durable_subscriber("groundlink.outbox", frozenset({EventType.GROUND_MESSAGE_CHANGED}), outbox)

    def bind(resolver: Resolver) -> None:
        settings = GroundLinkSettings.read(resolver.require(HubSettings))
        deps = GroundLinkDeps(
            resolver.require(UnitOfWorkFactory),
            resolver.require(ReadSnapshotFactory),
            settings,
            resolver.require(IdFactory),
            resolver.require(Clock),
            resolver.require(IdempotencyStore),
            SqliteGroundLinkRepository(settings),
            LinkConfigCache(),
            resolver.optional(MissionStatePort),
            resolver.optional(GroundMessageLifecyclePort),
        )
        reader = GroundLinkReader(deps)
        runtime._services = GroundLinkServices(
            deps, reader, GroundLinkCommands(deps, reader), Outbox(deps), Inbox(deps), ContactAgent(deps)
        )
        runtime.subscribers = {
            name: DurableHandler(deps, name, resolver.require(HubSettings))
            for name in ("groundlink.environment", "groundlink.outbox")
        }
        runtime.gate = resolver.optional(RoleGate)
        if settings.agent_enabled:
            runtime.loop = AgentLoop(runtime.agent, settings.agent_poll_ms)
            runtime.loop.start()

    registry.bind("groundlink", bind)
