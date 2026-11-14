"""Composition entry for crew_health.guidance (SYSTEM 5.12, 8.2). No import side effects."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from crew_health.guidance.application.facade import EligibilityFacade, FactProviders, GuidanceBounds
from crew_health.guidance.application.ports import (
    ContentFacts,
    EnvironmentFacts,
    EquipmentFacts,
    GuidanceEligibility,
    SkillFacts,
    StockFacts,
)
from crew_health.kernel.settings import HubSettings, SettingSpec


class Resolver(Protocol):
    """Structural subset of crew_health.app.registry.Resolver (feature modules may not import crew_health.app)."""

    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    """Structural subset of crew_health.app.registry.ModuleRegistry used by this module."""

    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


MAX_REQUIREMENT_ITEMS = "guidance.max_requirement_items"
MAX_OPERATORS = "guidance.max_operators"


def setting_specs() -> tuple[SettingSpec, ...]:
    return (
        SettingSpec(
            key=MAX_REQUIREMENT_ITEMS,
            kind="integer",
            unit=None,
            minimum=Decimal(1),
            maximum=Decimal(256),
            max_text_bytes=None,
            basis="team default: largest instrument, supply or skill list one guidance request may carry",
        ),
        SettingSpec(
            key=MAX_OPERATORS,
            kind="integer",
            unit=None,
            minimum=Decimal(1),
            maximum=Decimal(256),
            max_text_bytes=None,
            basis="team default: largest roster the operator rule evaluates",
        ),
    )


def _build(resolver: Resolver) -> GuidanceEligibility:
    settings = resolver.require(HubSettings)  # type: ignore[type-abstract]
    return EligibilityFacade(
        GuidanceBounds(
            max_requirement_items=settings.integer(MAX_REQUIREMENT_ITEMS),
            max_operators=settings.integer(MAX_OPERATORS),
        )
    )


def _attach(resolver: Resolver) -> None:
    facade = resolver.require(GuidanceEligibility)  # type: ignore[type-abstract]
    if not isinstance(facade, EligibilityFacade):
        raise ValueError("guidance facade replaced by another provider")
    facade.attach(
        FactProviders(
            content=resolver.optional(ContentFacts),  # type: ignore[type-abstract]
            environment=resolver.optional(EnvironmentFacts),  # type: ignore[type-abstract]
            equipment=resolver.optional(EquipmentFacts),  # type: ignore[type-abstract]
            skills=resolver.optional(SkillFacts),  # type: ignore[type-abstract]
            stock=resolver.optional(StockFacts),  # type: ignore[type-abstract]
        )
    )


def register(registry: ModuleRegistry) -> None:
    for spec in setting_specs():
        registry.setting(spec)
    registry.provide(GuidanceEligibility, _build, requires=(HubSettings,))  # type: ignore[type-abstract]
    registry.bind("guidance.attach_fact_providers", _attach)
