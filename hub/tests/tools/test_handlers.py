from __future__ import annotations

import pytest

from crew_health.kernel.testing import MemorySettings
from crew_health.measurement.application.ports import CapabilityView
from crew_health.tools.adapters.handlers.capabilities import CapabilitiesHandler
from crew_health.tools.application.ports import HandlerOk, HandlerRejected, HandlerUnavailable
from crew_health.tools.settings import setting_specs
from tests.tools.test_bounds import settings_values
from tests.tools.test_gateway import View, scope


@pytest.mark.trace("T-E12-005", "T-E12-011", "R45")
def test_missing_capability_owner_is_explicitly_unavailable() -> None:
    handler = CapabilitiesHandler(None, MemorySettings(setting_specs(), settings_values()))
    assert isinstance(handler.handle(View(), scope(), {"codes": []}, scope().deadline), HandlerUnavailable)


@pytest.mark.trace("T-E12-001", "T-E12-011", "R17", "R45")
def test_unknown_capability_is_not_invented() -> None:
    class Empty:
        def list(self, uow: object) -> tuple[CapabilityView, ...]:
            return ()

        def get(self, uow: object, code: str) -> CapabilityView | None:
            return None

    handler = CapabilitiesHandler(Empty(), MemorySettings(setting_specs(), settings_values()))
    assert isinstance(
        handler.handle(View(), scope(), {"codes": ["not_a_capability"]}, scope().deadline), HandlerRejected
    )
    result = handler.handle(View(), scope(), {"codes": []}, scope().deadline)
    assert isinstance(result, HandlerOk)
    assert result.data == b'{"items":[]}'


@pytest.mark.trace("T-E12-011", "T-E12-067", "I7")
def test_registered_optional_provider_remains_unavailable(tools_declarations) -> None:
    from crew_health.tools.adapters.registry import StaticToolRegistry

    resolver = tools_declarations.resolve()
    registry = resolver.require(StaticToolRegistry)
    handler = registry.handler("get_capabilities")
    assert handler is not None
    assert isinstance(handler.handle(View(), scope(), {"codes": []}, scope().deadline), HandlerUnavailable)
