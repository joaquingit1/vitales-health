# mypy: disable-error-code="type-abstract"
"""SYSTEM 5.12 and A6: declare under the consumer's exact port class, bind only after resolution."""

from datetime import datetime

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.catchup.adapters.sqlite_repository import SqliteCatchUpRepository
from crew_health.catchup.api.router import RoleGate, build_router
from crew_health.catchup.application.composer import SUBSCRIBER, CatchUpComposer, CatchUpDeps
from crew_health.catchup.application.log import GroundLogService
from crew_health.catchup.application.ports import (
    ConsentQueries,
    GroundLog,
    GroundLogView,
    PlanCatchUpPort,
    QuarantineRecord,
)
from crew_health.catchup.application.settings import CatchUpSettings, catchup_setting_specs
from crew_health.catchup.domain.errors import CatchUpUnavailable
from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.application.ports import BundleOutbox, GroundLinkQuery
from crew_health.help.application.ports import (
    CatchUpLines,
    GroundLogComposer,
    HelpLogSnapshots,
    HelpLogSnapshotView,
    LinkSnapshot,
)
from crew_health.kernel.clock import Clock
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshotFactory, StoredEvent, UnitOfWork, UnitOfWorkFactory


class CatchUpRuntime:
    def __init__(self) -> None:
        self._composer: CatchUpComposer | None = None
        self._log: GroundLogService | None = None
        self.gate: RoleGate | None = None

    @property
    def composer(self) -> CatchUpComposer:
        if self._composer is None:
            raise CatchUpUnavailable()
        return self._composer

    @property
    def deps(self) -> CatchUpDeps:
        return self.composer.deps

    @property
    def log_view(self) -> GroundLogView:
        if self._log is None:
            raise CatchUpUnavailable()
        return self._log

    def link(self) -> LinkSnapshot:
        return self.composer.link()

    def for_help(self, snapshot: HelpLogSnapshotView) -> CatchUpLines:
        return self.composer.for_help(snapshot)

    def log(self, since: datetime | None, *, recipient: str) -> GroundLog:
        return self.log_view.log(since, recipient=recipient)

    def quarantined(self, limit: int) -> tuple[QuarantineRecord, ...]:
        deps = self.deps
        with deps.snapshots.snapshot() as view:
            return deps.repository.quarantined(view, limit=limit)


def register(registry: ModuleRegistry) -> None:
    runtime = CatchUpRuntime()
    for spec in catchup_setting_specs():
        registry.setting(spec)
    registry.id_prefix(record_type="catchup_correlation", prefix="cuc")
    registry.router(build_router(runtime))
    registry.provide(CatchUpRuntime, lambda _: runtime)
    registry.provide(GroundLogComposer, lambda _: runtime)
    registry.provide(GroundLogView, lambda _: runtime)

    def changed(uow: UnitOfWork, event: StoredEvent) -> None:
        runtime.composer.on_ground_message_changed(uow, event)

    registry.durable_subscriber(SUBSCRIBER, frozenset({EventType.GROUND_MESSAGE_CHANGED}), changed)

    def bind(resolver: Resolver) -> None:
        settings = CatchUpSettings.read(resolver.require(HubSettings))
        deps = CatchUpDeps(
            resolver.require(UnitOfWorkFactory),
            resolver.require(ReadSnapshotFactory),
            settings,
            resolver.require(Clock),
            resolver.require(IdFactory),
            SqliteCatchUpRepository(settings.max_lines),
            resolver.optional(GroundLinkQuery),
            resolver.optional(BundleOutbox),
            resolver.optional(HelpLogSnapshots),
            resolver.optional(ConfigReader),
            resolver.optional(PlanCatchUpPort),
            resolver.optional(ConsentQueries),
        )
        runtime._composer = CatchUpComposer(deps)
        runtime._log = GroundLogService(deps, runtime.composer)
        runtime.gate = resolver.optional(RoleGate)

    registry.bind("catchup", bind)
