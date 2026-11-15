# mypy: disable-error-code="type-abstract"
"""Composer bindings for E13's consumer-owned seam keys (assistant.application.tools_copies, adapters.conversations).

E13 may not import E12's, E15's or E02's classes (SYSTEM 6), so it declares structural copies of their ports. The
composer, which may import everyone, binds each copy to its owner's real provider here, translating the frozen
dataclasses field by field. A missing owner leaves the copy unbound and the assistant's conversation routes stay
explicitly unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.assistant.adapters.conversations import EpisodeAccess, PersonOrigin, TurnAnchor
from crew_health.assistant.application import tools_copies as copies
from crew_health.assistant.application.ports import CommandFingerprint as AssistantFingerprint
from crew_health.episodes.application.ports import EpisodeFacts, OriginNamespace
from crew_health.identity.application.service import IdentityService
from crew_health.kernel.clock import Clock, ClockCorrections, ClockSegment, TimeInput
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWork
from crew_health.measurement.application import ports as measurement
from crew_health.platform.adapters.fingerprint import KeyedCommandFingerprint
from crew_health.tools.adapters.clock_capture import CorrectedCapture
from crew_health.tools.application import ports as tools
from crew_health.tools.domain import tool_result
from crew_health.tools.domain.phase import Phase as ToolsPhase
from crew_health.tools.domain.scope import ToolScope as ToolsScope

type Origin = Literal["LIVE", "REPLAY", "SIM"]


def _scope(scope: copies.ToolScope) -> ToolsScope:
    return ToolsScope(
        scope.session,
        scope.subject_person_id,
        scope.mission_id,
        scope.origin_mode,
        scope.episode_id,
        scope.help_session_id,
        scope.plan_run_id,
        scope.turn_id,
        scope.conversation_id,
        scope.config_release_id,
        scope.evidence_release_id,
        scope.model_release_id,
        ToolsPhase(scope.phase.value),
        scope.source_revision,
        scope.generation,
        scope.deadline,
    )


class _Gateway:
    def __init__(self, owner: tools.ToolGateway) -> None:
        self.owner = owner

    def execute(self, uow: UnitOfWork, scope: copies.ToolScope, call: copies.ToolCall) -> copies.ToolResult:
        result = self.owner.execute(
            uow, _scope(scope), tool_result.ToolCall(call.tool_call_id, call.name, call.arguments)
        )
        return copies.ToolResult(
            result.tool_call_id,
            result.status,
            result.data,
            result.provenance_ids,
            result.observed_at,
            result.generated_at,
            result.limitations,
            result.error_code,
        )


class _Effects:
    def __init__(self, owner: tools.EffectApplier) -> None:
        self.owner = owner

    def apply(
        self,
        uow: UnitOfWork,
        scope: copies.ToolScope,
        effects: tuple[copies.PendingEffect, ...],
        idempotency_prefix: str,
    ) -> tools.AppliedEffects:
        pending = tuple(
            tool_result.PendingEffect(
                item.turn_id,
                item.tool_call_id,
                item.generation,
                item.source_revision,
                item.arguments,
                item.references,
            )
            for item in effects
        )
        return self.owner.apply(uow, _scope(scope), pending, idempotency_prefix)


class _Policy:
    def __init__(self, owner: tools.OutputPolicyPort) -> None:
        self.owner = owner

    def check(
        self, fields: tuple[copies.TextField, ...], scope: copies.ToolScope, turn: copies.TurnData
    ) -> copies.PolicyVerdict:
        verdict = self.owner.check(
            tuple(tools.TextField(item.field, item.text, item.source_ref) for item in fields),
            _scope(scope),
            tools.TurnData(
                turn.release,
                turn.source_revision,
                tuple(
                    tools.TextField(item.field, item.text, item.source_ref) for item in turn.authored_fields
                ),
            ),
        )
        return copies.PolicyVerdict(verdict.allowed, verdict.error_code)


class _Episodes:
    def __init__(self, owner: EpisodeFacts) -> None:
        self.owner = owner

    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool:
        return self.owner.visible_to(uow, episode_id, actor)


class _Anchor:
    """A turn's deadline anchor: the hub's time on its own segment at the newest correction version, through
    E12's CorrectedCapture (the same capture the gateway measures with). The segment's opening version can be
    superseded before the first turn; anchoring on it would leave the start unknown and every tool timed out."""

    def __init__(
        self,
        clock: Clock,
        corrections: ClockCorrections,
        snapshots: ReadSnapshotFactory,
        segment: Callable[[], ClockSegment | None],
    ) -> None:
        self.clock, self.snapshots, self.segment = clock, snapshots, segment
        self.capture = CorrectedCapture(clock, lambda: corrections, page=100)

    def anchor(self, origin_mode: Origin) -> TimeInput | None:
        segment = self.segment()
        if segment is None:
            return None
        start = TimeInput(
            self.clock.now(),
            segment.source_clock_id,
            segment.segment_id,
            segment.capture_offset_version,
            "hub_corrected",
            origin_mode,
        )
        with self.snapshots.snapshot() as view:
            return self.capture(cast(UnitOfWork, view), start)


class _Origins:
    def __init__(self, identity: IdentityService) -> None:
        self.identity = identity

    def partition(self) -> tuple[str, Origin] | None:
        with self.identity.deps.snapshots.snapshot() as view:
            return self.identity.partition(view)


class _MeasurementEpisodes:
    """E05's structural copy of E15's EpisodeFacts, translated field by field."""

    def __init__(self, owner: EpisodeFacts) -> None:
        self.owner = owner

    def get(self, uow: UnitOfWork, episode_id: str) -> measurement.EpisodeFact | None:
        fact = self.owner.get(uow, episode_id)
        if fact is None:
            return None
        return measurement.EpisodeFact(
            fact.episode_id,
            fact.patient_id,
            fact.mission_id,
            fact.origin_mode,
            fact.state,
            fact.revision,
            fact.questionnaire_version,
            fact.red_flag_ids,
            fact.route_module_id,
            fact.created_at,
        )

    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool:
        return self.owner.visible_to(uow, episode_id, actor)


class _EpisodeOrigin:
    """E15's OriginNamespace from E02's roster partition: a crew member of the active mission records in the
    partition's origin namespace; anyone else, or another mission, has none (E15 refuses ORIGIN_MISMATCH)."""

    def __init__(self, identity: IdentityService) -> None:
        self.identity = identity

    def origin(self, uow: UnitOfWork, patient_id: str, mission_id: str) -> str | None:
        partition = self.identity.partition(uow)
        if partition is None or partition[0] != mission_id or self.identity.member(uow, patient_id) is None:
            return None
        return partition[1]


