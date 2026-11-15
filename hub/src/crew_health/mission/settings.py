"""Mission hub settings (E23 11, SYSTEM 5.11). Operational bounds only."""

from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import SettingSpec

BASIS = "docs/tdd/E23.md section 11 proposed operational settings, basis team default"

INTEGERS: tuple[tuple[str, int, int], ...] = (
    ("mission.page_max", 200, 1),
    ("mission.max_overlay_rows", 500, 1),
    ("mission.max_event_types", 128, 1),
    ("mission.max_state_facts", 128, 1),
)
TEXTS: tuple[tuple[str, int], ...] = (("mission.hub_source_clock_id", 128),)  # the hub's own capture stream (S5.2)
DEFAULTS: dict[str, object] = {**{key: seed for key, seed, _ in INTEGERS}, "mission.hub_source_clock_id": "hub_clock"}


def specifications() -> tuple[SettingSpec, ...]:
    integers = tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(seed), None, BASIS)
        for key, seed, low in INTEGERS
    )
    texts = tuple(SettingSpec(key, "text", None, None, None, size, BASIS) for key, size in TEXTS)
    return (*integers, *texts)
