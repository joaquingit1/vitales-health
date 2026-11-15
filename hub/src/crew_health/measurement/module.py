# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E05 registration: router, provided ports, audience, local projection, ID prefixes and settings (SYSTEM 5).

Pass 1 only declares. Provided ports are late facades: the service is built in a named binding once optional
consumed providers can be looked up (SYSTEM 5 pass 2). Until then every facade fails closed and loud.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, get_args, runtime_checkable

from fastapi import APIRouter

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.measurement_request_schema import MeasurementRequest
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceRule,
    LocalProjectionRegistration,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.measurement.adapters.repository import SqliteMeasurementStore
from crew_health.measurement.api.router import MeasurementApi, build_router
from crew_health.measurement.application.commands import (
    PROJECTION,
    Limits,
    MeasurementService,
    Platform,
    Providers,
)
from crew_health.measurement.application.ports import (
    AdapterAssignmentPort,
    AdapterReadinessPort,
    CapabilityQueries,
    CapabilityView,
    CommandFingerprint,
    ConsentQueries,
    EpisodeFacts,
    GapPlan,
    GapPlanPort,
    GapProposalPort,
    KitCapabilitiesPort,
    KitGate,
    MeasurementAssignmentPort,
    MeasurementCommands,
    MissionStatePort,
    ObservationCommands,
    ProposalInput,
    ProposalResult,
    RequestGatePort,
    SessionAuthenticator,
    View,
)
from crew_health.measurement.application.queries import (
    AUDIENCE_RULE,
    CapabilityService,
    MeasurementAudienceRule,
    MeasurementQueries,
)
from crew_health.measurement.domain.model import Action, Disposition, GapReason, RequestState
from crew_health.plans.application import ports as plans_ports

_BASIS = "E05 design 11 bounded work table"
_SETTINGS: tuple[tuple[str, int, int], ...] = (
    ("measurement.page_size", 1, 200),
    ("measurement.max_capabilities", 1, 1024),
    ("measurement.max_attempts", 1, 64),
    ("measurement.max_late_attempts", 1, 64),
    ("measurement.max_observations", 1, 32),
    ("measurement.max_text_bytes", 1, 8192),
    ("measurement.max_notes", 1, 64),
    ("measurement.max_context_entries", 1, 64),
    ("measurement.replay_window_ms", 1, 86400000),
)
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("measurement_request", "mrq"),
    ("measurement_attempt", "att"),
    ("capability_gap", "gap"),
    ("measurement_audit_event", "mev"),
)


class Resolver(Protocol):
    """Structural subset of E01 app.registry.Resolver (a feature module never imports the app package)."""

    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


@runtime_checkable
class _ProviderDeclarations(Protocol):
    @property
    def providers(self) -> Mapping[type[object], object]: ...


class Registry(Protocol):
    """Structural subset of E01 app.registry.ModuleRegistry used by this module."""

    def router(self, router: APIRouter) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def local_projection(self, registration: LocalProjectionRegistration) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def audit_policy(self, policy: AuditPolicy) -> None: ...


@dataclass
class _Live:
    service: MeasurementService | None = None
    capabilities: CapabilityService | None = None
    rule: MeasurementAudienceRule | None = None

    def require_service(self) -> MeasurementService:
        if self.service is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "measurement_not_bound", retryable=True)
        return self.service

    def require_capabilities(self) -> CapabilityService:
        if self.capabilities is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "measurement_not_bound", retryable=True)
        return self.capabilities


class _GapPlansAdapter:
    """Late optional translation under measurement's own registry key."""

    def __init__(self) -> None:
        self.source: plans_ports.GapPlanPort | None = None

    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None:
        if self.source is None:
            return None
        plan = self.source.plan_for(
            condition_ids=condition_ids, environment_id=environment_id, subject_person_id=subject_person_id
        )
        return None if plan is None else GapPlan(plan.plan_id, plan.check_another_way_step_ids)


class _Capabilities:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def get(self, uow: View, code: str) -> CapabilityView | None:
        return self._live.require_capabilities().get(uow, code)

    def list(self, uow: View) -> tuple[CapabilityView, ...]:
        return self._live.require_capabilities().list(uow)


class _Commands:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def propose(
        self, uow: UnitOfWork, *, requester: Actor, subject_person_id: str, proposal: ProposalInput
    ) -> ProposalResult:
        return self._live.require_service().propose(
            uow, requester=requester, subject_person_id=subject_person_id, proposal=proposal
        )


class _Gaps:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def record(self, uow: UnitOfWork, episode_id: str, capability_or_fact: str, consequence: str) -> str:
        return self._live.require_service().record(uow, episode_id, capability_or_fact, consequence)


class _Assignments:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def allows(self, view: View, *, request_id: str, operator_id: str, patient_id: str) -> bool:
        service = self._live.service
        return service is not None and service.allows(
            view, request_id=request_id, operator_id=operator_id, patient_id=patient_id
        )


class _Audience:
    """Unbound rule admits nobody: a missing audience answer is no access."""

    def __init__(self, live: _Live) -> None:
        self._live = live

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        rule = self._live.rule
        return rule is not None and rule.admits(snapshot, session, audience)


