# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E23 mission registration (SYSTEM 5.12): router, provided ports, loader checks, scheduler handlers, startup
migration, settings and ID prefixes.

Pass 1 only declares. The services are built in the named binding once optional providers can be looked up; until
then every operation fails closed with a retryable error. Consumer modules declared structural copies of E23's
ports (E23 4.2): one real adapter is registered under each copy so their optional lookups find a provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.app.registry import ModuleRegistry, Resolver, StartupPhase
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.guidance.application.ports import EnvironmentFacts as GuidanceEnvironmentFacts
from crew_health.help.application.ports import MissionStatePort as HelpMissionStatePort
from crew_health.kernel.clock import ClockCorrections, ClockSegments
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import CandidateRelease, Finding, ReleaseCheck, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWork, UnitOfWorkFactory
from crew_health.mission.adapters.audit_policy import POLICIES, register_policies
from crew_health.mission.adapters.config_reader import (
    Check7Profiles,
    Check12EffectFilters,
    lists_for,
    load_mission_config,
)
from crew_health.mission.adapters.consumers import (
    GuidanceEnvironment,
    HelpMission,
    RolesDayLabel,
    RolesLocation,
    RolesMission,
    RolesOverlays,
    RoutineMission,
)
from crew_health.mission.adapters.live_state import LiveMissionState
from crew_health.mission.adapters.migration import apply_migration, replicated_tables_of
from crew_health.mission.adapters.sqlite_repository import SqliteMissionStore, table_declarations
from crew_health.mission.api.router import MissionApi, build_router
from crew_health.mission.application.commands import Platform
from crew_health.mission.application.deadlines import sweep_overdue
from crew_health.mission.application.hub_time import event_time
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.ports import (
    ActivationParticipant,
    DayLabelPort,
    EnvironmentFacts,
    LocationPort,
    MissionInitializer,
    MissionStatePort,
    OriginMode,
    OverlayPort,
    RosterPort,
)
from crew_health.mission.application.use_cases import (
    ACTIVATE_HANDLER,
    POST_EVA_HANDLER,
    Caller,
    EventTimeFacts,
    Services,
    activate_due,
    end_post_eva,
)
from crew_health.mission.domain.config import MissionConfig
from crew_health.mission.domain.loader_checks import CHECK_7, CHECK_12, MissionBounds
from crew_health.mission.domain.profile import ProfileLists
from crew_health.mission.settings import specifications
from crew_health.roles.application.ports import DayLabelPort as RolesDayLabelPort
from crew_health.roles.application.ports import LocationPort as RolesLocationPort
from crew_health.roles.application.ports import MissionStatePort as RolesMissionStatePort
from crew_health.roles.application.ports import OverlayPort as RolesOverlayPort
from crew_health.routine.application.ports import MissionStatePort as RoutineMissionStatePort
from crew_health.sensors.application.ports import ProvenanceStreamsPort

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("mission", "msn"),
    ("environment_profile", "env"),
    ("activity_overlay", "ovl"),
    ("mission_event", "mce"),
)
# CONTRACTS 11 checks 7 and 12.effect are also registered by the config module's package checks, which stay
# the required contributors. The registry rejects a duplicate id, so the mission rules run beside them under
# their own id: both must pass, neither replaces the other.
SUFFIX = ".mission"
_MIGRATION = "adapters/migrations/0001_mission.sql"
_MIGRATION_NAME = "mission.0001"


@dataclass
class _Live:
    """What pass 2 builds. The scheduler handlers and the sweep read it and fail closed while it is empty."""

    api: MissionApi
    resolver: Resolver | None = None

    def services(self) -> Services:
        if self.api.services is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_bound", retryable=True)
        return self.api.services


def _shared_state(
    resolver: Resolver, config_of: Callable[[ReleaseHandle], MissionConfig]
) -> LiveMissionState:
    store = SqliteMissionStore(row_limit=resolver.require(HubSettings).integer("mission.max_overlay_rows"))
    return LiveMissionState(
        store,
        resolver.require(ReadSnapshotFactory),
        resolver.require(MissionClock),
        config_of,
    )


