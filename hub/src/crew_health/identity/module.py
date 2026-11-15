"""Registration of the identity module (SYSTEM 4.2 row `identity`); no side effects at import.

Kernel ports are Protocol classes. They are looked up through `type[object]` and narrowed by structural
guards, so no `type-abstract` silencing is needed while D25 is not applied to hub/pyproject.toml.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeGuard

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.identity.adapters.credentials import Argon2Verifier, OpaqueTokens
from crew_health.identity.adapters.repository import SqliteIdentityStore
from crew_health.identity.adapters.vault import UnavailableVault
from crew_health.identity.api.router import IdentityRuntime, IdentitySecurity, build_router
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import HubProfile, IdentityPorts, SessionLiveness, View
from crew_health.identity.application.service import IdentityService
from crew_health.identity.application.settings import IdentitySettings, specs
from crew_health.identity.application.support import (
    AUDIT_DECLARATIONS,
    LOCAL_AUDIENCE,
    GroundSignIn,
    IdentityDeps,
    KdfSlots,
    MissionDays,
)
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.private import PrivateKeyVault
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWorkFactory,
)

KERNEL_PORTS: tuple[type[object], ...] = (
    UnitOfWorkFactory,
    ReadSnapshotFactory,
    Clock,
    IdFactory,
    HubSettings,
    IdempotencyStore,
    CommandReceipts,
)


class _Lookup(Protocol):
    def require(self, port: type[object]) -> object: ...


class _Provide(Protocol):
    def provide(
        self,
        port: type[object],
        factory: Callable[[Resolver], object],
        *,
        requires: tuple[type[object], ...] = (),
    ) -> None: ...


class LocalOnly:
    """The audience of identity reference declarations: no session is ever admitted to them."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return False


class Liveness:
    """SessionLiveness for consumers holding a SessionContext: the session row is live in their view, on the
    listener that issued it, with its principal, roster membership and roles unchanged."""

    def __init__(self, service: IdentityService) -> None:
        self.service = service

    def is_live(self, view: View, session: SessionContext) -> bool:
        return self.service.alive(view, session)


def audit_policies() -> tuple[AuditPolicy, ...]:
    """E02's audit declarations as platform policies, registered through the module (R-E02-23)."""
    return tuple(
        AuditPolicy(
            value.event_type,
            value.metadata,
            frozenset({value.reference_field}),
            {value.reference_field: frozenset({value.reference_type})},
        )
        for value in AUDIT_DECLARATIONS
    )


def _has(value: object, *names: str) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


def _uows(value: object) -> TypeGuard[UnitOfWorkFactory]:
    return _has(value, "begin")


def _snapshots(value: object) -> TypeGuard[ReadSnapshotFactory]:
    return _has(value, "snapshot")


def _clock(value: object) -> TypeGuard[Clock]:
    return _has(value, "now", "monotonic_ns", "process_epoch")


def _ids(value: object) -> TypeGuard[IdFactory]:
    return _has(value, "new", "derived")


def _settings(value: object) -> TypeGuard[HubSettings]:
    return _has(value, "integer", "quantity", "boolean", "text")


def _ledger(value: object) -> TypeGuard[IdempotencyStore]:
    return _has(value, "lookup", "record")


def _receipts(value: object) -> TypeGuard[CommandReceipts]:
    return _has(value, "accepted")


def _mission_clock(value: object) -> TypeGuard[MissionClock]:
    return _has(value, "mission_day")


def build_deps(resolver: _Lookup, ground: GroundSignIn, days: MissionDays | None = None) -> IdentityDeps:
    uows, snapshots = resolver.require(UnitOfWorkFactory), resolver.require(ReadSnapshotFactory)
    clock, ids, hub = resolver.require(Clock), resolver.require(IdFactory), resolver.require(HubSettings)
    ledger, receipts = resolver.require(IdempotencyStore), resolver.require(CommandReceipts)
    if not (
        _uows(uows)
        and _snapshots(snapshots)
        and _clock(clock)
        and _ids(ids)
        and _settings(hub)
        and _ledger(ledger)
        and _receipts(receipts)
    ):
        raise ValueError("identity: a kernel port is missing or violates its protocol")
    settings = IdentitySettings.from_hub(hub)
    return IdentityDeps(
        uows=uows,
        snapshots=snapshots,
        clock=clock,
        ids=ids,
        settings=settings,
        store=SqliteIdentityStore(),
        hasher=Argon2Verifier(settings.argon2),
        tokens=OpaqueTokens(),
        ledger=ledger,
        receipts=receipts,
        slots=KdfSlots(settings.concurrency),
        ground=ground,
        days=MissionDays() if days is None else days,
    )


def consumer_ports(service: IdentityService) -> IdentityPorts:
    return IdentityPorts(service, service, service, service, service, service)


def register(registry: ModuleRegistry) -> None:
    runtime, ground, days = IdentityRuntime(), GroundSignIn(), MissionDays()
    security = IdentitySecurity(runtime)
    loose: _Provide = registry

    def bind(resolver: Resolver) -> None:
        profile = resolver.optional(HubProfile)
        ground.allowed = profile is not None and profile.ground_sign_in
        clock = resolver.optional(MissionClock)  # type: ignore[type-abstract]
        days.clock = clock if _mission_clock(clock) else None
        runtime.service_value = resolver.require(IdentityService)
        runtime.admin_value = resolver.require(IdentityAdmin)

    def sweep() -> None:
        service = runtime.service()
        service.end_stale_epoch(correlation_id=service.deps.ids.new("startup"))

    for spec in specs():
        registry.setting(spec)
    registry.id_prefix(record_type="identity.session", prefix="ses")
    registry.id_prefix(record_type="identity.owner_proof", prefix="owner")
    registry.audience_rule(LOCAL_AUDIENCE, LocalOnly())
    for policy in audit_policies():
        registry.audit_policy(policy)
    registry.provide(
        IdentityService,
        lambda resolver: IdentityService(build_deps(resolver, ground, days)),
        requires=KERNEL_PORTS,
    )
    registry.provide(
        IdentityAdmin,
        lambda resolver: IdentityAdmin(
            resolver.require(IdentityService).deps, resolver.require(IdentityService)
        ),
        requires=(IdentityService,),
    )
    registry.provide(
        IdentityPorts,
        lambda resolver: consumer_ports(resolver.require(IdentityService)),
        requires=(IdentityService,),
    )
    registry.provide(IdentitySecurity, lambda resolver: security)
    loose.provide(
        SessionLiveness,
        lambda resolver: Liveness(resolver.require(IdentityService)),
        requires=(IdentityService,),
    )
    loose.provide(PrivateKeyVault, lambda resolver: UnavailableVault())
    registry.bind("identity.runtime", bind)
    registry.router(build_router(runtime))
    registry.on_startup(StartupPhase.STATE_SWEEP, sweep)
