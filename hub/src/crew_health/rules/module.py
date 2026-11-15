# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E24 warnings registration (SYSTEM 5.12): router, provided ports, subscriptions, scheduler handlers, loader check,
audit policies, replicated table, startup migration and sweep, settings and ID prefixes.

Pass 1 only declares. The runtime is built in the named binding once optional providers can be looked up; until then
every operation fails closed with a retryable error. Safety functions do not depend on any optional provider: a
missing mission or renderer is CAPABILITY_UNAVAILABLE for evaluation (the producer's record still commits, 5.5), never
a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.contracts_gen.schemas.warning_schema import Warning as WireWarning
from crew_health.help.application import ports as help_ports
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import ClockCorrected, ClockRegressed
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.config import ConfigBinding
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.replication import ReplicatedTable
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Actor,
    Audience,
    LocalProjectionRegistration,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.mission.application.ports import (
    MissionEventRecorded,
    MissionStatePort,
    OverlayPort,
    StateFactChanged,
)
from crew_health.records.application.ports import AnswerAcceptedFact
from crew_health.rules.adapters.config_reader import WarningRulesCheck
from crew_health.rules.adapters.migration import apply_migration
from crew_health.rules.api.router import RulesApi, build_router
from crew_health.rules.application import core, handlers
from crew_health.rules.application import insight as insight_mod
from crew_health.rules.application.commands import Platform, WarningActions, attach_routine_overrides
from crew_health.rules.application.core import Runtime
from crew_health.rules.application.ports import (
    AssistantContext,
    AssistantWarningGuard,
    InsightWarningPort,
    RecordFactsPort,
    WarningCommands,
    WarningFact,
    WarningQueries,
)
from crew_health.rules.application.presentation import (
    Presenter,
    ReplicaView,
    SessionPresentation,
    WarningPresentation,
)
from crew_health.rules.application.queries import WarningReader
from crew_health.rules.application.stores import table_declarations
from crew_health.rules.application.wire import AUDIENCE_RULE, PROJECTION
from crew_health.rules.domain.inputs import InsightDraft
from crew_health.rules.settings import specifications
from crew_health.sensors.application.ports import ChannelFreshnessChanged, SensorEvaluated

_MIGRATION = "adapters/migrations/0001_rules.sql"
_ENGINE_ID = "rules"
_PREFIXES: tuple[tuple[str, str], ...] = (("warning", "wrn"), ("rules_pending", "pnd"))
_REFUSALS = {"trigger_spent", "clear_later", "origin_mismatch", "stale_release"}
_INSIGHT_CODES = {
        "level_not_allowed", "topic_unknown", "evidence_empty", "evidence_kind", "evidence_not_found",
        "evidence_other_person", "evidence_replay", "evidence_not_accepted", "evidence_origin_mixed",
        "habitat_fact_text", "action_count",
}  # fmt: skip
_ASSISTANT_CODES = {"LEVEL_NOT_ALLOWED", "ACTION_NOT_ALLOWED"}


def audit_policies() -> tuple[AuditPolicy, ...]:
    warning_ref = ({"warning"}, {"warning": frozenset({"warning"})})
    lifecycle = tuple(
        AuditPolicy("warning." + kind, {}, frozenset(warning_ref[0]), warning_ref[1])
        for kind in ("raised", "cleared", "updated", "occurrence_attached", "replica_applied")
    )
    coded = (
        AuditPolicy(
            "rules.pending_quarantined", {}, frozenset({"pending"}), {"pending": frozenset({"rules_pending"})}
        ),
        AuditPolicy("warning.refused", {"code": frozenset(_REFUSALS)}, frozenset(), {}),
        AuditPolicy("insight.rejected", {"code": frozenset(_INSIGHT_CODES)}, frozenset(), {}),
        AuditPolicy("warning.assistant_rejected", {"code": frozenset(_ASSISTANT_CODES)}, frozenset(), {}),
    )
    return (*lifecycle, *coded)


@dataclass(frozen=True, slots=True)
class _Audience:
    """`rules.warning` events: habitat and crew warnings reach every session, person warnings only that person (3.10)."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        if audience.rule != AUDIENCE_RULE:
            return False
        scope, _, person = audience.object_ref.partition(":")
        if scope in ("habitat", "crew"):
            return True
        return scope == "person" and session.kind == "crew" and person != "" and session.person_id == person


@dataclass
class _Live:
    api: RulesApi
    presenter: Presenter | None = None
    resolver: Resolver | None = None
    runtime: Runtime | None = None

    def need(self) -> Runtime:
        if self.runtime is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        return self.runtime


def _bind(live: _Live) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        live.resolver = resolver
        settings = resolver.require(HubSettings)
        rt = Runtime(
            settings=settings,
            ids=resolver.require(IdFactory),
            mission=resolver.optional(MissionStatePort),
            binding=resolver.optional(ConfigBinding),
            health=resolver.optional(HealthState),
            record_facts=resolver.optional(RecordFactsPort),
        )
        live.runtime = rt
        snapshots = resolver.require(ReadSnapshotFactory)
        platform = Platform(
            resolver.require(UnitOfWorkFactory),
            resolver.require(IdempotencyStore),
            resolver.require(CommandReceipts),
        )
        live.presenter = Presenter(rt, snapshots, resolver.optional(OverlayPort))
        live.api.reader = WarningReader(rt, snapshots)
        live.api.actions = WarningActions(rt, platform)
        live.api.page_limit = settings.integer("rules.page_limit")

    return bind


def _storage_hook(live: _Live) -> Callable[[], None]:
    def storage() -> None:
        if live.resolver is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        factory = live.resolver.require(UnitOfWorkFactory)
        apply_migration(factory, _MIGRATION, name="rules.0001")
        for table, record_type, key in table_declarations():
            factory.register_replicated_table(table, record_type=record_type, key_column=key)  # type: ignore[attr-defined]

    return storage


def _sweep_hook(live: _Live) -> Callable[[], None]:
    """STATE_SWEEP (5.3): dwell markers are void at start (continuity is proved, never assumed); recovery is
    scheduled when pending rows exist and health is degraded until they empty."""

    def sweep() -> None:
        rt, resolver = live.need(), live.resolver
        if resolver is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        with resolver.require(UnitOfWorkFactory).begin(
            actor=Actor("engine", _ENGINE_ID), correlation_id="rules_startup"
        ) as uow:
            rt.dwell.delete_not_in(uow.connection, (), limit=rt.cap("rules.dwell_scan_max"))
            if rt.pending.any(uow.connection):
                core.record_recovery_due(rt, uow)

    return sweep


class _Queries:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def _reader(self) -> WarningReader:
        reader = self._live.api.reader
        if reader is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        return reader

    def visible(self, session: SessionContext, warning_id: str) -> bool:
        return self._reader().visible(session, warning_id)

    def count_active_visible(self, session: SessionContext) -> int:
        return self._reader().count_active_visible(session)

    def active_shared_at_or_above(self, level: str) -> Sequence[WarningFact]:
        return self._reader().active_shared_at_or_above(level)


class _Commands:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def attach_routine_overrides(self, uow: UnitOfWork, warning_id: str, override_ids: Sequence[str]) -> None:
        attach_routine_overrides(self._live.need(), uow, warning_id, tuple(override_ids))


class _Guard:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def propose_level(self, uow: UnitOfWork, ctx: AssistantContext, level: str, topic_id: str | None):  # type: ignore[no-untyped-def]
        return insight_mod.AssistantGuard(self._live.need()).propose_level(uow, ctx, level, topic_id)

    def propose_action(self, uow: UnitOfWork, ctx: AssistantContext, warning_id: str, action: str):  # type: ignore[no-untyped-def]
        return insight_mod.AssistantGuard(self._live.need()).propose_action(uow, ctx, warning_id, action)


class _Presentation:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def _presenter(self) -> Presenter:
        if self._live.presenter is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "rules_not_bound", retryable=True)
        return self._live.presenter

    def for_session(self, session: SessionContext) -> SessionPresentation:
        return self._presenter().for_session(session)

    def replicas(self, session: SessionContext) -> tuple[ReplicaView, ...]:
        return self._presenter().replicas(session)


class _Insights:
    """What E13 calls in its own transaction (4.2): the evidence facts come from the records owner."""

    def __init__(self, live: _Live) -> None:
        self._live = live

    def validate_and_raise(self, uow: UnitOfWork, draft: InsightDraft) -> WarningFact | None:
        return insight_mod.InsightPort(self._live.need()).validate_and_raise(uow, draft)

    def on_insight_dismissed(self, uow: UnitOfWork, insight_id: str, actor: Actor) -> None:
        insight_mod.InsightPort(self._live.need()).on_insight_dismissed(uow, insight_id, actor)

    def on_insight_expired(self, uow: UnitOfWork, insight_id: str) -> None:
        insight_mod.InsightPort(self._live.need()).on_insight_expired(uow, insight_id)


class _HelpQueries:
    """E26's structural `WarningQueries` (`visible` only)."""

    def __init__(self, live: _Live) -> None:
        self._queries = _Queries(live)

    def visible(self, session: SessionContext, warning_id: str) -> bool:
        return self._queries.visible(session, warning_id)


def _late(
    live: _Live, handler: Callable[[Runtime, UnitOfWork, Any], None]
) -> Callable[[UnitOfWork, Any], None]:
    return lambda uow, event: handler(live.need(), uow, event)


def durable(registry: ModuleRegistry, live: _Live) -> None:
    """Producers that publish only an outbox event (no in-transaction bus payload on main): the link owner's
    `ground_link.changed`, and the configuration and mission owners' activations (3.8, C12). Each handler runs in the
    event worker's unit of work; an evaluator fault there becomes a pending row, a storage fault is retried."""

    def link(uow: UnitOfWork, event: StoredEvent) -> None:
        handlers.on_ground_link_event(live.need(), uow, event)

    def activated(uow: UnitOfWork, event: StoredEvent) -> None:
        handlers.on_activation(live.need(), uow, event)

    registry.durable_subscriber("rules.ground_link", frozenset({EventType.GROUND_LINK_CHANGED}), link)
    registry.durable_subscriber(
        "rules.activation",
        frozenset({EventType.CONFIG_RELEASE_ACTIVATED, EventType.ENVIRONMENT_ACTIVATED}),
        activated,
    )


def register(registry: ModuleRegistry) -> None:
    api = RulesApi()
    live = _Live(api)
    for spec in specifications():
        registry.setting(spec)
    for record_type, prefix in _PREFIXES:
        registry.id_prefix(record_type=record_type, prefix=prefix)
    for policy in audit_policies():
        registry.audit_policy(policy)
    registry.replicated_table(ReplicatedTable("rules_warning", "warning", "warning_id"))
    audience = _Audience()
    registry.audience_rule(AUDIENCE_RULE, audience)
    registry.local_projection(
        LocalProjectionRegistration(
            key=PROJECTION,
            payload_schema_json=canonical_json(WireWarning.model_json_schema()),
            required_field_pointers=("/warning_id", "/revision", "/state", "/level"),
            rule=audience,
        )
    )
    registry.provide(WarningQueries, lambda _: _Queries(live))
    registry.provide(help_ports.WarningQueries, lambda _: _HelpQueries(live))
    registry.provide(WarningCommands, lambda _: _Commands(live))
    registry.provide(AssistantWarningGuard, lambda _: _Guard(live))
    registry.provide(InsightWarningPort, lambda _: _Insights(live))
    registry.provide(WarningPresentation, lambda _: _Presentation(live))
    registry.loader_check(WarningRulesCheck(max_definitions=500))
    registry.subscribe(
        AnswerAcceptedFact, lambda uow, e: handlers.on_answer(live.need(), uow, e), name="rules.answer"
    )
    registry.subscribe(
        ClockCorrected,
        lambda uow, e: handlers.on_clock_corrected(live.need(), uow, e),
        name="rules.clock_corrected",
    )
    registry.subscribe(
        ClockRegressed,
        lambda uow, e: handlers.on_clock_regressed(live.need(), uow, e),
        name="rules.clock_regressed",
    )
    for event_class, handler, name in (
        (SensorEvaluated, handlers.on_sensor_evaluated, "rules.sensor_evaluated"),
        (ChannelFreshnessChanged, handlers.on_freshness, "rules.freshness"),
        (MissionEventRecorded, handlers.on_mission_event, "rules.mission_event"),
        (StateFactChanged, handlers.on_state_fact, "rules.state_fact"),
    ):
        registry.subscribe(event_class, _late(live, handler), name=name)
    durable(registry, live)
    registry.scheduler_handler(
        core.DEADLINE_HANDLER, lambda uow, job: handlers.fire_deadline(live.need(), uow, job)
    )
    registry.scheduler_handler(
        core.RECOVER_HANDLER, lambda uow, job: handlers.recover_pending(live.need(), uow, job)
    )
    registry.bind("rules", _bind(live))
    registry.on_startup(StartupPhase.STORAGE, _storage_hook(live))
    registry.on_startup(StartupPhase.STATE_SWEEP, _sweep_hook(live))
    registry.router(build_router(api))
