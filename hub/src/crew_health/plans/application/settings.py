"""Finite resource bounds read from the hub settings, with no embedded defaults."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.settings import HubSettings, SettingSpec

BOUNDS: tuple[tuple[str, int], ...] = (
    ("max_plans_per_document", 1000),
    ("max_steps_per_plan", 200),
    ("max_observations_per_record", 256),
    ("max_rechecks_per_step", 5000),
    ("max_escalations_per_run", 1024),
    ("max_text_chars", 20000),
    ("parsed_plan_cache_entries", 4096),
    ("page_limit_max", 200),
    ("max_document_bytes", 67108864),
)


@dataclass(frozen=True, slots=True)
class PlansSettings:
    max_plans_per_document: int
    max_steps_per_plan: int
    max_observations_per_record: int
    max_rechecks_per_step: int
    max_escalations_per_run: int
    max_text_chars: int
    parsed_plan_cache_entries: int
    page_limit_max: int
    max_document_bytes: int

    @staticmethod
    def specs() -> tuple[SettingSpec, ...]:
        return tuple(
            SettingSpec(
                "plans." + name,
                "integer",
                None,
                Decimal(1),
                Decimal(maximum),
                None,
                "team default: finite operational bound for " + name,
            )
            for name, maximum in BOUNDS
        )

    @classmethod
    def read(cls, settings: HubSettings) -> PlansSettings:
        values: dict[str, int] = {}
        for name, maximum in BOUNDS:
            value = settings.integer("plans." + name)
            if isinstance(value, bool) or not 1 <= value <= maximum:
                raise ProblemError(ErrorCode.CONFIG_INVALID, "plans." + name)
            values[name] = value
        return cls(**values)
