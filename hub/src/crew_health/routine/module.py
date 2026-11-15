# mypy: disable-error-code="type-abstract"
"""Routine declarations and late binding, with no work at import time."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fastapi import Request
from pydantic import ValidationError

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.contracts_gen.schemas.routine_occurrence_schema import RoutineOccurrence
from crew_health.contracts_gen.schemas.routine_override_schema import RoutineOverride
from crew_health.kernel.clock import Clock, ClockCorrected, ClockRegressed
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationInput, EvaluatorRegistration
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.scheduler import HealthState, ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    LocalProjectionRegistration,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.routine.adapters.config_reader import ReleaseCache, RoutineReleaseCheck
from crew_health.routine.adapters.sqlite_repository import SqliteRoutineStore
from crew_health.routine.adapters.wire_inputs import parse_input
from crew_health.routine.api.router import build_router
from crew_health.routine.application.actions import RoutineActions
from crew_health.routine.application.audit import audit
from crew_health.routine.application.checks import CheckNotifications
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.application.evaluator import RoutineEvaluator
from crew_health.routine.application.ports import (
    AddedQuestion,
    AssistantRoutinePort,
    DailyCheckAbandoned,
    DailyCheckQuestionSource,
    DailyCheckSubmitted,
    DrillOccurrence,
    LocationPort,
    MissionStatePort,
    OccurrenceActionBody,
    OccurrenceRef,
    OccurrenceView,
    OverrideCreateBody,
    OverrideView,
    ReactionTestRecorded,
    RecordFacts,
    RequestAuthenticator,
    RosterPort,
    RoutineCommands,
    RoutineCompletionPort,
    RoutineQueries,
    RoutineTodayPort,
    TodayRoutinePart,
    Visibility,
    WarningCommands,
)
from crew_health.routine.application.privacy import audit_care, care_granted, read_override
from crew_health.routine.application.provided import (
    AssistantRechecks,
    DailyCheckQuestions,
    RoutineCompletion,
    RoutineToday,
)
from crew_health.routine.application.queries import RoutineQueryService
from crew_health.routine.application.settings import RoutineSettings, specs
from crew_health.routine.application.subscriptions import RoutineSubscriptions
from crew_health.routine.domain.loader_checks import Bounds


def _need[T](service: T | None) -> T:
    if service is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "routine is not bound", retryable=True)
    return service


class RoutineAudience:
    def __init__(self, *, override: bool, runtime: RoutineRuntime | None = None) -> None:
        self.override = override
        self.runtime = runtime
        self.store = SqliteRoutineStore()

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        rule = "routine.override" if self.override else "routine.occurrence"
        projection = "routine.override_changed" if self.override else "routine.occurrence_changed"
        if (
            audience.policy_version != "1"
            or audience.rule != rule
            or audience.projection.owner != "routine"
            or audience.projection.name != projection
            or audience.projection.version != 1
        ):
            return False
        if not self.override:
            occurrence = self.store.get(snapshot, audience.object_ref)
            if occurrence is None:
                return False
            if session.person_id == occurrence.person_id:
                return True
            deps = None if self.runtime is None or self.runtime._engine is None else self.runtime.engine.deps
            if deps is not None and care_granted(session, occurrence.occurrence_id, deps.visibility):
                audit_care(deps, session)
                return True
            return False
        override = self.store.get_override(snapshot, audience.object_ref)
        if override is None:
            return False
        if self.runtime is None or self.runtime._engine is None:
            from crew_health.routine.application.privacy import override_visible

            return override_visible(override, session)
        return read_override(self.runtime.engine.deps, override, session)


class RoutineRuntime:
    """The layer C mounting seam; every facade fails loudly until bind completes."""

    def __init__(self) -> None:
        self.startup_phase = StartupPhase.STATE_SWEEP
        self._bound = False
        self.authenticator: RequestAuthenticator | None = None
        self._engine: RoutineEngine | None = None
        self._queries: RoutineQueryService | None = None
        self._commands: RoutineActions | None = None
        self._today: RoutineToday | None = None
        self._questions: DailyCheckQuestions | None = None
        self._completion: RoutineCompletion | None = None
        self._assistant: AssistantRechecks | None = None
        self._subscriptions: RoutineSubscriptions | None = None
        self._evaluator: RoutineEvaluator | None = None
        self._checks: CheckNotifications | None = None

    @property
    def engine(self) -> RoutineEngine:
        return _need(self._engine)

    @property
    def queries(self) -> RoutineQueryService:
        return _need(self._queries)

    @property
    def commands(self) -> RoutineActions:
        return _need(self._commands)

    def assemble(self, deps: RoutineDeps) -> None:
        engine = RoutineEngine(deps)
        self._engine = engine
        self._queries, self._commands = RoutineQueryService(deps, engine), RoutineActions(deps, engine)
        self._today, self._questions = RoutineToday(deps, engine), DailyCheckQuestions(deps, engine)
        self._completion, self._assistant = RoutineCompletion(deps, engine), AssistantRechecks(deps, engine)
        self._subscriptions = RoutineSubscriptions(deps, engine)
        self._evaluator = RoutineEvaluator(deps, engine)
        self._checks = CheckNotifications(engine)
        engine.startup_sweep()
        self._bound = True

    def authenticate(self, request: Request, operation_id: str) -> SessionContext:
        if not self._bound:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "routine is not bound", retryable=True)
        if self.authenticator is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return self.authenticator.authenticate(request.headers, operation_id)

    def active_release_id(self) -> str:
        return self.queries.active_release_id()

    def schedule(self, session: SessionContext, range_: str) -> dict[str, object]:
        return self.queries.schedule(session, range_)

    def occurrences(
        self, session: SessionContext, range_: str, state: str | None, cursor: str | None, limit: int
    ) -> dict[str, object]:
        return self.queries.occurrences(session, range_, state, cursor, limit)

    def occurrence(self, session: SessionContext, occurrence_id: str) -> OccurrenceView:
        return self.queries.occurrence(session, occurrence_id)

    def tasks(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]:
        return self.queries.tasks(session, cursor, limit)

    def task(self, session: SessionContext, item_id: str) -> dict[str, object]:
        return self.queries.task(session, item_id)

    def overrides(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]:
        return self.queries.overrides(session, cursor, limit)

    def act(
        self,
        session: SessionContext,
        occurrence_id: str,
        body: OccurrenceActionBody,
        *,
        if_match: int | None,
        idempotency_key: str,
        config_release: str | None,
    ) -> tuple[int, OccurrenceView]:
        return self.commands.act(
            session,
            occurrence_id,
            body,
            if_match=if_match,
            idempotency_key=idempotency_key,
            config_release=config_release,
        )

    def create_override(
        self,
        session: SessionContext,
        body: OverrideCreateBody,
        *,
        idempotency_key: str,
        config_release: str | None,
    ) -> tuple[int, OverrideView]:
        return self.commands.create_override(
            session, body, idempotency_key=idempotency_key, config_release=config_release
        )

    def end_override(
        self,
        session: SessionContext,
        override_id: str,
        *,
        if_match: int | None,
        idempotency_key: str,
        config_release: str | None = None,
        note: str | None = None,
    ) -> tuple[int, OverrideView]:
        return self.commands.end_override(
            session,
            override_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            config_release=config_release,
            note=note,
        )

    def due_today(self, person_id: str, at: datetime) -> Sequence[OccurrenceRef]:
        return _need(self._today).due_today(person_id, at)

    def today_summary(self, session: SessionContext, at: datetime) -> TodayRoutinePart:
        return _need(self._today).today_summary(session, at)

    def added_questions(self, person_id: str, mission_day: int, at: datetime) -> Sequence[AddedQuestion]:
        return _need(self._questions).added_questions(person_id, mission_day, at)

    def drill_occurrence(self, occurrence_id: str) -> DrillOccurrence | None:
        return _need(self._completion).drill_occurrence(occurrence_id)

    def complete(self, occurrence_id: str, completed_at: datetime, actor: Actor) -> bool:
        return _need(self._completion).complete(occurrence_id, completed_at, actor)

    def add_recheck(
        self, uow: UnitOfWork, *, person_id: str, item_id: str, insight_id: str, due_from: datetime
    ) -> OccurrenceRef:
        return _need(self._assistant).add_recheck(
            uow, person_id=person_id, item_id=item_id, insight_id=insight_id, due_from=due_from
        )

    def occurrence_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.engine.occurrence_job(uow, job)

    def day_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.engine.day_job(uow, job)

    def override_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.engine.override_job(uow, job)

    def instance_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.engine.instance_job(uow, job)

    def event(self, uow: UnitOfWork, event: StoredEvent) -> None:
        subs = _need(self._subscriptions)
        try:
            inp = parse_input(event)
        except ValidationError, ValueError, UnicodeError:
            audit(subs.deps, uow, "input_unreadable")
            return
        if inp is not None:
            subs.input(uow, inp)
        else:
            subs.release(uow, event)

    def evaluate(self, uow: UnitOfWork, value: EvaluationInput) -> None:
        _need(self._evaluator).evaluate(uow, value)

    def activation_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.engine.activations.job(uow, job)

    def submitted(self, uow: UnitOfWork, event: DailyCheckSubmitted) -> None:
        _need(self._subscriptions).isolated(uow, lambda: _need(self._checks).submitted(uow, event))

    def abandoned(self, uow: UnitOfWork, event: DailyCheckAbandoned) -> None:
        _need(self._subscriptions).isolated(uow, lambda: None)

    def reaction(self, uow: UnitOfWork, event: ReactionTestRecorded) -> None:
        _need(self._subscriptions).isolated(uow, lambda: _need(self._checks).reaction(uow, event))

    def regressed(self, uow: UnitOfWork, event: ClockRegressed) -> None:
        _need(self._subscriptions).regressed(uow, event)

    def corrected(self, uow: UnitOfWork, event: ClockCorrected) -> None:
        _need(self._subscriptions).corrected(uow, event)


def _deps(resolver: Resolver) -> RoutineDeps:
    settings = RoutineSettings.read(resolver.require(HubSettings))
    return RoutineDeps(
        resolver.require(UnitOfWorkFactory),
        resolver.require(ReadSnapshotFactory),
        resolver.require(Clock),
        resolver.require(MissionClock),
        resolver.require(IdFactory),
        settings,
        resolver.require(IdempotencyStore),
        resolver.optional(CommandReceipts),
        resolver.require(HealthState),
        ReleaseCache(
            settings.release_cache_entries,
            Bounds(settings.max_items, settings.max_bindings, settings.max_slots_per_binding),
            resolver.optional(ConfigReleaseReader),
        ),
        SqliteRoutineStore(),
        resolver.optional(MissionStatePort),
        resolver.optional(RosterPort),
        resolver.optional(LocationPort),
        resolver.optional(Visibility),
        resolver.optional(RecordFacts),
        resolver.optional(WarningCommands),
    )


def register(registry: ModuleRegistry) -> None:
    runtime = RoutineRuntime()
    registry.router(build_router(runtime, runtime, runtime.authenticate))
    for spec in specs():
        registry.setting(spec)
    for record_type, prefix in (
        ("routine_occurrence", "ro"),
        ("routine_override", "rov"),
        ("routine_question_instance", "rq"),
        ("routine_cadence_activation", "rca"),
    ):
        registry.id_prefix(record_type=record_type, prefix=prefix)
    maxima = {spec.key: int(spec.maximum) for spec in specs() if spec.maximum is not None}
    registry.loader_check(
        RoutineReleaseCheck(
            Bounds(
                maxima["routine.max_items"],
                maxima["routine.max_bindings"],
                maxima["routine.max_slots_per_binding"],
            )
        )
    )
    for name, model, pointers, override in (
        ("occurrence", RoutineOccurrence, ("/occurrence_id", "/revision", "/person_id", "/state"), False),
        (
            "override",
            RoutineOverride,
            ("/override_id", "/revision", "/audience", "/person_id", "/state"),
            True,
        ),
    ):
        rule = RoutineAudience(override=override, runtime=runtime)
        registry.audience_rule("routine." + name, rule)
        registry.local_projection(
            LocalProjectionRegistration(
                AudienceProjectionKey("routine", "routine." + name + "_changed", 1),
                canonical_json(model.model_json_schema()),
                pointers,
                rule,
            )
        )
    registry.scheduler_handler("routine.occurrence", runtime.occurrence_job)
    registry.scheduler_handler("routine.day", runtime.day_job)
    registry.scheduler_handler("routine.override", runtime.override_job)
    registry.scheduler_handler("routine.instance", runtime.instance_job)
    registry.scheduler_handler("routine.activation", runtime.activation_job)
    registry.durable_subscriber(
        "routine.inputs",
        frozenset(
            {
                EventType.WARNING_RAISED,
                EventType.WARNING_CLEARED,
                EventType.MISSION_EVENT_RECORDED,
                EventType.OVERLAY_CHANGED,
                EventType.ENVIRONMENT_ACTIVATED,
                EventType.CONFIG_RELEASE_ACTIVATED,
            }
        ),
        runtime.event,
    )
    registry.subscribe(ClockRegressed, runtime.regressed, name="routine.clock_regressed")
    registry.subscribe(ClockCorrected, runtime.corrected, name="routine.clock_corrected")
    registry.evaluator(EvaluatorRegistration("routine", "1", runtime))
    registry.subscribe(DailyCheckSubmitted, runtime.submitted, name="routine.daily_submitted")
    registry.subscribe(DailyCheckAbandoned, runtime.abandoned, name="routine.daily_abandoned")
    registry.subscribe(ReactionTestRecorded, runtime.reaction, name="routine.reaction")
    registry.provide(RoutineQueries, lambda _: runtime)
    registry.provide(RoutineCommands, lambda _: runtime)
    registry.provide(RoutineTodayPort, lambda _: runtime)
    registry.provide(DailyCheckQuestionSource, lambda _: runtime)
    registry.provide(RoutineCompletionPort, lambda _: runtime)
    registry.provide(AssistantRoutinePort, lambda _: runtime)

    def bind(resolver: Resolver) -> None:
        runtime.authenticator = resolver.optional(RequestAuthenticator)
        runtime.assemble(_deps(resolver))

    registry.bind("routine", bind)
