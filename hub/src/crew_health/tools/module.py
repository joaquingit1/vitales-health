from __future__ import annotations

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.kernel.settings import HubSettings
from crew_health.measurement.application.ports import CapabilityQueries
from crew_health.tools.adapters.handlers.capabilities import CapabilitiesHandler
from crew_health.tools.adapters.jsonschema_validator import PolicyValidator
from crew_health.tools.adapters.registry import StaticToolRegistry
from crew_health.tools.settings import setting_specs


def _registry(resolver: Resolver) -> StaticToolRegistry:
    return StaticToolRegistry(resolver.require(HubSettings))


def register(registry: ModuleRegistry) -> None:
    for spec in setting_specs():
        registry.setting(spec)
    policy = PolicyValidator()
    registry.loader_check(policy)
    registry.provide(StaticToolRegistry, _registry, requires=(HubSettings,))

    def bind(resolver: Resolver) -> None:
        settings = resolver.require(HubSettings)
        policy.bind(settings)
        handlers = resolver.require(StaticToolRegistry)
        handlers.register(
            "get_capabilities", CapabilitiesHandler(resolver.optional(CapabilityQueries), settings)
        )
        handlers.freeze()

    registry.bind("tools", bind)
