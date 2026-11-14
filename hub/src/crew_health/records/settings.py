from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import SettingSpec


def specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            key,
            "integer",
            None,
            Decimal(1),
            Decimal(maximum),
            None,
            "docs/tdd/E01.md section 11 record query operational ceilings",
        )
        for key, maximum in (
            ("records.query_max_ids", 1000),
            ("records.query_max_codes", 200),
            ("records.query_max_rows", 1000),
            ("records.query_max_window_seconds", 315360000),
        )
    )
