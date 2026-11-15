"""SYSTEM 5.11: operational limits, never mission content."""

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec

MAX_LINES_CEILING = 1024


def catchup_setting_specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(key, "integer", None, Decimal(1), Decimal(maximum), None, basis)
        for key, maximum, basis in (
            (
                "catchup.log_max_items",
                10000,
                "P29-2 interim: a log larger than this answers a retryable INTERNAL_ERROR, never a partial result",
            ),
            ("catchup.port_timeout_ms", 60000, "SYSTEM 5.11: bound on each optional provider call"),
            ("catchup.max_lines", MAX_LINES_CEILING, "SYSTEM 5.11: lines per catch-up section"),
            (
                "catchup.subscriber_max_attempts",
                20,
                "SYSTEM 5.11: deliveries of one event before the catch-up subscriber quarantines it",
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class CatchUpSettings:
    log_max_items: int
    port_timeout_ms: int
    max_lines: int
    subscriber_max_attempts: int

    @classmethod
    def read(cls, settings: HubSettings) -> CatchUpSettings:
        return cls(*(settings.integer(s.key) for s in catchup_setting_specs()))