def _bind(live: _Live, api: MeasurementApi, gap_plans: _GapPlansAdapter) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        gap_plans.source = resolver.optional(plans_ports.GapPlanPort)
        candidate = resolver.optional(GapPlanPort)
        gap_provider = None if candidate is gap_plans and gap_plans.source is None else candidate
        settings = resolver.require(HubSettings)
        consent = resolver.optional(ConsentQueries)
        store = SqliteMeasurementStore(
            max_attempts=settings.integer("measurement.max_attempts"),
            max_late_attempts=settings.integer("measurement.max_late_attempts"),
        )
        capabilities = CapabilityService(
            kit_capabilities=resolver.optional(KitCapabilitiesPort),
            mission=resolver.optional(MissionStatePort),
            readiness=resolver.optional(AdapterReadinessPort),
            max_capabilities=settings.integer("measurement.max_capabilities"),
        )
        live.capabilities = capabilities
        live.rule = MeasurementAudienceRule(store=store, consent=consent)
        live.service = MeasurementService(
            platform=Platform(
                uows=resolver.require(UnitOfWorkFactory),
                ids=resolver.require(IdFactory),
                ledger=resolver.require(IdempotencyStore),
                receipts=resolver.require(CommandReceipts),
            ),
            store=store,
            capabilities=capabilities,
            providers=Providers(
                kit_gate=resolver.optional(KitGate),
                mission=resolver.optional(MissionStatePort),
                request_gate=resolver.optional(RequestGatePort),
                consent=consent,
                episodes=resolver.optional(EpisodeFacts),
                gap_plans=gap_provider,
                observations=resolver.optional(ObservationCommands),
                assignments=resolver.optional(AdapterAssignmentPort),
                units=resolver.require(UnitRegistry),
            ),
            limits=Limits(
                max_attempts=settings.integer("measurement.max_attempts"),
                max_late_attempts=settings.integer("measurement.max_late_attempts"),
                max_observations=settings.integer("measurement.max_observations"),
                max_text_bytes=settings.integer("measurement.max_text_bytes"),
                max_notes=settings.integer("measurement.max_notes"),
                max_context_entries=settings.integer("measurement.max_context_entries"),
                replay_window_ms=settings.integer("measurement.replay_window_ms"),
            ),
        )
        api.service = live.service
        api.queries = MeasurementQueries(
            snapshots=resolver.require(ReadSnapshotFactory),
            store=store,
            capabilities=capabilities,
            consent=consent,
            page_size=settings.integer("measurement.page_size"),
        )
        api.auth = resolver.optional(SessionAuthenticator)
        api.fingerprints = resolver.optional(CommandFingerprint)
        api.ids = resolver.require(IdFactory)

    return bind


def audit_policies() -> tuple[AuditPolicy, ...]:
    """SYSTEM 6: every audit event type `MeasurementService._changed` writes for a request transition, keyed by
    the replicated request it names. An attempt event also names its attempt; gaps are declared with their refs."""
    states = frozenset[str | int | bool](get_args(RequestState.__value__))
    reasons = frozenset[str | int | bool](get_args(GapReason.__value__))
    causes = (
        "created",
        *get_args(Action.__value__),
        *("gate_" + reason for reason in get_args(GapReason.__value__)),
    )
    request = {"request_id": frozenset({"measurement_request"})}
    attempt = {**request, "attempt_id": frozenset({"measurement_attempt"})}
    policies = [
        AuditPolicy("measurement.request_" + c, {"state": states}, frozenset(request), request)
        for c in causes
    ]
    policies += [
        AuditPolicy("measurement.request_attempt_" + d, {"state": states}, frozenset(attempt), attempt)
        for d in get_args(Disposition.__value__)
    ]
    policies.append(
        AuditPolicy(
            "measurement.gap_not_opened", {"reason": frozenset({"no_episode"})}, frozenset(request), request
        )
    )
    gap = {**request, "gap_id": frozenset({"capability_gap"}), "episode_id": frozenset({"episode"})}
    policies.append(AuditPolicy("measurement.gap_opened", {"reason": reasons}, frozenset(gap), gap))
    return tuple(policies)


def register(registry: Registry) -> None:
    live = _Live()
    api = MeasurementApi()
    gap_plans = _GapPlansAdapter()
    audience = _Audience(live)
    for key, low, high in _SETTINGS:
        registry.setting(SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, _BASIS))
    for record_type, prefix in _PREFIXES:
        registry.id_prefix(record_type=record_type, prefix=prefix)
    for policy in audit_policies():
        registry.audit_policy(policy)
    registry.audience_rule(AUDIENCE_RULE, audience)
    registry.local_projection(
        LocalProjectionRegistration(
            key=PROJECTION,
            payload_schema_json=canonical_json(MeasurementRequest.model_json_schema()),
            required_field_pointers=("/request_id", "/revision", "/state", "/capability_code"),
            rule=audience,
        )
    )
    if not isinstance(registry, _ProviderDeclarations) or GapPlanPort not in registry.providers:
        registry.provide(GapPlanPort, lambda _: gap_plans)
    registry.provide(CapabilityQueries, lambda _: _Capabilities(live))
    registry.provide(MeasurementCommands, lambda _: _Commands(live))
    registry.provide(GapProposalPort, lambda _: _Gaps(live))
    registry.provide(MeasurementAssignmentPort, lambda _: _Assignments(live))
    registry.bind("measurement", _bind(live, api, gap_plans))
    registry.router(build_router(api))