def _config_of(release: ReleaseHandle) -> MissionConfig:
    return load_mission_config(lambda name: release.document(name).raw)


def _bind(live: _Live) -> Callable[[Resolver], None]:
    api = live.api

    def bind(resolver: Resolver) -> None:
        live.resolver = resolver
        settings = resolver.require(HubSettings)
        snapshots = resolver.require(ReadSnapshotFactory)
        corrections = resolver.optional(ClockCorrections)
        segments = resolver.optional(ClockSegments)
        streams = resolver.optional(ProvenanceStreamsPort)
        roster = resolver.optional(RosterPort)
        store = SqliteMissionStore(row_limit=settings.integer("mission.max_overlay_rows"))
        state = _shared_state(resolver, _config_of)
        hub_id = settings.text("mission.hub_source_clock_id")

        def lists_of(uow: UnitOfWork, config: MissionConfig, environment_id: str) -> ProfileLists:
            wanted = config.profile(environment_id)
            channels: Sequence[str] = ()
            if streams is not None and wanted is not None:
                channels = tuple(
                    s.channel_id
                    for s in streams.habitat_streams(uow)
                    if s.sensor_type_id in wanted.sensor_type_ids
                )
            return lists_for(lambda name: uow.release.document(name).raw, config, environment_id, channels)

        def participants() -> tuple[ActivationParticipant, ...]:
            found = resolver.optional(ActivationParticipant)
            return () if found is None else (found,)

        def stamped(
            uow: UnitOfWork, at: datetime, origin: Literal["LIVE", "REPLAY", "SIM"]
        ) -> EventTimeFacts:
            return event_time(corrections, segments, uow, source_clock_id=hub_id, at=at, origin_mode=origin)

        api.services = Services(
            store=store,
            ids=resolver.require(IdFactory),
            config_of=_config_of,
            lists_of=lists_of,
            roster=lambda uow: frozenset() if roster is None else roster.person_ids(),
            participants=participants,
            day_end=state.day_end,
        )
        api.reader = store
        api.snapshots = snapshots
        api.platform = Platform(
            uows=resolver.require(UnitOfWorkFactory),
            ledger=resolver.require(IdempotencyStore),
            receipts=resolver.require(CommandReceipts),
        )
        api.ids = resolver.require(IdFactory)
        api.page_max = settings.integer("mission.page_max")
        api.event_time = stamped
        register_policies(resolver.require(UnitOfWorkFactory), POLICIES)
        tables = replicated_tables_of(resolver.require(UnitOfWorkFactory))
        for table, record_type, key in table_declarations():
            tables.register_replicated_table(table, record_type=record_type, key_column=key)

    return bind


class _Late:
    """A loader check whose bounds come from settings, known only in pass 2. Unbound: a finding, never a pass."""

    def __init__(self, check_id: str) -> None:
        self._check_id = check_id
        self.bound: ReleaseCheck | None = None

    @property
    def check_id(self) -> str:
        return self._check_id

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        if self.bound is None:
            return (Finding(self._check_id, None, "", "mission validator unavailable"),)
        return self.bound.check(candidate)


def _checks(resolver: Resolver) -> tuple[Check7Profiles, Check12EffectFilters]:
    settings = resolver.require(HubSettings)
    bounds = MissionBounds(
        settings.integer("mission.max_event_types"), settings.integer("mission.max_state_facts")
    )
    return Check7Profiles(bounds), Check12EffectFilters()


def _consumer(
    registry: ModuleRegistry, port: type[object], build: Callable[[LiveMissionState], object]
) -> None:
    holder: dict[str, LiveMissionState] = {}

    def factory(resolver: Resolver) -> object:
        if "state" not in holder:
            holder["state"] = _shared_state(resolver, _config_of)
        return build(holder["state"])

    registry.provide(port, factory, requires=(HubSettings, ReadSnapshotFactory, MissionClock))


