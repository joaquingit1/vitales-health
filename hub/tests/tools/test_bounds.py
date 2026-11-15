from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.units import Quantity
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.settings import argument_bounds, setting_specs, validate_settings


def settings_values() -> dict[str, object]:
    values: dict[str, object] = {}
    for spec in setting_specs():
        assert spec.maximum is not None
        values[spec.key] = int(spec.maximum) if spec.unit is None else Quantity(spec.maximum, spec.unit)
    return values


@pytest.mark.trace("T-E12-005", "T-E12-067", "D7")
def test_setting_edit_changes_parser_bound() -> None:
    values = settings_values()
    original = MemorySettings(setting_specs(), values)
    validate_settings(original)
    assert argument_bounds(original).max_string_chars == 2000
    values["tools.max_string_chars"] = 10
    changed = MemorySettings(setting_specs(), values)
    assert argument_bounds(changed).max_string_chars == 10
    assert changed.integer("tools.external_retries") == 0
    assert changed.integer("tools.private_cache_entries") == 0
    assert changed.integer("tools.max_concurrent_calls") == 1


@pytest.mark.trace("T-E12-005", "T-E12-067", "I2")
@pytest.mark.parametrize("replacement", [0, -1, 2001, True])
def test_invalid_bounds_refuse_startup(replacement: object) -> None:
    values = settings_values()
    values["tools.max_string_chars"] = replacement
    with pytest.raises(ProblemError):
        MemorySettings(setting_specs(), values)


@pytest.mark.trace("T-E12-005", "I2")
def test_fractional_byte_count_and_widened_provider_are_rejected() -> None:
    specs = setting_specs()
    spec = next(spec for spec in specs if spec.key == "tools.max_argument_bytes")
    assert spec.unit is not None
    values = settings_values()
    values[spec.key] = Quantity(Decimal("1.5"), spec.unit)
    with pytest.raises(ToolsInvalid):
        validate_settings(MemorySettings(specs, values))
    widened = tuple(
        replace(item, maximum=Decimal(999999999)) if item.key == spec.key else item for item in specs
    )
    values[spec.key] = Quantity(Decimal(999999999), spec.unit)
    with pytest.raises(ToolsInvalid):
        validate_settings(MemorySettings(widened, values))


@pytest.mark.trace("T-E12-067", "D7")
def test_module_declares_settings_registry_and_mandatory_policy_check() -> None:
    from crew_health.app.registry import DeclarationRegistry
    from crew_health.tools.module import register

    registry = DeclarationRegistry(max_ports=64, max_hooks=200)
    register(registry)
    specs = registry.declarations["settings"]
    assert set(specs) == {spec.key for spec in setting_specs()}
    assert len(specs) == 31
    from crew_health.tools.adapters.registry import StaticToolRegistry

    assert StaticToolRegistry in registry.providers
    assert "tools.assistant-policy" in registry.declarations["loader_checks"]
    assert "tools" in registry.bindings
