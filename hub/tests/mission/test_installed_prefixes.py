"""SYSTEM 5.12: every installed module declares into one registry, which rejects a duplicate ID prefix, loader check
or other declaration. Mission and sensors must register beside every module on main without a collision."""

from __future__ import annotations

from crew_health.app.installed_modules import REGISTRATIONS
from crew_health.app.registry import DeclarationRegistry


def test_every_installed_module_registers_into_one_registry_without_a_duplicate() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    owners = []
    for register in REGISTRATIONS:
        register(registry)  # type: ignore[arg-type]
        owners.append(register.__module__)
    assert "crew_health.mission.module" in owners and "crew_health.sensors.module" in owners
