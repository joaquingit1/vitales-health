# mypy: disable-error-code="type-abstract"
# E01's registry keys providers by Protocol class (registry.provide and Resolver.require take type[P]); mypy rejects a
# Protocol class there, so only this check is off here and only in this file.
"""Registration of the supplies module (SYSTEM 4.2 row `supplies`, 5.12); no side effects at import."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Request

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.supply_use_schema import SupplyUse
from crew_health.guidance.application.ports import Fact, StockFact, StockFacts
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.commands import IdempotencyStore, canonical_json
from crew_health.kernel.config import ActivationGuard, ConfigReleaseReader, ReleaseCheck
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.units import Quantity
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
from crew_health.roles.application.ports import SupplyAboardPort
from crew_health.supplies.adapters.sqlite_ledger import SqliteLedger
from crew_health.supplies.adapters.verifier import verify_ledger
from crew_health.supplies.api.router import RequestAuthenticator, build_router
from crew_health.supplies.application import provided
from crew_health.supplies.application.context import BoundedCalls, Corrupt, Deps
from crew_health.supplies.application.events import AUDIENCE_RULE, CREW_EVENT_ROLES, PROJECTION
from crew_health.supplies.application.ports import (
    CoverChange,
    CoverFact,
    CoverPlanningPort,
    KitMapContext,
    MissionStatePort,
    PlanRunReader,
    StockLevel,
    StockReader,
    SupplyAddition,
    SupplyCoverFacts,
    SupplyCoverPort,
    SupplyLedgerPort,
    SupplyNeed,
    SupplyPartition,
)
from crew_health.supplies.application.release_index import (
    ReleaseIndexCache,
    SuppliesActivationGuard,
    SuppliesReleaseCheck,
)
from crew_health.supplies.application.settings import SuppliesLimits, specs
from crew_health.supplies.application.step_uses import record_step_uses
from crew_health.supplies.application.use_cases import UseRequest, UseResult, cover_view, record_use

_LOGGER = "crew_health.supplies"


# The slice of E01's ModuleRegistry and Resolver (crew_health.app.registry) this module uses; a structural copy
# because a feature module may not import crew_health.app (import contract app-only-assembles).
class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def local_projection(self, registration: LocalProjectionRegistration) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def activation_guard(self, guard: ActivationGuard) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


class CrewAudience:
    """`supply.used` is a crew event with no person data: crew, medical officer and station sessions see it."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == AUDIENCE_RULE and bool(session.roles & CREW_EVENT_ROLES)


def build_deps(resolver: Resolver) -> Deps:
    limits = SuppliesLimits.read(resolver.require(HubSettings))
    return Deps(
        uow=resolver.require(UnitOfWorkFactory),
        snapshots=resolver.require(ReadSnapshotFactory),
        ids=resolver.require(IdFactory),
        idempotency=resolver.require(IdempotencyStore),
        repository=SqliteLedger(),
        indexes=ReleaseIndexCache(limits.release_cache),
        limits=limits,
        mission=resolver.optional(MissionStatePort),
        help_sessions=resolver.optional(HelpSessionReader),
        plan_runs=resolver.optional(PlanRunReader),
        releases=resolver.optional(ConfigReleaseReader),
        references=BoundedCalls(inflight=limits.reference_inflight, timeout_ms=limits.reference_timeout_ms),
        corrupt=Corrupt(),
    )


class SuppliesRuntime:
    """The routes and every provided port call through this facade; until `bind` resolved the ports each call
    fails loudly with a retryable INTERNAL_ERROR, and nothing answers from a partial wiring."""

    def __init__(self) -> None:
        self.deps: Deps | None = None
        self.authenticator: RequestAuthenticator | None = None

    def need(self) -> Deps:
        if self.deps is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "supplies module is not bound yet", retryable=True)
        return self.deps

    # SuppliesApi
    def cover(self, environment_id: str | None) -> bytes:
        return cover_view(self.need(), environment_id)

    def record(self, session: SessionContext, request: UseRequest) -> UseResult:
        return record_use(self.need(), session, request)

    def release_id(self) -> str:
        with self.need().snapshots.snapshot() as view:
            return view.release.release_id

    def authenticate(self, request: Request, operation_id: str) -> SessionContext:
        if self.authenticator is None:  # E02 absent: nobody is signed in (fail closed)
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return self.authenticator.session(request, operation_id)


