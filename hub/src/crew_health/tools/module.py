# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E12 registration: settings, the assistant-policy loader check, the tool audit policies and the four provided
ports (ToolGateway, ContextBuilderPort, OutputPolicyPort, EffectApplier) with the static handler table.

Pass 1 only declares. The provided ports are late facades bound in pass 2 once optional owner providers can be looked
up (SYSTEM 5). Every missing owner seam leaves its tool or port unavailable, never a local stand-in:
- E02 IdentityPorts and consent's ObservationPartitions decide scope; without IdentityPorts no call is admitted.
- E17 ModelReleaseReader supplies the pinned catalog and retrieval settings; E17's TokenCounter the tokenizer.
- E10 EvidenceQueryPort and E27 GuidanceEligibility back read_source_passage and search_evidence.
- E05 CapabilityQueries and MeasurementCommands back get_capabilities, propose_measurement and the effect applier.
- E01 TextRulesPort backs the output policy; ContextBuilderPort waits for the owners' authorized projections.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.config.application.ports import TextRulesPort
from crew_health.consent.application.ports import ObservationPartitions
from crew_health.guidance.application.ports import GuidanceEligibility
from crew_health.identity.application.ports import IdentityPorts
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock, ClockCorrections
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork
from crew_health.knowledge.application.ports import EvidenceQueryPort
from crew_health.measurement.application.ports import CapabilityQueries, MeasurementCommands
from crew_health.model.application.ports import ModelReleaseReader
from crew_health.model.application.ports import TokenCounter as ModelTokenCounter
from crew_health.tools.adapters.authority import SessionAuthority
from crew_health.tools.adapters.clock_capture import CorrectedCapture
from crew_health.tools.adapters.effect_validation import CapabilityEffectValidation
from crew_health.tools.adapters.handlers.capabilities import CapabilitiesHandler
from crew_health.tools.adapters.handlers.evidence import ReadPassageHandler, SearchEvidenceHandler
from crew_health.tools.adapters.handlers.proposals import ProposeMeasurementHandler
from crew_health.tools.adapters.jsonschema_validator import CatalogValidator, CompiledCatalog, PolicyValidator
from crew_health.tools.adapters.model_bindings import (
    ModelTokens,
    active_release,
    compile_catalog,
    release_retrieval,
)
from crew_health.tools.adapters.registry import StaticToolRegistry, tool_names
from crew_health.tools.application.effects import Effects
from crew_health.tools.application.gateway import Gateway
from crew_health.tools.application.policy_service import OutputPolicy
from crew_health.tools.application.ports import (
    AppliedEffects,
    ArgumentValidator,
    ContextBuilderPort,
    EffectApplier,
    OutputPolicyPort,
    PolicyVerdict,
    TextField,
    ToolGateway,
    TurnData,
)
from crew_health.tools.application.retrieval_service import RetrievalService
from crew_health.tools.domain.context import ContextBudget, ContextSnapshot
from crew_health.tools.domain.denials import DenialReason
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.domain.tool_result import PendingEffect, ToolCall, ToolResult
from crew_health.tools.settings import setting_specs

_STATUSES: tuple[str, ...] = ("denied", "ok", "no_result", "unavailable")
_REASONS: tuple[DenialReason, ...] = (
    "unknown_tool",
    "out_of_stage",
    "malformed_schema",
    "scope",
    "tool_not_wired",
    "context_over_budget",
    "tool_timeout",
    "stale",
    "policy",
    "handler_rejected",
)
# Machine limitation codes the model reads in a ToolResult; they are not crew-facing copy.
_LIMITATIONS = {
    "dense_unavailable": "dense_unavailable",
    "no_supported_passage": "no_supported_passage",
    "context_over_budget": "context_over_budget",
    "history_incomplete": "history_incomplete",
}


def audit_policies() -> tuple[AuditPolicy, ...]:
    """The session is the only stored reference; metadata values are closed sets (SYSTEM 5.8)."""
    metadata: dict[str, frozenset[str | int | bool]] = {
        "status": frozenset(_STATUSES),
        "tool": frozenset(tool_names()),
        "reason": frozenset(_REASONS),
    }
    return tuple(
        AuditPolicy(event, metadata, frozenset({"session"}), {"session": frozenset({"identity.session"})})
        for event in ("tools.denied", "tools.executed")
    )


@dataclass(slots=True)
class _Live:
    gateway: Gateway | None = None
    policy: OutputPolicy | None = None
    effects: Effects | None = None
    identity: IdentityPorts | None = None
    records: ObservationPartitions | None = None
    capabilities: CapabilityQueries | None = None
    corrections: ClockCorrections | None = None
    catalog: CompiledCatalog | None = None
    retrieval: RetrievalService | None = None