def compose(registry: ModuleRegistry, segment: Callable[[], ClockSegment | None]) -> None:
    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    entries: tuple[tuple[object, Callable[[Resolver], object], tuple[object, ...]], ...] = (
        (copies.ToolGateway, lambda r: _Gateway(r.require(tools.ToolGateway)), (tools.ToolGateway,)),
        (copies.EffectApplier, lambda r: _Effects(r.require(tools.EffectApplier)), (tools.EffectApplier,)),
        (
            copies.OutputPolicyPort,
            lambda r: _Policy(r.require(tools.OutputPolicyPort)),
            (tools.OutputPolicyPort,),
        ),
        (EpisodeAccess, lambda r: _Episodes(r.require(EpisodeFacts)), (EpisodeFacts,)),
        (
            TurnAnchor,
            lambda r: _Anchor(
                r.require(Clock), r.require(ClockCorrections), r.require(ReadSnapshotFactory), segment
            ),
            (Clock, ClockCorrections, ReadSnapshotFactory),
        ),
        (PersonOrigin, lambda r: _Origins(r.require(IdentityService)), (IdentityService,)),
        (OriginNamespace, lambda r: _EpisodeOrigin(r.require(IdentityService)), (IdentityService,)),
        (measurement.EpisodeFacts, lambda r: _MeasurementEpisodes(r.require(EpisodeFacts)), (EpisodeFacts,)),
        (
            AssistantFingerprint,
            lambda r: r.require(KeyedCommandFingerprint),
            (KeyedCommandFingerprint,),
        ),
    )
    for port, factory, requires in entries:
        key = cast(type[object], port)
        needed = tuple(cast(type[object], item) for item in requires)
        if key not in providers and all(item in providers for item in needed):
            registry.provide(key, factory, requires=needed)