class _Initializer:
    """The operator's one-shot initialization, bound with the rest in pass 2."""

    def __init__(self, live: _Live) -> None:
        self._live = live

    def initialize(
        self,
        *,
        operator_id: str,
        mission_mode: OriginMode,
        planned_length_days: int | None,
        site_node_id: str,
        environment_id: str,
    ) -> str:
        services, platform = self._live.services(), self._live.api.platform
        if platform is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_bound", retryable=True)
        actor = Actor("operator", operator_id)
        with platform.uows.begin(actor=actor, correlation_id="mission_init_" + operator_id) as uow:
            record = initialize_mission(
                services,
                uow,
                Caller(actor, None, frozenset({"operator"})),
                mission_mode=mission_mode,
                planned_length_days=planned_length_days,
                site_node_id=site_node_id,
                environment_id=environment_id,
            )
            identity = services.store.identity(uow)
        if identity is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_identity_missing", retryable=True)
        del record
        return identity.mission_id


def register(registry: ModuleRegistry) -> None:
    api = MissionApi()
    live = _Live(api)
    for spec in specifications():
        registry.setting(spec)
    for record_type, prefix in _PREFIXES:
        registry.id_prefix(record_type=record_type, prefix=prefix)
    for port, build in (
        (MissionStatePort, lambda s: s),
        (OverlayPort, lambda s: s),
        (LocationPort, lambda s: s),
        (DayLabelPort, lambda s: s),
        (EnvironmentFacts, lambda s: s),
        (HelpMissionStatePort, HelpMission),
        (RolesMissionStatePort, RolesMission),
        (RolesOverlayPort, RolesOverlays),
        (RolesLocationPort, RolesLocation),
        (RolesDayLabelPort, RolesDayLabel),
        (GuidanceEnvironmentFacts, GuidanceEnvironment),
        (RoutineMissionStatePort, RoutineMission),
    ):
        _consumer(registry, port, build)
    profiles, filters = _Late(CHECK_7 + SUFFIX), _Late(CHECK_12 + ".effect" + SUFFIX)
    registry.loader_check(profiles)
    registry.loader_check(filters)
    registry.provide(MissionInitializer, lambda _: _Initializer(live))
    registry.bind("mission", _bind(live))
    registry.on_startup(StartupPhase.STORAGE, _storage_hook(live, profiles, filters))
    registry.on_startup(StartupPhase.STATE_SWEEP, _sweep_hook(live))
    registry.scheduler_handler(ACTIVATE_HANDLER, lambda uow, job: activate_due(live.services(), uow, job))
    registry.scheduler_handler(POST_EVA_HANDLER, lambda uow, job: end_post_eva(live.services(), uow, job))
    registry.router(build_router(api))


def _storage_hook(live: _Live, profiles: _Late, filters: _Late) -> Callable[[], None]:
    def storage() -> None:
        resolver = live.resolver
        if resolver is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_bound", retryable=True)
        apply_migration(resolver.require(UnitOfWorkFactory), _MIGRATION, name=_MIGRATION_NAME)
        profiles.bound, filters.bound = _checks(resolver)

    return storage


def _sweep_hook(live: _Live) -> Callable[[], None]:
    """Startup catch up (E23 3.1): an absolute deadline that passed while nothing ran is finished before serving."""

    def sweep() -> None:
        resolver = live.resolver
        if resolver is None or live.api.snapshots is None:
            return
        services = live.services()
        uows = resolver.require(UnitOfWorkFactory)
        with live.api.snapshots.snapshot() as snapshot:
            if services.store.identity(snapshot) is None:
                return  # before mission init there is nothing to finish
        roster = resolver.optional(RosterPort)
        people = () if roster is None else tuple(sorted(roster.person_ids()))
        with uows.begin(
            actor=Actor("engine", "mission_sweep"), correlation_id="mission_startup_sweep"
        ) as uow:
            sweep_overdue(services, uow, people=people)

    return sweep
