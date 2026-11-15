import pytest
from fastapi import APIRouter

from crew_health.app.registry import DeclarationRegistry
from crew_health.groundlink.application.ports import BundleInbox, BundleOutbox, GroundLinkQuery
from crew_health.groundlink.module import GroundLinkRuntime, register
from crew_health.help.application.ports import GroundMessageLifecyclePort
from crew_health.help.module import register as register_help
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.settings import SettingSpec


@pytest.mark.trace("SYSTEM-4")
def test_module_declares_all_seams_and_binds_real_help_port_class() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    for spec in registry.declarations["settings"].values():
        assert isinstance(spec, SettingSpec) and spec.basis and spec.maximum is not None
    assert set(registry.declarations["prefixes"]) == {"glb", "gla", "glc"}
    routers = [r for r in registry.declarations["routers"].values() if isinstance(r, APIRouter)]
    assert {getattr(route, "path", "") for r in routers for route in r.routes} == {
        "/v1/ground-link",
        "/v1/demo/ground-link",
    }
    assert set(registry.declarations["durable_subscribers"]) == {
        "groundlink.environment",
        "groundlink.outbox",
    }
    assert "groundlink.sim_schedule" in registry.declarations["loader_checks"]
    assert "groundlink.link" in registry.declarations["audiences"]
    for port in (GroundLinkQuery, BundleOutbox, BundleInbox):
        assert port in registry.providers
    register_help(registry)
    assert GroundMessageLifecyclePort in registry.providers


@pytest.mark.trace("SYSTEM-4")
def test_unbound_facade_is_retryable_failure() -> None:
    runtime = GroundLinkRuntime()
    with pytest.raises(ProblemError) as error:
        _ = runtime.services
    assert error.value.retryable
