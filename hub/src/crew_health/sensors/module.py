# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E23 sensors registration (SYSTEM 5.12): router, provided ports, evaluator, scheduler handlers, loader checks and
guard, startup migration and sweep, settings and ID prefixes.

Pass 1 only declares. The runtime is built in the named binding once optional providers can be looked up; until
then every operation fails closed with a retryable error. The ActivationParticipant mission calls on activation is
provided here, so mission never imports sensors beyond its ports.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import UnitMappingJson
from crew_health.kernel.clock import ClockCorrections, ClockSegments
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import (
    ActivationGuard,
    CandidateRelease,
    ConfigReleaseReader,
    Finding,
    ReleaseCheck,
    ReleaseHandle,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationIsolation, EvaluatorRegistration
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import UnitRegistry, WireUnitConverter
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWork, UnitOfWorkFactory
from crew_health.mission.application.ports import ActivationParticipant, MissionStatePort
from crew_health.sensors.adapters.audit_policy import POLICIES, register_policies
from crew_health.sensors.adapters.config_reader import (
    Check4SensorLimits,
    Check12Sensors,
    ScanBoundGuard,
    SensorConfig,
    load_sensor_config,
)
from crew_health.sensors.adapters.migration import apply_migration, replicated_tables_of
from crew_health.sensors.adapters.sqlite_repository import SqliteSensorStore, table_declarations
from crew_health.sensors.adapters.timer import SweepTimer
from crew_health.sensors.api.router import SensorsApi, build_router
from crew_health.sensors.application.activation import SensorActivation
from crew_health.sensors.application.commands import Platform
from crew_health.sensors.application.deadlines import freshness_job, status_job
from crew_health.sensors.application.evaluate import SensorEvaluator
from crew_health.sensors.application.ports import FreshnessSweeper, ProvenanceStreamsPort, SensorSeriesReader, SensorStateQuery
from crew_health.sensors.application.providers import SensorProviders
from crew_health.sensors.application.registry import install_file
from crew_health.sensors.application.runtime import (
    EVALUATOR,
    EVALUATOR_VERSION,
    FRESHNESS_HANDLER,
    STATUS_HANDLER,
    Faults,
    Limits,
    Runtime,
)
from crew_health.sensors.application.sweep import FreshnessSweep
from crew_health.sensors.domain.loader_checks import SensorBounds
from crew_health.sensors.settings import specifications

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("sensor_reading", "rdg"),
    ("sensor_evaluation", "evl"),
    ("sensor_evaluation_input", "evi"),
    ("sensor_partition", "prt"),
    ("sensor_completion", "cmp"),
    ("sensor_schedule", "sch"),
)
_MIGRATION = "adapters/migrations/0001_sensors.sql"
_ENGINE = Actor("engine", "sensors")


@dataclass
class _Live:
    api: SensorsApi
    resolver: Resolver | None = None
    runtime: Runtime | None = None
    sweep: FreshnessSweep | None = None
    timer: SweepTimer | None = None

    def need(self) -> Runtime:
        if self.runtime is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        return self.runtime


class _LateEvaluator:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def evaluate(self, uow: UnitOfWork, value: object) -> None:
        SensorEvaluator(self._live.need()).evaluate(uow, value)  # type: ignore[arg-type]


class _Late:
    """A loader check or guard that needs pass 2 facts. Unbound means a finding, never a pass."""

    def __init__(self, check_id: str) -> None:
        self._check_id = check_id
        self.build: Callable[[CandidateRelease], tuple[Finding, ...]] | None = None
        self.build_guard: Callable[[UnitOfWork, CandidateRelease], tuple[Finding, ...]] | None = None

    @property
    def check_id(self) -> str:
        return self._check_id

    def check(self, *args: object) -> tuple[Finding, ...]:
        candidate = args[-1]
        if not isinstance(candidate, CandidateRelease):
            return (Finding(self._check_id, None, "", "no candidate"),)
        if len(args) == 2 and self.build_guard is not None and isinstance(args[0], object):
            return self.build_guard(args[0], candidate)  # type: ignore[arg-type]
        if len(args) == 1 and self.build is not None:
            return self.build(candidate)
        return (Finding(self._check_id, None, "", "sensor validator unavailable"),)


def _config_of(release: ReleaseHandle) -> SensorConfig:
    return load_sensor_config(lambda name: release.document(name).raw)


