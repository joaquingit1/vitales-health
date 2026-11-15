from __future__ import annotations

import pytest
from tests.tools.test_bounds import settings_values

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.tools.module import register
from crew_health.tools.settings import setting_specs


@pytest.fixture
def tools_settings() -> MemorySettings:
    return MemorySettings(setting_specs(), settings_values())


@pytest.fixture
def tools_declarations(tools_settings: MemorySettings) -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=64, max_hooks=200)
    registry.provide(HubSettings, lambda _: tools_settings)
    register(registry)
    return registry
