"""Composition entry for crew_health.kitmap (SYSTEM 5.12, E27 8.2 and 11). No import side effects."""

from __future__ import annotations

import typing
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from fastapi import APIRouter

from crew_health.contracts_gen.schemas.common_schema import BodyRegion, EnvironmentId
from crew_health.guidance.application.ports import EquipmentFacts
from crew_health.kernel.config import ActivationGuard, ConfigBinding, ReleaseCheck
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import ReadSnapshotFactory
from crew_health.kitmap.adapters.release import (
    ConfigKitMapReleaseSource,
    ExampleInLiveGuard,
    KitMapReleaseCheck,
)
from crew_health.kitmap.api.router import KitMapApi, RequireRoles, build_router
from crew_health.kitmap.application.errors import KitMapUnavailable
from crew_health.kitmap.application.ports import (
    ConditionRegistryPort,
    CoverPlanningPort,
    InstrumentReadinessPort,
    KitCapabilitiesPort,
    KitGate,
    KitMapContext,
    KitMapQueries,
    MissionStatePort,
    PlanChange,
    PlanColumns,
    PlanColumnsPort,
    PlanningDelta,
    RosterPort,
)
from crew_health.kitmap.application.queries import KitMapReads, Vocabulary
from crew_health.kitmap.application.whatif import RunWhatIf, WhatIfBounds
from crew_health.kitmap.domain.document import DocumentBounds
from crew_health.plans.application import ports as plans_ports


class Resolver(Protocol):
    """Structural subset of crew_health.app.registry.Resolver (feature modules may not import crew_health.app)."""

    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


@runtime_checkable
class _ProviderDeclarations(Protocol):
    @property
    def providers(self) -> Mapping[type[object], object]: ...


class ModuleRegistry(Protocol):
    """Structural subset of crew_health.app.registry.ModuleRegistry used by this module."""

    def router(self, router: APIRouter) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def activation_guard(self, guard: ActivationGuard) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


# key, minimum, maximum, basis (E27 section 11 team defaults)
_SPECS: tuple[tuple[str, int, int, str], ...] = (
    ("kitmap.max_document_bytes", 1024, 16_777_216, "team default 1048576: E01 config document bound"),
    ("kitmap.max_conditions", 1, 1000, "team default 200: the 119 IMM conditions with room"),
    ("kitmap.max_tests_per_row", 1, 64, "team default 8"),
    ("kitmap.max_instruments_per_test", 1, 64, "team default 8"),
    ("kitmap.max_instruments", 1, 1024, "team default 128"),
    ("kitmap.max_capabilities", 1, 1024, "team default 128"),
    ("kitmap.max_findings_per_row", 1, 256, "team default 32"),
    ("kitmap.max_text_chars", 16, 100_000, "team default 2000"),
    ("kitmap.max_url_chars", 16, 8192, "team default 2048"),
    ("kitmap.gate_max_condition_ids", 1, 256, "team default 16"),
    ("kitmap.whatif_max_instruments", 1, 1024, "team default 64"),
    ("kitmap.whatif_max_supplies", 1, 1024, "team default 64"),
    ("kitmap.whatif_max_skills", 1, 1024, "team default 32"),
    ("kitmap.whatif_max_people_per_skill", 1, 1024, "team default 64"),
    ("kitmap.whatif_max_quantity", 1, 1_000_000_000, "E32 supplies.max_quantity, 1000000"),
    ("kitmap.whatif_max_quantity_scale", 0, 12, "E32 supplies.max_quantity_scale, 6"),
    ("kitmap.parsed_release_cache", 1, 64, "team default 4"),
)


def setting_specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            key=key,
            kind="integer",
            unit=None,
            minimum=Decimal(low),
            maximum=Decimal(high),
            max_text_bytes=None,
            basis=basis,
        )
        for key, low, high, basis in _SPECS
    )


def vocabulary() -> Vocabulary:
    """Environment and body region orders from the generated contract enums (common.schema)."""
    environments = typing.get_args(EnvironmentId.model_fields["root"].annotation)
    regions = typing.get_args(BodyRegion.model_fields["root"].annotation)
    return Vocabulary(environments=tuple(environments), body_regions=tuple(regions))


class _PlanColumnsAdapter:
    """Owner-side translation keeps plans independent of kitmap's domain types."""

    def __init__(self) -> None:
        self.source: plans_ports.PlanColumnsPort | None = None

    def row_columns(
        self, ctx: KitMapContext[object], *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]:
        if self.source is None:
            raise KitMapUnavailable("onboard plans present but no plan columns provider")
        copied = plans_ports.KitMapContext(ctx.snapshot, ctx.release, ctx.environment_id, ctx.at)
        return tuple(
            PlanColumns(c.plan_id, c.written_for, c.plan_aboard)
            for c in self.source.row_columns(copied, kit=kit, plan_ids=plan_ids)
        )

    def plans_changed(
        self, ctx: KitMapContext[object], *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]:
        if self.source is None:
            raise KitMapUnavailable("onboard plans present but no plan columns provider")
        copied = plans_ports.KitMapContext(ctx.snapshot, ctx.release, ctx.environment_id, ctx.at)
        additions = plans_ports.PlanningDelta(
            delta.add_instruments,
            tuple(plans_ports.SupplyAddition(v.supply_id, v.quantity) for v in delta.add_supplies),
            tuple(plans_ports.SkillAddition(v.skill_id, v.person_ids) for v in delta.add_skills),
        )
        return tuple(
            PlanChange(
                c.plan_id,
                c.condition_id,
                c.flow_id,
                c.before_plan_aboard,
                c.before_waits_for_mo,
                c.after_plan_aboard,
                c.after_waits_for_mo,
            )
            for c in self.source.plans_changed(copied, base_kit=base_kit, delta=additions)
        )


