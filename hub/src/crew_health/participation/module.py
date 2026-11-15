"""E22 declarations (SYSTEM 4.2 row `participation`): settings, audit policies, the loader check, composition
adapters under E22's consumed port classes, the provided ports and the router. No side effects at import."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from fastapi import APIRouter

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.identity.application.ports import IdentityPorts
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ReleaseCheck
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWorkFactory,
)
from crew_health.participation.adapters.composition import IdentityOrigins, IdentityRoster
from crew_health.participation.adapters.loader_check import ParticipationRulesCheck
from crew_health.participation.adapters.repository import SqliteParticipationStore
from crew_health.participation.api.router import ParticipationApi, build_router
from crew_health.participation.application.consent import ConsentCommands
from crew_health.participation.application.context import LOCAL_AUDIENCE, Deps
from crew_health.participation.application.intake import Intake
from crew_health.participation.application.ports import (
    CompletionRecorder,
    CrewGoalQueries,
    ParticipationRoster,
    ParticipationSettle,
    PatchQueries,
    RecordOrigins,
    StreakQueries,
)
from crew_health.participation.application.projector import Projector
from crew_health.participation.application.queries import Queries
from crew_health.participation.application.rebuild import Rebuild
from crew_health.participation.application.settings import limits, specs
from crew_health.participation.domain.types import CrewGoalView, PatchAward, StreakView


class Registry(Protocol):
    def audit_policy(self, policy: AuditPolicy) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def router(self, router: APIRouter) -> None: ...


class LocalOnly:
    """The audience of participation's reference declarations: no session is ever admitted to them."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return False


def audit_policies() -> tuple[AuditPolicy, ...]:
    """Ids and enumerated words only (E22 2): no value, count or free text can be carried."""

    def policy(
        event: str, field: str, kind: str, metadata: dict[str, frozenset[str | int | bool]]
    ) -> AuditPolicy:
        return AuditPolicy(event, metadata, frozenset({field}), {field: frozenset({kind})})

    return (
        policy(
            "participation.consent_changed",
            "consent",
            "participation.consent",
            {"enabled": frozenset({True, False})},
        ),
        policy("participation.award_earned", "award", "participation.award", {}),
        policy("participation.award_removed", "award", "participation.award", {}),
        policy("participation.intent_conflict", "intent", "participation.intent", {}),
        policy("participation.intent_parked", "intent", "participation.intent", {}),
    )


@dataclass(slots=True)
class Parts:
    deps: Deps
    intake: Intake
    projector: Projector
    queries: Queries
    consent: ConsentCommands
    rebuild: Rebuild


class Holder:
    """Builds the use cases once, on first resolution, so every provided port shares one set."""

    def __init__(self) -> None:
        self.value: Parts | None = None

    def parts(self, resolver: Resolver) -> Parts:
        if self.value is None:
            hub = resolver.require(HubSettings)  # type: ignore[type-abstract]
            deps = Deps(
                resolver.require(UnitOfWorkFactory),  # type: ignore[type-abstract]
                resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
                resolver.require(IdFactory),  # type: ignore[type-abstract]
                resolver.require(MissionClock),  # type: ignore[type-abstract]
                resolver.require(ParticipationRoster),  # type: ignore[type-abstract]
                resolver.require(RecordOrigins),  # type: ignore[type-abstract]
                resolver.require(IdempotencyStore),  # type: ignore[type-abstract]
                resolver.require(CommandReceipts),  # type: ignore[type-abstract]
                limits(hub),
                SqliteParticipationStore(),
            )
            projector = Projector(deps)
            queries = Queries(deps, projector)
            self.value = Parts(
                deps,
                Intake(deps),
                projector,
                queries,
                ConsentCommands(deps),
                Rebuild(deps, projector, queries),
            )
        return self.value


class _Streaks:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def for_person(self, person_id: str) -> StreakView:
        return self.parts.queries.streak(person_id)


class _Patches:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def for_person(self, person_id: str) -> tuple[PatchAward, ...]:
        return self.parts.queries.patches(person_id)


class _CrewGoal:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def current(self) -> CrewGoalView:
        return self.parts.queries.crew_goal().view


class _Settle:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def settle(self, intent_key: str) -> Literal["settled", "pending", "parked"]:
        return self.parts.projector.settle(intent_key)


def register(registry: ModuleRegistry) -> None:
    holder, api = Holder(), ParticipationApi()
    for spec in specs():
        registry.setting(spec)
    for policy in audit_policies():
        registry.audit_policy(policy)
    registry.audience_rule(LOCAL_AUDIENCE, LocalOnly())
    registry.loader_check(ParticipationRulesCheck())

    def hub_limit(resolver: Resolver) -> int:
        return limits(resolver.require(HubSettings)).roster_max  # type: ignore[type-abstract]

    registry.provide(
        ParticipationRoster,  # type: ignore[type-abstract]
        lambda r: IdentityRoster(r.require(IdentityPorts).roster, limit=hub_limit(r)),
        requires=(IdentityPorts, HubSettings),
    )
    registry.provide(
        RecordOrigins,  # type: ignore[type-abstract]
        lambda r: IdentityOrigins(r.require(IdentityPorts).roster),
        requires=(IdentityPorts,),
    )
    needs = (
        UnitOfWorkFactory,
        ReadSnapshotFactory,
        IdFactory,
        MissionClock,
        ParticipationRoster,
        RecordOrigins,
        IdempotencyStore,
        CommandReceipts,
        HubSettings,
    )
    registry.provide(CompletionRecorder, lambda r: holder.parts(r).intake, requires=needs)  # type: ignore[type-abstract]
    registry.provide(ParticipationSettle, lambda r: _Settle(holder.parts(r)), requires=needs)  # type: ignore[type-abstract]
    registry.provide(StreakQueries, lambda r: _Streaks(holder.parts(r)), requires=needs)  # type: ignore[type-abstract]
    registry.provide(ConsentCommands, lambda r: holder.parts(r).consent, requires=needs)
    registry.provide(PatchQueries, lambda r: _Patches(holder.parts(r)), requires=needs)  # type: ignore[type-abstract]
    registry.provide(CrewGoalQueries, lambda r: _CrewGoal(holder.parts(r)), requires=needs)  # type: ignore[type-abstract]

    def bind(resolver: Resolver) -> None:
        parts = holder.parts(resolver)
        api.deps, api.queries, api.consent, api.rebuild = (
            parts.deps,
            parts.queries,
            parts.consent,
            parts.rebuild,
        )

    registry.bind("participation", bind)
    registry.router(build_router(api))
