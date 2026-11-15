"""Sensor hub settings (E23 11, SYSTEM 5.11). Operational bounds only: no source, freshness, coverage, quality
or clearing check can be relaxed through any of them."""

from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import SettingSpec

BASIS = "docs/tdd/E23.md section 11 proposed operational settings, basis team default"

# key, seed (also the ceiling), minimum
INTEGERS: tuple[tuple[str, int, int], ...] = (
    ("sensors.batch_max_readings", 1000, 1),
    ("sensors.aggregate_bucket_s", 3600, 1),
    ("sensors.window_scan_max_readings", 20000, 1),
    ("sensors.window_max_buckets", 10080, 1),
    ("sensors.series_max_points", 2000, 1),
    ("sensors.page_max", 200, 1),
    ("sensors.max_channels", 256, 1),
    ("sensors.max_types", 64, 1),
    ("sensors.max_limits", 1024, 1),
    ("sensors.freshness_sweep_s", 30, 1),
    ("sensors.adapter_heartbeat_max_s", 60, 1),
    ("sensors.adapter_poll_s", 15, 1),
    ("sensors.adapter_poll_timeout_s", 2, 1),
    ("sensors.adapter_poll_max", 32, 1),
    ("sensors.scheduler_lag_max_s", 60, 1),
    ("sensors.sweep_page", 200, 1),
    ("sensors.raw_row_max_bytes", 65536, 1),
    ("sensors.plausibility_history_max", 64, 2),
)
TEXTS: tuple[tuple[str, int], ...] = (
    ("sensors.channel_registry_path", 1024),
    ("sensors.demo_registry_path", 1024),
    ("sensors.deployment_profile", 64),
    ("sensors.hub_source_clock_id", 128),
)
BOOLEANS: tuple[str, ...] = ("sensors.sweep_timer_enabled",)

# Seeds a deployment or a test starts from. The maximum of each integer is its seed.
DEFAULTS: dict[str, object] = {
    **{key: seed for key, seed, _ in INTEGERS},
    "sensors.aggregate_bucket_s": 60,
    "sensors.channel_registry_path": "",
    "sensors.demo_registry_path": "",
    "sensors.deployment_profile": "",
    "sensors.hub_source_clock_id": "hub_clock",
    "sensors.sweep_timer_enabled": False,
}


def specifications() -> tuple[SettingSpec, ...]:
    integers = tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(seed), None, BASIS)
        for key, seed, low in INTEGERS
    )
    texts = tuple(SettingSpec(key, "text", None, None, None, size, BASIS) for key, size in TEXTS)
    booleans = tuple(SettingSpec(key, "boolean", None, None, None, None, BASIS) for key in BOOLEANS)
    return (*integers, *texts, *booleans)
