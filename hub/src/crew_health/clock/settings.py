from decimal import Decimal

from crew_health.kernel.settings import SettingSpec


def specifications() -> tuple[SettingSpec, ...]:
    basis = "docs/SYSTEM.md 5.2 and docs/tdd/E01.md 11 clock serialization and operational bounds"
    bounds = (
        ("clock.regression_tolerance_ms", 0, 60000),
        ("clock.future_tolerance_ms", 0, 60000),
        ("clock.max_uncertainty_ms", 0, 60000),
        ("clock.hlc_future_ms", 0, 60000),
        ("clock.hlc_logical_max", 1, 2147483647),
        ("clock.history_page_size", 1, 200),
    )
    return tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, basis)
        for key, low, high in bounds
    )
