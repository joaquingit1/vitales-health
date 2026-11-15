from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.clock import Clock
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemorySettings
from crew_health.tools.module import register
from crew_health.tools.settings import setting_specs
from tests.tools.test_bounds import settings_values


@pytest.fixture
def tools_settings() -> MemorySettings:
    return MemorySettings(setting_specs(), settings_values())


@pytest.fixture
def tools_declarations(tools_settings: MemorySettings) -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=64, max_hooks=200)
    registry.provide(HubSettings, lambda _: tools_settings)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda _: MemoryClock(datetime(2026, 1, 1, tzinfo=UTC), 0, "epoch_a"))  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda _: MemoryIds(identifiers=tuple(str(i) for i in range(50))))  # type: ignore[type-abstract]
    register(registry)
    return registry


@pytest.fixture(scope="session")
def station_resources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """E01's station resources (hub/tests/app/conftest.py) for the tools composition tests."""
    from tests.app.conftest import copy_station_resources

    return copy_station_resources(tmp_path_factory.mktemp("station") / "resources")


# E17's installed-release fixtures (its candidate manifest and a ManifestStore over a copy of model/).
from tests.model.conftest import manifest, store  # noqa: E402, F401
