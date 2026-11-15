# mypy: disable-error-code="type-abstract"
"""Installed E13 capabilities. Unfinished clinical commands remain explicitly unavailable."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from fastapi import APIRouter

from crew_health.app.registry import StartupPhase as Phase
from crew_health.assistant.adapters.calls import BoundedCalls
from crew_health.assistant.adapters.conversations import (
    Bounds,
    Conversations,
    EpisodeAccess,
    PersonOrigin,
    TurnAnchor,
    TurnPorts,
)
from crew_health.assistant.adapters.facade import AssistantFacade
from crew_health.assistant.adapters.runtime_worker import RuntimeWorker
from crew_health.assistant.adapters.store import Store
from crew_health.assistant.api.router import AssistantApi, build_router
from crew_health.assistant.application.access import Access
from crew_health.assistant.application.assessment_queries import AssessmentQueries
from crew_health.assistant.application.http_ports import (
    AssistantHttp,
    CommandRequest,
    HttpResult,
    QueryParameters,
)
from crew_health.assistant.application.json_data import JsonBounds
from crew_health.assistant.application.ports import (
    AssistantStatusPort,
    CommandFingerprint,
    DeliveredAssessmentSource,
    RecordAuthorization,
    SelectedAssessment,
    SelectedAssessmentFacts,
    SessionReader,
)
from crew_health.assistant.application.runtime_actions import RuntimeActions
from crew_health.assistant.application.tools_copies import EffectApplier, OutputPolicyPort, ToolGateway
from crew_health.episodes.application.ports import AssessmentCommands, AssessmentFacts
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState, WorkerLifecycle
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import (
    Actor,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.model.application.ports import (
    InferencePort,
    ModelInstallation,
    ModelReleaseReader,
    ModelRuntimePort,
    PromptPort,
)


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class Registry(Protocol):
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def on_startup(self, phase: Phase, fn: Callable[[], None]) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def router(self, router: APIRouter) -> None: ...


@dataclass
class Live:
    facade: AssistantFacade | None = None
    queries: AssessmentQueries | None = None
    store: Store | None = None
    actions: RuntimeActions | None = None
    worker: RuntimeWorker | None = None
    uows: UnitOfWorkFactory | None = None
    actor: Actor | None = None
    lifecycle: WorkerLifecycle | None = None
    conversations: Conversations | None = None


def register(registry: Registry) -> None:
    live = Live()

    class Bound:
        def current(self) -> AssistantFacade:
            if live.facade is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant not bound")
            return live.facade

        def query(
            self, session: SessionContext, operation: str, target: str | None, parameters: QueryParameters
        ) -> HttpResult:
            return self.current().query(session, operation, target, parameters)

        def command(
            self, session: SessionContext, operation: str, target: str | None, request: CommandRequest
        ) -> HttpResult:
            return self.current().command(session, operation, target, request)

        def stream(self, session: SessionContext, conversation_id: str) -> Iterator[bytes]:
            return self.current().stream(session, conversation_id)

        def state(self) -> Literal["running", "stopped", "loading", "unavailable"]:
            if live.actions is None or live.facade is None:
                return "unavailable"
            with live.facade.views.snapshot() as view:
                return live.actions.current(view).runtime.state.value

    bound = Bound()
    api = AssistantApi(bound, None, None, 1)
    registry.provide(AssistantHttp, lambda _: bound)
    registry.provide(AssistantStatusPort, lambda _: bound)

    class Selected:
        def read(
            self,
            view: ReadSnapshot | UnitOfWork,
            session: SessionContext,
            *,
            assessment_id: str,
            fields: frozenset[str],
        ) -> SelectedAssessment:
            if live.queries is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assessment reader not bound")
            return live.queries.read(view, session, assessment_id=assessment_id, fields=fields)

    registry.provide(SelectedAssessmentFacts, lambda _: Selected())

    class Assessments:
        def citing(
            self, uow: ReadSnapshot | UnitOfWork, episode_id: str, record_ids: Sequence[str]
        ) -> Sequence[str]:
            if live.store is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assessment store not bound")
            return live.store.citing(uow, episode_id, record_ids)

        def ids_for_episode(self, uow: ReadSnapshot | UnitOfWork, episode_id: str) -> Sequence[str]:
            if live.store is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assessment store not bound")
            return live.store.assessment_ids(uow, episode_id)

        def mark_stale(self, uow: UnitOfWork, assessment_id: str, cause_ref: str) -> None:
            if live.store is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assessment store not bound")
            value = live.store.assessment(uow, assessment_id)
            if value is not None and value.validation_status != "stale":
                live.store.mark_assessment(uow, assessment_id, state="stale", cause=cause_ref)

        def mark_reviewed(self, uow: UnitOfWork, assessment_id: str, review_id: str) -> None:
            if live.store is None:
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assessment store not bound")
            value = live.store.assessment(uow, assessment_id)
            if value is not None and value.validation_status == "draft":
                live.store.mark_assessment(uow, assessment_id, state="reviewed", review=review_id)

    registry.provide(AssessmentFacts, lambda _: Assessments())
    registry.provide(AssessmentCommands, lambda _: Assessments())
    bounds = {
        "health_fail_threshold": (1, 10),
        "probe_interval_ms": (10, 60000),
        "probe_timeout_ms": (1, 2000),
        "load_timeout_ms": (1, 300000),
        "dependency_workers": (1, 2),
        "max_rows": (1, 100),
        "max_queued_turns": (1, 16),
        "max_context_messages": (1, 32),
    }
    for key, (low, high) in bounds.items():
        registry.setting(
            SettingSpec(
                "assistant." + key,
                "integer",
                None,
                Decimal(low),
                Decimal(high),
                None,
                "Team assumption: bounded E13 runtime operations; E13 sections3.9/5.2",
            )
        )

    def bind(resolver: Resolver) -> None:
        settings = resolver.require(HubSettings)
        ids = resolver.require(IdFactory)
        views = resolver.require(ReadSnapshotFactory)
        uows = resolver.require(UnitOfWorkFactory)
        installation = resolver.optional(ModelInstallation)
        state = Store(
            max_rows=settings.integer("assistant.max_rows"),
            max_body_bytes=settings.integer("commands.max_bytes"),
        )
        actions = RuntimeActions(
            state,
            profile=installation.profile if installation else "flight",
            health_fail_threshold=settings.integer("assistant.health_fail_threshold"),
        )
        queries = AssessmentQueries(
            state,
            Access(resolver.optional(SessionReader), resolver.optional(RecordAuthorization)),
            delivered=resolver.optional(DeliveredAssessmentSource),
            max_bytes=settings.integer("commands.max_bytes"),
        )
        worker = None
        actor = Actor("engine", ids.new("worker"))
        if installation is not None:
            worker = RuntimeWorker(
                state=state,
                actions=actions,
                uows=uows,
                views=views,
                clock=resolver.require(Clock),
                model=resolver.require(ModelRuntimePort),
                calls=BoundedCalls(
                    resolver.require(Clock),
                    max_inflight=settings.integer("assistant.dependency_workers"),
                    max_duration_ms=settings.integer("assistant.load_timeout_ms"),
                    cancel_poll_ms=min(
                        settings.integer("model.cancel_poll_ms"),
                        settings.integer("assistant.load_timeout_ms"),
                    ),
                ),
                health=resolver.require(HealthState),
                actor=actor,
                interval_ms=settings.integer("assistant.probe_interval_ms"),
                probe_ms=settings.integer("assistant.probe_timeout_ms"),
                load_ms=settings.integer("assistant.load_timeout_ms"),
            )
        conversations = _conversations(resolver, settings, state, actions, views, uows, ids)
        facade = AssistantFacade(
            state=state,
            actions=actions,
            queries=queries,
            views=views,
            uows=uows,
            ids=ids,
            ledger=resolver.require(IdempotencyStore),
            releases=resolver.require(ModelReleaseReader),
            worker=worker,
            max_bytes=settings.integer("commands.max_bytes"),
            conversations=conversations,
        )
        live.conversations = conversations
        live.facade, live.queries, live.store, live.actions, live.worker, live.uows, live.actor = (
            facade,
            queries,
            state,
            actions,
            worker,
            uows,
            actor,
        )
        live.lifecycle = resolver.require(WorkerLifecycle)
        api.facade, api.ids, api.fingerprint, api.max_body_bytes = (
            bound,
            ids,
            resolver.optional(CommandFingerprint),
            settings.integer("commands.max_bytes"),
        )

    def boot() -> None:
        if live.uows is None or live.actor is None or live.actions is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant not bound")
        with live.uows.begin(actor=live.actor, correlation_id=live.actor.id) as uow:
            live.actions.boot(uow)

    def start() -> None:
        if live.worker is not None and live.lifecycle is not None:
            live.worker.start()
            live.lifecycle.started("assistant.runtime", live.worker.stop)
        if live.conversations is not None and live.lifecycle is not None:
            live.conversations.start()
            live.lifecycle.started("assistant.turns", live.conversations.stop)

    registry.bind("assistant", bind)
    registry.on_startup(Phase.STATE_SWEEP, boot)
    registry.on_startup(Phase.INGEST, start)
    registry.router(build_router(api))


def _conversations(
    resolver: Resolver,
    settings: HubSettings,
    state: Store,
    actions: RuntimeActions,
    views: ReadSnapshotFactory,
    uows: UnitOfWorkFactory,
    ids: IdFactory,
) -> Conversations | None:
    """The episode intake conversation, only when every seam it needs is bound; otherwise its routes stay
    explicitly unavailable (CAPABILITY_UNAVAILABLE), never a local stand-in."""
    gateway, effects = resolver.optional(ToolGateway), resolver.optional(EffectApplier)
    policy, episodes = resolver.optional(OutputPolicyPort), resolver.optional(EpisodeAccess)
    anchor, origins = resolver.optional(TurnAnchor), resolver.optional(PersonOrigin)
    prompts, inference = resolver.optional(PromptPort), resolver.optional(InferencePort)
    if (
        gateway is None
        or effects is None
        or policy is None
        or episodes is None
        or anchor is None
        or origins is None
        or prompts is None
        or inference is None
    ):
        return None
    limit = settings.integer("commands.max_bytes")
    return Conversations(
        state=state,
        actions=actions,
        views=views,
        uows=uows,
        ids=ids,
        clock=resolver.require(Clock),
        releases=resolver.require(ModelReleaseReader),
        ports=TurnPorts(gateway, effects, policy, episodes, anchor, origins, prompts, inference),
        bounds=Bounds(
            settings.integer("assistant.max_queued_turns"),
            settings.integer("assistant.max_context_messages"),
            limit,
            JsonBounds(limit, 32, 4096, 256, limit),
        ),
    )