class _Gateway:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def execute(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> ToolResult:
        gateway = self._live.gateway
        if gateway is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        return gateway.execute(uow, scope, call)


class _Context:
    """No owner publishes an authorized context projection or prompt serializer yet (E12 4.4, R-E12-7)."""

    def build(
        self, uow: UnitOfWork, scope: ToolScope, phase: Phase, budget: ContextBudget
    ) -> ContextSnapshot:
        raise ToolsInvalid("CAPABILITY_UNAVAILABLE")


class _Policy:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def check(self, fields: Sequence[TextField], scope: ToolScope, turn: TurnData) -> PolicyVerdict:
        policy = self._live.policy
        if policy is None:
            return PolicyVerdict(False, "CAPABILITY_UNAVAILABLE")
        return policy.check(fields, scope, turn)


class _Effects:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def apply(
        self, uow: UnitOfWork, scope: ToolScope, effects: tuple[PendingEffect, ...], idempotency_prefix: str
    ) -> AppliedEffects:
        applier = self._live.effects
        if applier is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        return applier.apply(uow, scope, effects, idempotency_prefix)


def _model(live: _Live, resolver: Resolver, settings: HubSettings) -> None:
    """The active model release's catalog and retrieval binding, compiled once at startup (AGENTS 3 rule 3)."""
    reader = resolver.optional(ModelReleaseReader)
    active = None if reader is None else active_release(reader)
    if reader is None or active is None:
        return
    try:
        live.catalog = compile_catalog(reader, CatalogValidator(settings), active.release_id)
    except ToolsInvalid as error:
        # A model release whose catalog does not compile leaves every tool unavailable; the hub still serves
        # every safety function (AGENTS rule 3). Storage and other errors are never swallowed.
        if error.code.value != "CONFIG_INVALID":
            raise
        return
    tokens = resolver.optional(ModelTokenCounter)
    evidence, eligibility = resolver.optional(EvidenceQueryPort), resolver.optional(GuidanceEligibility)
    if tokens is None or evidence is None or eligibility is None:
        return
    try:
        tuning = release_retrieval(reader, active.release_id)
    except ToolsInvalid as error:
        if error.code.value != "CONFIG_INVALID":
            raise
        return
    live.retrieval = RetrievalService(
        evidence,
        eligibility,
        ModelTokens(tokens, active.release_id),
        settings,
        tuning,
        boundary=None,
        dense_unavailable=_LIMITATIONS["dense_unavailable"],
        no_supported_passage=_LIMITATIONS["no_supported_passage"],
        context_over_budget=_LIMITATIONS["context_over_budget"],
        history_incomplete=_LIMITATIONS["history_incomplete"],
    )


def _catalog(live: _Live, model_release_id: str) -> ArgumentValidator | None:
    catalog = live.catalog
    return catalog if catalog is not None and catalog.model_release_id == model_release_id else None


def _retrieval(live: _Live, scope: ToolScope) -> RetrievalService | None:
    catalog = live.catalog
    if catalog is None or catalog.model_release_id != scope.model_release_id:
        return None
    return live.retrieval


def _bind(live: _Live, policy: PolicyValidator) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        settings = resolver.require(HubSettings)
        policy.bind(settings)
        live.identity, live.records = (
            resolver.optional(IdentityPorts),
            resolver.optional(ObservationPartitions),
        )
        live.capabilities = resolver.optional(CapabilityQueries)
        live.corrections = resolver.optional(ClockCorrections)
        authority = SessionAuthority(
            lambda: live.identity,
            lambda: live.records,
            max_members=settings.integer("tools.context.max_items"),
            max_strings=settings.integer("tools.context.max_items")
            * settings.integer("tools.max_array_items"),
        )
        handlers = resolver.require(StaticToolRegistry)
        handlers.register("get_capabilities", CapabilitiesHandler(live.capabilities, settings))
        handlers.register(
            "read_source_passage", ReadPassageHandler(lambda scope: _retrieval(live, scope), settings)
        )
        handlers.register("search_evidence", SearchEvidenceHandler(lambda scope: _retrieval(live, scope)))
        handlers.register("propose_measurement", ProposeMeasurementHandler(lambda: live.capabilities))
        handlers.freeze()
        _model(live, resolver, settings)
        clock = resolver.require(Clock)
        live.gateway = Gateway(
            handlers,
            lambda model_release_id: _catalog(live, model_release_id),
            settings,
            authority,
            clock,
            resolver.require(IdFactory),
            policy=policy.pinned,
            corrections=live.corrections,
            capture=CorrectedCapture(
                clock, lambda: live.corrections, page=settings.integer("tools.max_array_items")
            ),
        )
        live.policy = OutputPolicy(resolver.optional(TextRulesPort), settings)
        live.effects = Effects(
            resolver.optional(MeasurementCommands),
            authority,
            settings=settings,
            validator=CapabilityEffectValidation(lambda: live.capabilities, authority),
        )

    return bind


def register(registry: ModuleRegistry) -> None:
    live = _Live()
    for spec in setting_specs():
        registry.setting(spec)
    for audit in audit_policies():
        registry.audit_policy(audit)
    policy = PolicyValidator()
    registry.loader_check(policy)
    registry.provide(
        StaticToolRegistry,
        lambda resolver: StaticToolRegistry(resolver.require(HubSettings)),
        requires=(HubSettings,),
    )
    registry.provide(ToolGateway, lambda resolver: _Gateway(live))
    registry.provide(ContextBuilderPort, lambda resolver: _Context())
    registry.provide(OutputPolicyPort, lambda resolver: _Policy(live))
    registry.provide(EffectApplier, lambda resolver: _Effects(live))
    bind = _bind(live, policy)
    registry.bind("tools", bind)
