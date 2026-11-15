"""Hub settings of the teach module (SYSTEM 5.11; E30 section 11). Resource bounds and timeouts, each with a
validated maximum. `teach.count_max_gap_seconds` carries a clinical basis until X5 moves it into the count step."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec

_BOUND = "resource bound (E30 section 11)"

SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("teach.max_count_seconds", "integer", None, Decimal(1), Decimal(600), None, _BOUND),
    SettingSpec("teach.max_beats_per_second", "integer", None, Decimal(1), Decimal(10), None, _BOUND),
    SettingSpec(
        "teach.count_max_gap_seconds",
        "integer",
        None,
        Decimal(1),
        Decimal(10),
        None,
        "team default, to be set by clinical review (E30 3.3, X5)",
    ),
    SettingSpec("teach.max_attempts", "integer", None, Decimal(1), Decimal(20), None, _BOUND),
    SettingSpec("teach.max_active_checks", "integer", None, Decimal(1), Decimal(256), None, _BOUND),
    SettingSpec("teach.camera_begin_timeout_ms", "integer", None, Decimal(1), Decimal(1000), None, _BOUND),
    SettingSpec("teach.camera_call_timeout_ms", "integer", None, Decimal(1), Decimal(1000), None, _BOUND),
    SettingSpec("teach.reference_wait_ms", "integer", None, Decimal(0), Decimal(5000), None, _BOUND),
    SettingSpec("teach.camera_ticket_margin_s", "integer", None, Decimal(0), Decimal(120), None, _BOUND),
    SettingSpec("teach.clock_tolerance_ms", "integer", None, Decimal(1), Decimal(2000), None, _BOUND),
    SettingSpec("teach.camera_clock_tolerance_ms", "integer", None, Decimal(1), Decimal(1000), None, _BOUND),
    SettingSpec("teach.list_page_max", "integer", None, Decimal(1), Decimal(200), None, _BOUND),
    SettingSpec("teach.content_cache_max", "integer", None, Decimal(1), Decimal(64), None, _BOUND),
    SettingSpec(
        "teach.rate_display_decimals",
        "integer",
        None,
        Decimal(0),
        Decimal(1),
        None,
        "team default until X5 moves it into the compression check target",
    ),
)


@dataclass(frozen=True, slots=True)
class TeachSettings:
    max_count_seconds: int
    max_beats_per_second: int
    count_max_gap_seconds: int
    max_attempts: int
    max_active_checks: int
    camera_begin_timeout_ms: int
    camera_call_timeout_ms: int
    reference_wait_ms: int
    camera_ticket_margin_s: int
    clock_tolerance_ms: int
    camera_clock_tolerance_ms: int
    list_page_max: int
    content_cache_max: int
    rate_display_decimals: int

    @staticmethod
    def read(settings: HubSettings) -> TeachSettings:
        def get(name: str) -> int:
            return settings.integer("teach." + name)

        return TeachSettings(
            max_count_seconds=get("max_count_seconds"),
            max_beats_per_second=get("max_beats_per_second"),
            count_max_gap_seconds=get("count_max_gap_seconds"),
            max_attempts=get("max_attempts"),
            max_active_checks=get("max_active_checks"),
            camera_begin_timeout_ms=get("camera_begin_timeout_ms"),
            camera_call_timeout_ms=get("camera_call_timeout_ms"),
            reference_wait_ms=get("reference_wait_ms"),
            camera_ticket_margin_s=get("camera_ticket_margin_s"),
            clock_tolerance_ms=get("clock_tolerance_ms"),
            camera_clock_tolerance_ms=get("camera_clock_tolerance_ms"),
            list_page_max=get("list_page_max"),
            content_cache_max=get("content_cache_max"),
            rate_display_decimals=get("rate_display_decimals"),
        )
