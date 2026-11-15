from decimal import Decimal

from crew_health.kernel.settings import SettingSpec


def specifications() -> tuple[SettingSpec, ...]:
    bounds = {
        "max_bytes": (1024, 8388608),
        "max_chunks": (1, 65536),
        "max_tools": (1, 128),
        "connect_timeout_ms": (1, 10000),
        "cancel_poll_ms": (1, 200),
        "memory_budget_bytes": (1, 7500000000),
        "cpu_budget": (1, 6),
        "nice": (0, 19),
        "stop_grace_ms": (1, 5000),
    }
    return tuple(
        SettingSpec(
            "model." + key,
            "integer",
            None,
            Decimal(lo),
            Decimal(hi),
            None,
            "Operational resource bound, E17 5.2 and DEPLOYMENT 7.3; no clinical meaning",
        )
        for key, (lo, hi) in bounds.items()
    )
