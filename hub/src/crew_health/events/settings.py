from decimal import Decimal

from crew_health.kernel.settings import SettingSpec


def specifications() -> tuple[SettingSpec, ...]:
    basis = "docs/tdd/E01.md 11 event memory, retention and contract heartbeat bounds"
    bounds = (
        ("events.page_size", 1, 200),
        ("events.retention_hours", 1, 8760),
        ("events.max_subscribers", 1, 128),
        ("events.max_streams", 1, 128),
        ("events.stream_queue_size", 1, 256),
        ("events.heartbeat_seconds", 15, 15),
    )
    return tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, basis)
        for key, low, high in bounds
    )