def _messages_of(release: ReleaseHandle) -> Mapping[str, str]:
    try:
        found = json.loads(release.document(ConfigName.UI_COPY).raw)["error_messages"]
    except (ValueError, KeyError, TypeError):
        return {}
    return {str(k): str(v) for k, v in found.items() if isinstance(v, str)}


def _mapping_resolver(path: str) -> Callable[[str, str], str | None] | None:
    """D12 unit-mapping.json: wire spelling to UCUM per indicator. Absent or invalid: no resolver, a finding."""
    if not path:
        return None
    try:
        document = UnitMappingJson.model_validate_json(_read(path))
    except (OSError, ValueError):
        return None
    table = {(m.indicator_code, m.wire_unit): m.ucum_unit for m in document.mappings}
    return lambda sensor_type_id, wire: table.get((sensor_type_id, wire))


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _bind(live: _Live, check4: _Late, check12: _Late, guard: _Late, evaluator_ready: list[bool]) -> Callable[[Resolver], None]:
    api = live.api

    def bind(resolver: Resolver) -> None:
        live.resolver = resolver
        settings = resolver.require(HubSettings)
        limits = Limits.of(settings)
        store = SqliteSensorStore(
            row_limit=min(limits.max_channels, settings.integer("storage.max_rows")),
            page_rows=settings.integer("storage.max_rows"),
        )
        health = resolver.optional(HealthState)
        rt = Runtime(
            store=store,
            ids=resolver.require(IdFactory),
            limits=limits,
            config_of=_config_of,
            messages_of=_messages_of,
            hub_source_clock_id=settings.text("sensors.hub_source_clock_id"),
            mission=resolver.optional(MissionStatePort),
            corrections=resolver.optional(ClockCorrections),
            segments=resolver.optional(ClockSegments),
            converter=resolver.optional(WireUnitConverter),
            isolation=resolver.optional(EvaluationIsolation),
            faults=None if health is None else Faults(health),
            release_reader=resolver.optional(ConfigReleaseReader),
            read_file=_read,
            deployment_profile=settings.text("sensors.deployment_profile"),
            demo_registry_path=settings.text("sensors.demo_registry_path"),
        )
        live.runtime = rt
        api.runtime = rt
        api.snapshots = resolver.require(ReadSnapshotFactory)
        api.platform = Platform(
            uows=resolver.require(UnitOfWorkFactory),
            ledger=resolver.require(IdempotencyStore),
            receipts=resolver.require(CommandReceipts),
        )
        live.sweep = FreshnessSweep(rt)
        _bind_checks(resolver, settings, store, limits, check4, check12, guard)
        register_policies(resolver.require(UnitOfWorkFactory), POLICIES)
        tables = replicated_tables_of(resolver.require(UnitOfWorkFactory))
        for table, record_type, key in table_declarations():
            tables.register_replicated_table(table, record_type=record_type, key_column=key)
        evaluator_ready.append(True)

    return bind


def _bind_checks(resolver: Resolver, settings: HubSettings, store: SqliteSensorStore, limits: Limits, check4: _Late, check12: _Late, guard: _Late) -> None:
    units = resolver.optional(UnitRegistry)
    bounds = SensorBounds(
        settings.integer("sensors.aggregate_bucket_s"),
        settings.integer("sensors.window_max_buckets"),
        limits.window_scan_max_readings,
    )
    check4.build = Check4SensorLimits().check

    def sensor_check(candidate: CandidateRelease) -> tuple[Finding, ...]:
        resolve = _mapping_resolver(settings.text("sensors.unit_mapping_path"))
        if units is None or resolve is None:
            return (Finding(check12.check_id, ConfigName.SENSOR_CATALOG, "", "unit registry or unit mapping unavailable"),)
        return Check12Sensors(bounds, units, resolve).check(candidate)

    check12.build = sensor_check

    def intervals(uow: UnitOfWork) -> Mapping[str, int]:
        found: dict[str, int] = {}
        for c in store.channels(uow):
            if c.facts.expected_interval_seconds is not None:
                kind = c.facts.sensor_type_id
                found[kind] = min(found.get(kind, c.facts.expected_interval_seconds), c.facts.expected_interval_seconds)
        return found

    guard.build_guard = ScanBoundGuard(limits.window_scan_max_readings, intervals).check


def _storage_hook(live: _Live) -> Callable[[], None]:
    def storage() -> None:
        if live.resolver is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        apply_migration(live.resolver.require(UnitOfWorkFactory), _MIGRATION, name="sensors.0001")

    return storage