class KitMapWiring:
    """One instance per registration: the objects the provider factories build and the late bindings fill."""

    def __init__(self) -> None:
        self.source: ConfigKitMapReleaseSource | None = None
        self.reads: KitMapReads | None = None
        self.what_if: RunWhatIf | None = None
        self.mission: MissionStatePort | None = None
        self.conditions: ConditionRegistryPort | None = None
        self.api = KitMapApi()
        self.plan_columns = _PlanColumnsAdapter()

    def build(self, resolver: Resolver) -> KitMapReads:
        if self.reads is not None:
            return self.reads
        settings = resolver.require(HubSettings)  # type: ignore[type-abstract]
        snapshots = resolver.require(ReadSnapshotFactory)  # type: ignore[type-abstract]
        words = vocabulary()

        def value(name: str) -> int:
            return settings.integer("kitmap." + name)

        self.source = ConfigKitMapReleaseSource(
            bounds=DocumentBounds(
                max_document_bytes=value("max_document_bytes"),
                max_conditions=value("max_conditions"),
                max_tests_per_row=value("max_tests_per_row"),
                max_instruments_per_test=value("max_instruments_per_test"),
                max_instruments=value("max_instruments"),
                max_capabilities=value("max_capabilities"),
                max_findings_per_row=value("max_findings_per_row"),
                max_text_chars=value("max_text_chars"),
                max_url_chars=value("max_url_chars"),
            ),
            environments=words.environments,
            conditions=lambda: self.conditions,  # late bound; absent at activation is a check 11c finding
            cache_size=value("parsed_release_cache"),
        )
        self.reads = KitMapReads(
            snapshots=snapshots.snapshot,
            releases=self.source,
            config=resolver.require(ConfigBinding),  # type: ignore[type-abstract]
            vocabulary=words,
            gate_max_condition_ids=value("gate_max_condition_ids"),
        )
        self.what_if = RunWhatIf(
            self.reads,
            WhatIfBounds(
                max_instruments=value("whatif_max_instruments"),
                max_supplies=value("whatif_max_supplies"),
                max_skills=value("whatif_max_skills"),
                max_people_per_skill=value("whatif_max_people_per_skill"),
                max_quantity=Decimal(value("whatif_max_quantity")),
                max_quantity_scale=value("whatif_max_quantity_scale"),
            ),
        )
        self.api.reads, self.api.what_if = self.reads, self.what_if
        self.api.ids = resolver.require(IdFactory)  # type: ignore[type-abstract]
        return self.reads

    def attach(self, resolver: Resolver) -> None:
        """Late binding: providers of consumed ports may be absent; each absence fails closed where it is used."""
        if self.reads is None or self.what_if is None:
            raise ValueError("kit map providers were not built")
        self.mission = resolver.optional(MissionStatePort)  # type: ignore[type-abstract]
        self.conditions = resolver.optional(ConditionRegistryPort)  # type: ignore[type-abstract]
        self.reads.readiness = resolver.optional(InstrumentReadinessPort)  # type: ignore[type-abstract]
        self.reads.mission = self.mission
        self.plan_columns.source = resolver.optional(plans_ports.PlanColumnsPort)  # type: ignore[type-abstract]
        candidate = resolver.optional(PlanColumnsPort)  # type: ignore[type-abstract]
        self.reads.plans = (
            None if candidate is self.plan_columns and self.plan_columns.source is None else candidate
        )
        self.what_if.roster = resolver.optional(RosterPort)  # type: ignore[type-abstract]
        self.what_if.cover = resolver.optional(CoverPlanningPort)  # type: ignore[type-abstract]
        self.api.require_roles = resolver.optional(RequireRoles)  # type: ignore[type-abstract]


_REQUIRES: tuple[type[object], ...] = (HubSettings, ReadSnapshotFactory, ConfigBinding, IdFactory)


def register(registry: ModuleRegistry) -> None:
    wiring = KitMapWiring()
    for spec in setting_specs():
        registry.setting(spec)
    if not isinstance(registry, _ProviderDeclarations) or PlanColumnsPort not in registry.providers:
        registry.provide(PlanColumnsPort, lambda _: wiring.plan_columns)  # type: ignore[type-abstract]
    registry.provide(KitGate, wiring.build, requires=_REQUIRES)  # type: ignore[type-abstract]
    registry.provide(KitMapQueries, wiring.build, requires=_REQUIRES)  # type: ignore[type-abstract]
    registry.provide(KitCapabilitiesPort, wiring.build, requires=_REQUIRES)  # type: ignore[type-abstract]
    registry.provide(EquipmentFacts, wiring.build, requires=_REQUIRES)  # type: ignore[type-abstract]
    registry.loader_check(KitMapReleaseCheck(lambda: wiring.source))
    registry.activation_guard(ExampleInLiveGuard(lambda: wiring.source, lambda: wiring.mission))
    registry.router(build_router(wiring.api))
    registry.bind("kitmap.attach_consumed_ports", wiring.attach)
