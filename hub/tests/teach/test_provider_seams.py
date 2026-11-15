"""Teach resolves help's reader by the provider's class (SYSTEM 6.4)."""

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.help.module import register
from crew_health.teach.application.ports import HelpSessionReader


@pytest.mark.trace("R53", "SYSTEM-6.4")
def test_help_registers_the_reader_teach_consumes() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    assert HelpSessionReader in registry.providers