def _sweep_hook(live: _Live) -> Callable[[], None]:
    """Before serving: load the channel registry, run one sweep, then start the independent timer if enabled."""

    def start() -> None:
        resolver, rt, sweep = live.resolver, live.need(), live.sweep
        if resolver is None or sweep is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        settings = resolver.require(HubSettings)
        uows = resolver.require(UnitOfWorkFactory)
        path = settings.text("sensors.channel_registry_path")
        with uows.begin(actor=_ENGINE, correlation_id="sensors_startup") as uow:
            if path:
                install_file(rt, uow, path, source="registry")
            sweep.tick(uow)
        if settings.boolean("sensors.sweep_timer_enabled"):
            def tick() -> None:
                with uows.begin(actor=_ENGINE, correlation_id="sensors_sweep") as unit:
                    sweep.tick(unit)

            def failed() -> None:
                if rt.faults is not None:
                    from crew_health.sensors.application.runtime import SCHEDULER_LAG
                    from datetime import UTC, datetime

                    rt.faults.mark(SCHEDULER_LAG, datetime.now(UTC))

            live.timer = SweepTimer(tick, failed, period_s=rt.limits.freshness_sweep_s)
            live.timer.start()

    return start


def register(registry: ModuleRegistry) -> None:
    api = SensorsApi()
    live = _Live(api)
    for spec in specifications():
        registry.setting(spec)
    for record_type, prefix in _PREFIXES:
        registry.id_prefix(record_type=record_type, prefix=prefix)
    registry.provide(SensorStateQuery, lambda _: _Providers(live))
    registry.provide(SensorSeriesReader, lambda _: _Providers(live))
    registry.provide(ProvenanceStreamsPort, lambda _: _Providers(live))
    registry.provide(ActivationParticipant, lambda _: _Participant(live))
    registry.provide(FreshnessSweeper, lambda _: _Sweeper(live))
    check4, check12, guard = _Late("4"), _Late("12.sensor"), _Late("12.scan_bound")
    registry.loader_check(check4)  # type: ignore[arg-type]
    registry.loader_check(check12)  # type: ignore[arg-type]
    registry.activation_guard(guard)  # type: ignore[arg-type]
    registry.evaluator(EvaluatorRegistration(EVALUATOR, EVALUATOR_VERSION, _LateEvaluator(live)))  # type: ignore[arg-type]
    registry.scheduler_handler(FRESHNESS_HANDLER, lambda uow, job: freshness_job(live.need(), uow, job))
    registry.scheduler_handler(STATUS_HANDLER, lambda uow, job: status_job(live.need(), uow, job))
    registry.bind("sensors", _bind(live, check4, check12, guard, []))
    registry.on_startup(StartupPhase.STORAGE, _storage_hook(live))
    registry.on_startup(StartupPhase.STATE_SWEEP, _sweep_hook(live))
    registry.router(build_router(api))


class _Providers:
    """Late bound provider facade: fails closed until pass 2 has built the runtime."""

    def __init__(self, live: _Live) -> None:
        self._live = live

    def _real(self) -> SensorProviders:
        resolver = self._live.resolver
        if resolver is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        return SensorProviders(self._live.need(), resolver.require(ReadSnapshotFactory))

    def freshness(self, uow, channel_id):  # type: ignore[no-untyped-def]
        return self._real().freshness(uow, channel_id)

    def channels(self, uow):  # type: ignore[no-untyped-def]
        return self._real().channels(uow)

    def type_name(self, uow, sensor_type_id):  # type: ignore[no-untyped-def]
        return self._real().type_name(uow, sensor_type_id)

    def accepted(self, channel_id, *, until, limit):  # type: ignore[no-untyped-def]
        return self._real().accepted(channel_id, until=until, limit=limit)

    def habitat_streams(self, snapshot):  # type: ignore[no-untyped-def]
        return self._real().habitat_streams(snapshot)


class _Sweeper:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def sweep_once(self) -> int:
        resolver, sweep = self._live.resolver, self._live.sweep
        if resolver is None or sweep is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "sensors_not_bound", retryable=True)
        with resolver.require(UnitOfWorkFactory).begin(actor=_ENGINE, correlation_id="sensors_sweep") as uow:
            return sweep.tick(uow)


class _Participant:
    def __init__(self, live: _Live) -> None:
        self._live = live

    def on_activated(self, uow, profile):  # type: ignore[no-untyped-def]
        SensorActivation(self._live.need()).on_activated(uow, profile)