class SuppliesProvided:
    """Every port this module provides, over the runtime (registered under each consumer's class)."""

    def __init__(self, runtime: SuppliesRuntime) -> None:
        self._runtime = runtime

    def stock(self, environment_id: str) -> Mapping[str, StockLevel]:
        return provided.stock_levels(self._runtime.need(), environment_id)

    def uses_after_one(
        self, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
    ) -> Mapping[tuple[str, str], int | None]:
        return provided.uses_after_one(self._runtime.need(), environment_id, needs, stock)

    def record_step_uses(
        self,
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]:
        return record_step_uses(
            self._runtime.need(),
            uow,
            environment_id=environment_id,
            plan_run_id=plan_run_id,
            step_id=step_id,
            occurrence=occurrence,
            pinned_release_id=pinned_release_id,
            uses=uses,
            actor=actor,
            at=at,
        )

    def rows(
        self,
        view: UnitOfWork | ReadSnapshot,
        *,
        partition: SupplyPartition,
        after_supply_id: str | None,
        limit: int,
    ) -> tuple[CoverFact, ...]:
        return provided.cover_facts(self._runtime.need(), view, partition, after_supply_id, limit)

    def cover_changed(
        self, ctx: KitMapContext, *, add_supplies: Sequence[SupplyAddition]
    ) -> Sequence[CoverChange]:
        return provided.cover_changed(self._runtime.need(), ctx, add_supplies)


class SuppliesStockFacts:
    """guidance's StockFacts, a separate object so its `stock` signature does not clash with StockReader's."""

    def __init__(self, runtime: SuppliesRuntime) -> None:
        self._runtime = runtime

    def stock(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, supplies: Mapping[str, Quantity | None]
    ) -> Mapping[str, Fact[StockFact]]:
        return provided.stock_facts(self._runtime.need(), snapshot, patient_location_id, supplies)


class SuppliesAboard:
    def __init__(self, runtime: SuppliesRuntime) -> None:
        self._runtime = runtime

    def aboard(self, supply_ids: Collection[str], environment_id: str) -> Mapping[str, bool]:
        return provided.aboard(self._runtime.need(), supply_ids, environment_id)


def verify_at_start(deps: Deps) -> None:
    """E32 5.1: a broken chain is logged loudly and its partition shows unknown cover and refuses uses."""
    with deps.snapshots.snapshot() as view:
        broken = verify_ledger(
            view,
            deps.repository,
            max_partitions=deps.limits.max_supplies,
            batch_rows=deps.limits.verify_batch_rows,
            max_batches=deps.limits.verify_max_batches,
        )
    deps.corrupt.mark(broken)
    for where, supply_id in sorted(broken, key=lambda k: (k[0].mission_id, k[0].environment_id, k[1])):
        logging.getLogger(_LOGGER).error(
            "supplies: ledger chain broken for %s in %s", supply_id, where.environment_id
        )


def register(registry: ModuleRegistry) -> None:
    runtime = SuppliesRuntime()
    ports = SuppliesProvided(runtime)

    def bind(resolver: Resolver) -> None:
        deps = build_deps(resolver)
        runtime.authenticator = resolver.optional(RequestAuthenticator)
        verify_at_start(deps)
        runtime.deps = deps

    def guard_repository() -> SqliteLedger | None:
        return None if runtime.deps is None else SqliteLedger()

    def guard_mission() -> MissionStatePort | None:
        return None if runtime.deps is None else runtime.deps.mission

    def guard_limit() -> int:
        return runtime.need().limits.max_supplies

    registry.id_prefix(record_type="supply_use", prefix="sup")
    registry.id_prefix(record_type="supply_use_refusal", prefix="supr")
    for spec in specs():
        registry.setting(spec)
    rule = CrewAudience()
    registry.audience_rule(AUDIENCE_RULE, rule)
    registry.local_projection(
        LocalProjectionRegistration(
            key=PROJECTION,
            payload_schema_json=canonical_json(SupplyUse.model_json_schema()),
            required_field_pointers=("/supply_use_id", "/supply_id", "/environment_id", "/quantity", "/at"),
            rule=rule,
        )
    )
    registry.loader_check(SuppliesReleaseCheck())
    registry.activation_guard(SuppliesActivationGuard(guard_repository, guard_mission, guard_limit))
    registry.router(build_router(runtime, runtime.authenticate))
    registry.provide(StockReader, lambda _: ports)
    registry.provide(SupplyCoverPort, lambda _: ports)
    registry.provide(SupplyLedgerPort, lambda _: ports)
    registry.provide(SupplyCoverFacts, lambda _: ports)
    registry.provide(CoverPlanningPort, lambda _: ports)
    registry.provide(StockFacts, lambda _: SuppliesStockFacts(runtime))
    registry.provide(SupplyAboardPort, lambda _: SuppliesAboard(runtime))
    registry.bind("supplies", bind)
