"""The consumer resolves the real kit provider by class identity (SYSTEM 6.4)."""

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.kitmap.application.ports import KitCheckFacts
from crew_health.kitmap.module import register
from crew_health.measurement.application.mapping import kit_check
from crew_health.measurement.application.ports import KitCapabilitiesPort, KitGate
from crew_health.measurement.domain.errors import MeasurementInvariant


@pytest.mark.trace("R07", "V78", "SYSTEM-6.4")
def test_kitmap_registers_the_ports_measurement_consumes() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    assert KitGate in registry.providers
    assert KitCapabilitiesPort in registry.providers


@pytest.mark.trace("R07", "V78", "I1")
def test_provider_kit_facts_cannot_bypass_measurement_invariants() -> None:
    invalid = KitCheckFacts(True, (), (), (), (), "rendered kit check", "version", "environment")
    with pytest.raises(MeasurementInvariant, match="kit_check_can_requires_instruments"):
        kit_check(invalid)
