"""Registration of the consent module (SYSTEM 4.2 row `consent`); no side effects at import.

Kernel ports are Protocol classes, looked up through `type[object]` and narrowed by structural guards.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeGuard

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.consent.adapters.repository import SqliteConsentStore
from crew_health.consent.application.ports import AuthoredRecords, ConsentPorts
from crew_health.consent.application.records import ConsentRecordAuthorization
from crew_health.consent.application.service import (
    EXPIRY_HANDLER,
    LOCAL_AUDIENCE,
    ConsentDeps,
    ConsentService,
)
from crew_health.consent.application.settings import limits, specs
from crew_health.identity.application.ports import IdentityPorts
from crew_health.kernel.clock import Clock, ClockCorrected, ClockRegressed
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)

KERNEL_PORTS: tuple[type[object], ...] = (
    UnitOfWorkFactory,
    ReadSnapshotFactory,
    Clock,
    IdFactory,
    HubSettings,
    IdentityPorts,
)


class _Lookup(Protocol):
    def require(self, port: type[object]) -> object: ...


class LocalOnly:
    """The audience of consent reference declarations: no session is ever admitted to them."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return False


class Bound:
    """The resolved service for hooks declared before resolution; an unbound hook fails loudly."""

    def __init__(self) -> None:
        self.value: ConsentService | None = None

    def service(self) -> ConsentService:
        if self.value is None:
            raise ValueError("consent: hook ran before the module was bound")
        return self.value


def _has(value: object, *names: str) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


def _authored(value: object) -> TypeGuard[AuthoredRecords]:
    return _has(value, "observation")


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


def build_deps(resolver: _Lookup) -> ConsentDeps:
    uows, snapshots = resolver.require(UnitOfWorkFactory), resolver.require(ReadSnapshotFactory)
    clock, ids, hub = resolver.require(Clock), resolver.require(IdFactory), resolver.require(HubSettings)
    identity = resolver.require(IdentityPorts)
    if not (
        _uows(uows)
        and _snapshots(snapshots)
        and _clock(clock)
        and _ids(ids)
        and _settings(hub)
        and isinstance(identity, IdentityPorts)
    ):
        raise ValueError("consent: a required port is missing or violates its protocol")
    return ConsentDeps(
        uows, snapshots, clock, ids, SqliteConsentStore(), identity.roster, identity.directory, limits(hub)
    )


def _hooks(
    bound: Bound,
) -> tuple[
    Callable[[UnitOfWork, ClockCorrected], None],
    Callable[[UnitOfWork, ClockRegressed], None],
    Callable[[UnitOfWork, ScheduledJob], None],
    Callable[[], None],
]:
    def corrected(uow: UnitOfWork, event: ClockCorrected) -> None:
        bound.service().fence_on_correction(uow, event)

    def regressed(uow: UnitOfWork, event: ClockRegressed) -> None:
        bound.service().fence_on_regression(uow, event)

    def expire(uow: UnitOfWork, job: ScheduledJob) -> None:
        bound.service().expire(uow, job)

    def sweep() -> None:
        service = bound.service()
        service.fence_earlier_processes(correlation_id=service.deps.ids.new("startup"))

    return corrected, regressed, expire, sweep


def register(registry: ModuleRegistry) -> None:
    bound = Bound()
    corrected, regressed, expire, sweep = _hooks(bound)

    def bind(resolver: Resolver) -> None:
        bound.value = resolver.require(ConsentService)

    def authorization(resolver: Resolver) -> ConsentRecordAuthorization:
        loose: _Lookup = resolver
        records = loose.require(AuthoredRecords)
        if not _authored(records):
            raise ValueError("consent: a required port is missing or violates its protocol")
        return ConsentRecordAuthorization(
            resolver.require(ConsentPorts).queries, resolver.require(IdentityPorts).capture, records
        )

    for spec in specs():
        registry.setting(spec)
    registry.id_prefix(record_type="consent.grant", prefix="cns")
    registry.id_prefix(record_type="consent.fence", prefix="fence")
    registry.audience_rule(LOCAL_AUDIENCE, LocalOnly())
    registry.provide(
        ConsentService, lambda resolver: ConsentService(build_deps(resolver)), requires=KERNEL_PORTS
    )
    registry.provide(
        ConsentPorts,
        lambda resolver: ConsentPorts(resolver.require(ConsentService)),
        requires=(ConsentService,),
    )
    registry.provide(
        ConsentRecordAuthorization, authorization, requires=(ConsentPorts, IdentityPorts, AuthoredRecords)
    )
    registry.bind("consent.service", bind)
    registry.subscribe(ClockCorrected, corrected, name="consent.fence_on_correction")
    registry.subscribe(ClockRegressed, regressed, name="consent.fence_on_regression")
    registry.scheduler_handler(EXPIRY_HANDLER, expire)
    registry.on_startup(StartupPhase.STATE_SWEEP, sweep)
