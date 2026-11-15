"""Help hub settings (SYSTEM 5.11, E26 5.4): operational bounds with their team defaults, never content."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.help.domain.session import HelpLimits
from crew_health.kernel.settings import HubSettings, SettingSpec

_BASIS = "team default (E26 5.4)"

# key, minimum, maximum, team default
HELP_SETTING_ROWS: tuple[tuple[str, int, int, int], ...] = (
    ("help.log_soft_cap_bytes", 1024, 16777216, 786432),
    ("help.log_hard_cap_bytes", 1024, 16777216, 1048576),
    ("help.path_max_entries", 10, 10000, 500),
    ("help.offline_path_max", 1, 1000, 50),
    ("help.note_max_chars", 1, 100000, 2000),
    ("help.max_open_sessions", 1, 1000, 20),
    ("help.page_max", 1, 1000, 100),
    ("help.turn_timeout_s", 1, 3600, 120),
    ("help.ground_events_max_per_message", 1, 10000, 200),
    ("help.docs_cache_entries", 1, 64, 8),
    ("help.port_timeout_ms", 10, 10000, 100),
    ("help.worker_port_timeout_ms", 10, 60000, 1000),
    ("help.worker_batch", 1, 1000, 20),
    ("help.worker_interval_ms", 100, 600000, 5000),
)


def help_setting_specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, _BASIS)
        for key, low, high, _ in HELP_SETTING_ROWS
    )


def help_setting_defaults() -> dict[str, object]:
    return {key: default for key, _, _, default in HELP_SETTING_ROWS}


@dataclass(frozen=True, slots=True)
class HelpBounds:
    limits: HelpLimits
    max_open_sessions: int
    page_max: int
    turn_timeout_s: int
    ground_events_max: int
    docs_cache_entries: int
    port_timeout_ms: int
    worker_port_timeout_ms: int
    worker_batch: int
    worker_interval_ms: int


def read_bounds(settings: HubSettings) -> HelpBounds:
    note_max = settings.integer("help.note_max_chars")
    limits = HelpLimits(
        log_soft_cap_bytes=settings.integer("help.log_soft_cap_bytes"),
        log_hard_cap_bytes=settings.integer("help.log_hard_cap_bytes"),
        path_max_entries=settings.integer("help.path_max_entries"),
        note_max_chars=note_max,
        place_max_chars=note_max,
        offline_path_max=settings.integer("help.offline_path_max"),
    )
    return HelpBounds(
        limits=limits,
        max_open_sessions=settings.integer("help.max_open_sessions"),
        page_max=settings.integer("help.page_max"),
        turn_timeout_s=settings.integer("help.turn_timeout_s"),
        ground_events_max=settings.integer("help.ground_events_max_per_message"),
        docs_cache_entries=settings.integer("help.docs_cache_entries"),
        port_timeout_ms=settings.integer("help.port_timeout_ms"),
        worker_port_timeout_ms=settings.integer("help.worker_port_timeout_ms"),
        worker_batch=settings.integer("help.worker_batch"),
        worker_interval_ms=settings.integer("help.worker_interval_ms"),
    )
