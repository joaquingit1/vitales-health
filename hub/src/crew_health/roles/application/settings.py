"""Operational bounds of the roles module (E31 design 8, SYSTEM 5.11): resource limits, never clinical values."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec

_BASIS = "E31 design section 8 operational settings"

# (key, default, minimum, maximum)
BOUNDS: tuple[tuple[str, int, int, int], ...] = (
    ("roles.port_timeout_ms", 100, 10, 1000),
    ("roles.recompute_batch", 16, 1, 128),
    ("roles.retry_base_seconds", 2, 1, 60),
    ("roles.retry_max_seconds", 60, 1, 600),
    ("roles.proposal_overdue_seconds", 30, 5, 600),
    ("roles.max_proposals_per_context", 100, 10, 1000),
    ("roles.max_open_drills", 10, 1, 50),
    ("roles.roster_max", 64, 1, 256),
    ("roles.page_max", 200, 1, 200),
)


class RolesSettingsInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RolesSettings:
    port_timeout_ms: int
    recompute_batch: int
    retry_base_seconds: int
    retry_max_seconds: int
    proposal_overdue_seconds: int
    max_proposals_per_context: int
    max_open_drills: int
    roster_max: int
    page_max: int

    @staticmethod
    def specs() -> tuple[SettingSpec, ...]:
        return tuple(
            SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, _BASIS)
            for key, _, low, high in BOUNDS
        )

    @staticmethod
    def defaults() -> dict[str, object]:
        return {key: default for key, default, _, _ in BOUNDS}

    @classmethod
    def parse(cls, values: dict[str, object]) -> RolesSettings:
        parsed: dict[str, int] = {}
        for key, default, low, high in BOUNDS:
            value = values.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise RolesSettingsInvalid(key)
            parsed[key.removeprefix("roles.")] = value
        if parsed["retry_max_seconds"] < parsed["retry_base_seconds"]:
            raise RolesSettingsInvalid("roles.retry_max_seconds")
        return cls(**parsed)

    @classmethod
    def from_hub(cls, hub: HubSettings) -> RolesSettings:
        return cls.parse({key: hub.integer(key) for key, _, _, _ in BOUNDS})
