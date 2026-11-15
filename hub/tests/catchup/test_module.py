"""SYSTEM 4 and lesson 15: actual provider class identity, never a consumer lookalike."""

import pytest
from fastapi import APIRouter

from crew_health.app.registry import DeclarationRegistry
from crew_health.catchup.application.ports import GroundLogView
from crew_health.catchup.module import CatchUpRuntime, register
from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.application.ports import BundleOutbox, GroundLinkQuery
from crew_health.groundlink.module import register as register_groundlink
from crew_health.help.application.ports import GroundLogComposer, HelpLogSnapshots
from crew_health.help.module import register as register_help
from crew_health.kernel.settings import SettingSpec


@pytest.mark.trace("SYSTEM-4")
def test_register_provides_exact_help_class_and_real_provider_seams() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    assert GroundLogComposer in registry.providers and GroundLogView in registry.providers
    assert "cuc" in registry.declarations["prefixes"]
    specs = registry.declarations["settings"]
    assert set(specs) == {
        "catchup.log_max_items",
        "catchup.port_timeout_ms",
        "catchup.max_lines",
        "catchup.subscriber_max_attempts",
    }
    for spec in specs.values():
        assert isinstance(spec, SettingSpec) and spec.basis and spec.minimum == 1 and spec.maximum is not None
    routers = [
        router for router in registry.declarations["routers"].values() if isinstance(router, APIRouter)
    ]
    assert {getattr(route, "operation_id", "") for router in routers for route in router.routes} == {
        "getGroundLog"
    }
    subscriber = registry.declarations["durable_subscribers"]["catchup.ground_message"]
    assert isinstance(subscriber, tuple) and subscriber[0] == frozenset({EventType.GROUND_MESSAGE_CHANGED})
    register_help(registry)
    register_groundlink(registry)
    assert HelpLogSnapshots in registry.providers
    assert GroundLinkQuery in registry.providers and BundleOutbox in registry.providers


@pytest.mark.trace("SYSTEM-4")
def test_unbound_runtime_fails_loudly() -> None:
    from crew_health.catchup.domain.errors import CatchUpUnavailable

    runtime = CatchUpRuntime()
    with pytest.raises(CatchUpUnavailable):
        _ = runtime.composer
    with pytest.raises(CatchUpUnavailable):
        _ = runtime.log_view
