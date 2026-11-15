"""E15 declarations. Host composition supplies durable storage and validated episode-rules pins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Protocol

from fastapi import APIRouter

from crew_health.episodes.adapters.sqlite_repo import EpisodeRepository
from crew_health.episodes.api.router import EpisodeApi, build_router
from crew_health.episodes.application import ports
from crew_health.episodes.application.dto import (
    AnswerFact,
    EpisodeFact,
    EpisodeRef,
    EpisodeToolFacts,
    LinkedFact,
    NewEpisode,
    Viewer,
)
from crew_health.episodes.application.effects import RunCorrectionEffects
from crew_health.episodes.application.handlers import EpisodeHandlers
from crew_health.episodes.application.maintenance import EpisodeMaintenance
from crew_health.episodes.application.queries import (
    EpisodeQueries,
    EpisodeReaderFacade,
    KernelFacade,
    RecordsSourceFacade,
    RefCheckFacade,
    ToolFactsFacade,
)
from crew_health.episodes.application.rules import EpisodeRulesSource
from crew_health.episodes.application.use_cases import EpisodeService, Limits, Providers
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState, ScheduledJob
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class StartupPhase(IntEnum):
    STATE_SWEEP = 40


class Registry(Protocol):
    def scheduler_handler(self, name: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None: ...
    def on_startup(self, phase: StartupPhase, fn: Callable[[], None]) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def router(self, router: APIRouter) -> None: ...
    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


# Bound source: E15 section 8. These are operational ceilings, never clinical timing.
_SETTINGS = (
    ("max_open_tasks", 50, 1, 1000),
    ("max_task_text_chars", 500, 1, 10000),
    ("max_answers_in_view", 2000, 1, 10000),
    ("max_links_per_episode", 1000, 1, 10000),
    ("effect_batch_size", 20, 1, 1000),
    ("effect_call_timeout_ms", 2000, 1, 30000),
    ("effect_retry_backoff_seconds", 5, 1, 3600),
    ("effect_max_attempts", 10, 1, 100),
    ("overdue_tick_seconds", 30, 1, 3600),
    ("overdue_batch_size", 100, 1, 1000),
)


def setting_defaults() -> dict[str, int]:
    return {"episodes." + key: default for key, default, _, _ in _SETTINGS}


def setting_specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            "episodes." + key,
            "integer",
            None,
            Decimal(low),
            Decimal(high),
            None,
            "team assumption: E15 section 8 operational bound " + str(default),
        )
        for key, default, low, high in _SETTINGS
    )


@dataclass(slots=True)
class Live:
    service: EpisodeService | None = None
    queries: EpisodeQueries | None = None
    handlers: EpisodeHandlers | None = None
    maintenance: EpisodeMaintenance | None = None


class Kernel:
    def __init__(self, live: Live) -> None:
        self.live = live

    def current(self) -> KernelFacade:
        if self.live.service is None or self.live.queries is None:
            raise RuntimeError("episode service not bound")
        return KernelFacade(self.live.service, self.live.queries)

    def create(self, uow: UnitOfWork, draft: NewEpisode) -> EpisodeRef:
        return self.current().create(uow, draft)

    def facts(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None:
        return self.current().facts(uow, episode_id)

    def record_route(
        self, uow: UnitOfWork, episode_id: str, module_id: str, area_answer_id: str, at: datetime
    ) -> None:
        self.current().record_route(uow, episode_id, module_id, area_answer_id, at)

    def view(self, uow: UnitOfWork, viewer: Viewer, episode_id: str) -> dict[str, object]:
        return self.current().view(uow, viewer, episode_id)


def _bind(live: Live, api: EpisodeApi) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        settings = resolver.require(HubSettings)  # type: ignore[type-abstract]
        rules = resolver.optional(EpisodeRulesSource)
        if rules is None:
            return
        limits = Limits(**{key: settings.integer("episodes." + key) for key, _, _, _ in _SETTINGS})
        providers = Providers(
            answers=resolver.optional(ports.AnswerStore),  # type: ignore[type-abstract]
            gaps=resolver.optional(ports.GapFacts),  # type: ignore[type-abstract]
            observations=resolver.optional(ports.ObservationFacts),  # type: ignore[type-abstract]
            urgency=resolver.optional(ports.UrgencyQueries),  # type: ignore[type-abstract]
            roster=resolver.optional(ports.RosterPort),  # type: ignore[type-abstract]
            consent=resolver.optional(ports.ConsentQueries),  # type: ignore[type-abstract]
            sensitivity=resolver.optional(ports.QuestionSensitivity),  # type: ignore[type-abstract]
            origin=resolver.optional(ports.OriginNamespace),  # type: ignore[type-abstract]
            assessments=resolver.optional(ports.AssessmentFacts),  # type: ignore[type-abstract]
            assessment_commands=resolver.optional(ports.AssessmentCommands),  # type: ignore[type-abstract]
            mission_clock=resolver.optional(ports.MissionClock),  # type: ignore[type-abstract]
            day_labels=resolver.optional(ports.DayLabelPort),  # type: ignore[type-abstract]
        )
        service = EpisodeService(
            EpisodeRepository(max_links=limits.max_links_per_episode, max_tasks=limits.max_open_tasks),
            resolver.require(IdFactory),  # type: ignore[type-abstract]
            rules,
            providers,
            limits,
            resolver.require(IdempotencyStore),  # type: ignore[type-abstract]
        )
        snapshots = resolver.require(ReadSnapshotFactory)  # type: ignore[type-abstract]
        live.service = service
        live.queries = EpisodeQueries(service, snapshots)
        live.handlers = EpisodeHandlers(service)
        health = resolver.require(HealthState)  # type: ignore[type-abstract]
        uows = resolver.require(UnitOfWorkFactory)  # type: ignore[type-abstract]
        effects = RunCorrectionEffects(service, uows, snapshots, health)
        live.maintenance = EpisodeMaintenance(service, effects, uows, health)
        api.service, api.queries = service, live.queries
        api.uows, api.snapshots = resolver.require(UnitOfWorkFactory), snapshots  # type: ignore[type-abstract]
        api.auth, api.fingerprint, api.ids = (
            resolver.optional(ports.SessionAuthenticator),  # type: ignore[type-abstract]
            resolver.optional(ports.CommandFingerprint),  # type: ignore[type-abstract]
            resolver.require(IdFactory),  # type: ignore[type-abstract]
        )

    return bind


def register(registry: Registry) -> None:
    live = Live()
    api = EpisodeApi()
    for spec in setting_specs():
        registry.setting(spec)
    for prefix in ("epi", "ack", "rev", "rop", "res", "tsk", "etl", "efx"):
        registry.id_prefix(record_type="episode." + prefix, prefix=prefix)
    registry.provide(ports.EpisodeKernel, lambda _: Kernel(live))  # type: ignore[type-abstract]

    def queries() -> EpisodeQueries:
        if live.queries is None:
            raise RuntimeError("episode queries not bound")
        return live.queries

    class Facts:
        def get(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None:
            return queries().get(uow, episode_id)

        def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool:
            return queries().visible_to(uow, episode_id, actor)

        def episode_of(self, uow: UnitOfWork, kind: str, ref_id: str) -> str | None:
            return queries().episode_of(uow, kind, ref_id)

    registry.provide(ports.EpisodeFacts, lambda _: Facts())  # type: ignore[type-abstract]

    class Tools:
        def facts(self, uow: UnitOfWork, episode_id: str, sections: frozenset[str]) -> EpisodeToolFacts:
            return queries().tool_facts(uow, episode_id, sections)

        def subject_of(self, uow: UnitOfWork, episode_id: str) -> str | None:
            return ToolFactsFacade(queries()).subject_of(uow, episode_id)

    registry.provide(ports.EpisodeFactsPort, lambda _: Tools())  # type: ignore[type-abstract]

    class Reader:
        def subject_of(self, episode_id: str) -> str | None:
            return EpisodeReaderFacade(queries()).subject_of(episode_id)

        def visible(self, session: SessionContext, episode_id: str) -> EpisodeRef | None:
            return EpisodeReaderFacade(queries()).visible(session, episode_id)

    registry.provide(ports.EpisodeReader, lambda _: Reader())  # type: ignore[type-abstract]

    class RefCheck:
        def visible(self, ref_id: str, actor: Actor) -> bool:
            return RefCheckFacade(queries()).visible(ref_id, actor)

    registry.provide(ports.RefCheckPort, lambda _: RefCheck())  # type: ignore[type-abstract]

    class RecordsSource:
        def items(self, uow: UnitOfWork, person_id: str, cursor: str | None, limit: int) -> dict[str, object]:
            return RecordsSourceFacade(queries()).items(uow, person_id, cursor, limit)

    registry.provide(ports.EpisodeRecordsSource, lambda _: RecordsSource())  # type: ignore[type-abstract]

    def answer(uow: UnitOfWork, event: AnswerFact) -> None:
        if live.handlers is None:
            raise RuntimeError("episode handlers not bound")
        live.handlers.answer(uow, event)

    def linked(uow: UnitOfWork, event: LinkedFact) -> None:
        if live.handlers is None:
            raise RuntimeError("episode handlers not bound")
        live.handlers.linked(uow, event)

    registry.subscribe(AnswerFact, answer, name="episodes.answer")
    registry.subscribe(LinkedFact, linked, name="episodes.linked")

    def tick(uow: UnitOfWork, job: ScheduledJob) -> None:
        if live.maintenance is None:
            raise RuntimeError("episode maintenance not bound")
        live.maintenance.tick(uow, job)

    def start() -> None:
        if live.maintenance is not None:
            live.maintenance.start()

    registry.scheduler_handler("episodes.maintenance", tick)
    registry.on_startup(StartupPhase.STATE_SWEEP, start)
    registry.bind("episodes", _bind(live, api))
    registry.router(build_router(api))
