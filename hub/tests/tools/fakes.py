from __future__ import annotations

from crew_health.kernel.testing import MemoryIds, MemoryVault
from crew_health.platform.testing.sqlite import test_settings as platform_settings


def identity() -> MemoryVault:
    return MemoryVault(
        ids=MemoryIds(identifiers=()),
        settings=platform_settings(),  # type: ignore[arg-type]
        secrets={"person_a": b"test_owner_secret"},
        authorize=lambda session, reference: False,
    )
