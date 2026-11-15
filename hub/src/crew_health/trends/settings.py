"""Bounded operational settings for deterministic trends and optional forecasts."""

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec


def specs() -> tuple[SettingSpec, ...]:
    bounds = (
        ("trends.history_max_rows", 1, 1000),
        ("trends.max_codes", 1, 200),
        ("trends.environment_points_max", 1, 10000),
        ("trends.forecast_steps_max", 1, 366),
        ("trends.forecast_staging_max", 1, 1024),
        ("workers.forecast.timeout_ms", 100, 120000),
        ("workers.forecast.response_max_bytes", 1024, 1048576),
    )
    integers = tuple(
        SettingSpec(
            key,
            "integer",
            None,
            Decimal(low),
            Decimal(high),
            None,
            "SYSTEM 5.11 and E14 bounded history, worker execution and staging budgets",
        )
        for key, low, high in bounds
    )
    return integers + (
        SettingSpec(
            "workers.forecast.enabled",
            "boolean",
            None,
            None,
            None,
            None,
            "E14 explicit opt in for the isolated experimental forecast worker",
        ),
        SettingSpec(
            "workers.forecast.base_url",
            "text",
            None,
            None,
            None,
            256,
            "E14 worker transport endpoint byte budget",
        ),
        SettingSpec(
            "workers.forecast.token_path",
            "text",
            None,
            None,
            None,
            1024,
            "E14 local worker credential path byte budget",
        ),
    )


@dataclass(frozen=True, slots=True)
class TrendsSettings:
    history_max_rows: int
    max_codes: int
    environment_points_max: int
    forecast_steps_max: int
    forecast_staging_max: int
    worker_enabled: bool
    worker_base_url: str
    worker_token_path: str
    worker_timeout_ms: int
    worker_response_max_bytes: int

    @classmethod
    def read(cls, settings: HubSettings) -> TrendsSettings:
        return cls(
            settings.integer("trends.history_max_rows"),
            settings.integer("trends.max_codes"),
            settings.integer("trends.environment_points_max"),
            settings.integer("trends.forecast_steps_max"),
            settings.integer("trends.forecast_staging_max"),
            settings.boolean("workers.forecast.enabled"),
            settings.text("workers.forecast.base_url"),
            settings.text("workers.forecast.token_path"),
            settings.integer("workers.forecast.timeout_ms"),
            settings.integer("workers.forecast.response_max_bytes"),
        )
