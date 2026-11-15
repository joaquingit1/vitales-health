# mypy: disable-error-code="type-abstract"
# E01's registry keys providers by Protocol class (registry.provide and Resolver.require take type[P]); mypy rejects a
# Protocol class there, so only this check is off here and only in this file.
"""Plans declarations and late wiring; importing the module performs no work."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, Protocol, get_args

from fastapi import APIRouter, Request

from crew_health.catchup.application.ports import PlanCatchUpInputs, PlanCatchUpPort
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.guidance.application.ports import GuidanceEligibility, SkillFacts
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import (
    CommandIntents,
    CommandResumer,
    IdempotencyStore,
    PendingCommand,
    StoredResult,
)
from crew_health.kernel.config import ReleaseCheck
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import (
    Audience,
    AudienceRule,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.plans.adapters.repository import SqlitePlanRuns
from crew_health.plans.api.router import RequestAuthenticator, build_router
from crew_health.plans.api.tools import register_plan_tools
from crew_health.plans.application import ground_log
from crew_health.plans.application.act import ActOnPlanRun, EscalationResumer, PlanEscalationIntents
from crew_health.plans.application.assistant_copies import AnswerScope, ApprovedPassages, ToolRegistry
from crew_health.plans.application.audience import RunViewersRule
from crew_health.plans.application.consumers import PlanConsumers
from crew_health.plans.application.kitmap_copies import (
    KitMapContext,
    PlanChange,
    PlanColumns,
    PlanColumnsPort,
    PlanningDelta,
)
from crew_health.plans.application.measurement_copies import GapPlan, GapPlanPort
from crew_health.plans.application.ports import (
    EpisodeReader,
    ObservationRefReader,
    PlanEscalation,
    PlanEscalationReader,
    PlanHelpEntry,
    PlanRunContextReader,
    RunContext,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
)
from crew_health.plans.application.provided import PlansProvided
from crew_health.plans.application.release import PlansCatalog
from crew_health.plans.application.release_check import PlansReleaseCheck
from crew_health.plans.application.runs import PlanRuns
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.application.teach_copies import (
    GuidedCheckCompleted,
    GuidedCheckQueries,
    GuidedCompletionSink,
    GuidedStepRef,
    PlanRunReader,
)
from crew_health.plans.application.tools import PlanTools
from crew_health.plans.application.world import PlansWorld
from crew_health.plans.domain.run import EventKind
from crew_health.roles.application import ports as roles_ports


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def durable_subscriber(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def command_resumer(self, name: str, resumer: CommandResumer) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def audit_policy(self, policy: AuditPolicy) -> None: ...


def audit_policies() -> tuple[AuditPolicy, ...]:
    """SYSTEM 5.8: the audit entry PlansWorld.persist writes for each run change, one per event kind it emits. It
    names only the run, whose reference the same unit of work replicates first; it carries no metadata."""
    run = {"plan_run_id": frozenset({"plan_run"})}
    return tuple(AuditPolicy(kind, {}, frozenset(run), run) for kind in get_args(EventKind))


def build_world(resolver: Resolver) -> PlansWorld:
    settings = PlansSettings.read(resolver.require(HubSettings))
    return PlansWorld(
        uow=resolver.require(UnitOfWorkFactory),
        snapshots=resolver.require(ReadSnapshotFactory),
        clock=resolver.require(Clock),
        ids=resolver.require(IdFactory),
        settings=settings,
        idempotency=resolver.require(IdempotencyStore),
        intents=resolver.require(CommandIntents),
        repository=SqlitePlanRuns(settings),
        catalog=PlansCatalog(settings),
        guidance=resolver.optional(GuidanceEligibility),
        skills=resolver.optional(SkillFacts),
        roster=resolver.optional(roles_ports.RosterPort),
        mission=resolver.optional(roles_ports.MissionStatePort),
        help_sessions=resolver.optional(HelpSessionReader),
        stock=resolver.optional(StockReader),
        cover=resolver.optional(SupplyCoverPort),
        supply_ledger=resolver.optional(SupplyLedgerPort),
        help_entry=resolver.optional(PlanHelpEntry),
        guided_checks=resolver.optional(GuidedCheckQueries),
        episodes=resolver.optional(EpisodeReader),
        observations=resolver.optional(ObservationRefReader),
        day_labels=resolver.optional(roles_ports.DayLabelPort),
        ground_line=ground_log.resolve(resolver),
    )


class PlansApi:
    """Holder filled at bind; layer 3 will attach a router to these services."""

    def __init__(self) -> None:
        self.runs: PlanRuns | None = None
        self.actions: ActOnPlanRun | None = None


class PlansRuntime:
    def __init__(self) -> None:
        self.world: PlansWorld | None = None
        self.authenticator: RequestAuthenticator | None = None
        self.api = PlansApi()
        self.tools = PlanTools(self.need)

    def need(self) -> PlansWorld:
        if self.world is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "plans module is not bound", retryable=True)
        return self.world

    def authenticate(self, request: Request, operation: str) -> SessionContext:
        if self.authenticator is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "plans authenticator unavailable", retryable=True)
        return self.authenticator.session(request, operation)

    def mission_mode(self) -> Literal["LIVE", "REPLAY", "SIM"] | None:
        mission = self.need().mission
        facts = mission.current() if mission is not None else None
        return facts.mission_mode if facts is not None else None

    def provided(self) -> PlansProvided:
        return PlansProvided(self.need())

    def state(self, plan_run_id: str) -> roles_ports.PlanRunSnapshot | None:
        return self.provided().state(plan_run_id)

    def guided_step(self, plan_run_id: str, skill_id: str) -> GuidedStepRef | None:
        return self.provided().guided_step(plan_run_id, skill_id)

    def context(self, session: SessionContext, plan_run_id: str) -> RunContext | None:
        return self.provided().context(session, plan_run_id)

    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None:
        return self.provided().plan_for(
            condition_ids=condition_ids, environment_id=environment_id, subject_person_id=subject_person_id
        )

    def row_columns(
        self, ctx: KitMapContext, *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]:
        return self.provided().row_columns(ctx, kit=kit, plan_ids=plan_ids)

    def plans_changed(
        self, ctx: KitMapContext, *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]:
        return self.provided().plans_changed(ctx, base_kit=base_kit, delta=delta)

    def catch_up_inputs(self, plan_run_id: str) -> PlanCatchUpInputs | None:
        return self.provided().catch_up_inputs(plan_run_id)

    def read(self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str) -> PlanEscalation:
        return PlanEscalationIntents(self.need()).read(snapshot, session, escalation_id)

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return RunViewersRule(self.need().repository).admits(snapshot, session, audience)

    def resume(self, session: SessionContext, intent: PendingCommand) -> StoredResult | None:
        return EscalationResumer(self.need()).resume(session, intent)

    def on_guided_check_completed(self, uow: UnitOfWork, event: GuidedCheckCompleted) -> None:
        PlanConsumers(self.need()).on_guided_check_completed(uow, event)

    def help_event(self, uow: UnitOfWork, event: StoredEvent) -> None:
        PlanConsumers(self.need()).on_help_session_changed(uow, event)

    def handoff_event(self, uow: UnitOfWork, event: StoredEvent) -> None:
        PlanConsumers(self.need()).on_handoff_changed(uow, event)


def register(registry: ModuleRegistry) -> None:
    runtime = PlansRuntime()

    def provided(resolver: Resolver) -> PlansRuntime:
        return runtime

    def tools(resolver: Resolver) -> PlanTools:
        return runtime.tools

    def bind(resolver: Resolver) -> None:
        world = build_world(resolver)
        runtime.world = world
        runtime.authenticator = resolver.optional(RequestAuthenticator)
        runtime.api.runs = PlanRuns(world)
        runtime.api.actions = ActOnPlanRun(world)
        runtime.tools.answers = resolver.optional(AnswerScope)
        runtime.tools.passages = resolver.optional(ApprovedPassages)
        tool_registry = resolver.optional(ToolRegistry)
        if tool_registry is not None:
            register_plan_tools(tool_registry, runtime.tools)

    for spec in PlansSettings.specs():
        registry.setting(spec)
    for policy in audit_policies():
        registry.audit_policy(policy)
    registry.id_prefix(record_type="plan_run", prefix="prun")
    registry.id_prefix(record_type="escalation_intent", prefix="pes")
    registry.loader_check(PlansReleaseCheck(lambda: runtime.need().settings, runtime.mission_mode))
    registry.audience_rule("plans.run_viewers", runtime)
    registry.durable_subscriber("plans.help", frozenset({EventType.HELP_SESSION_CHANGED}), runtime.help_event)
    registry.durable_subscriber(
        "plans.handoff", frozenset({EventType.HANDOFF_CHANGED}), runtime.handoff_event
    )
    registry.command_resumer("plans.escalation", runtime)
    registry.provide(roles_ports.PlanRunReader, provided)
    registry.provide(PlanRunReader, provided)
    # teach/module.py forwards its GuidedCheckCompleted here inside teach's finish transaction (SYSTEM 8.1).
    registry.provide(GuidedCompletionSink, lambda _: runtime)
    registry.provide(GapPlanPort, provided)
    registry.provide(PlanColumnsPort, provided)
    registry.provide(PlanRunContextReader, provided)
    registry.provide(PlanCatchUpPort, provided)
    registry.provide(PlanEscalationReader, provided)
    registry.provide(PlanTools, tools)
    registry.router(build_router(runtime))
    registry.bind("plans", bind)
