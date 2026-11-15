"""Hub settings of the participation module (SYSTEM 5.11). Operational ceilings only: no clinical or goal value."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec

# A privacy invariant, not configuration: no setting or document can lower the minimum below this.
MIN_COUNTING_FLOOR = 3

_SETTINGS = (
    ("projection_batch", 256, 1, 1000, "E22 5.5: intents fetched per drain"),
    ("projection_attempts", 5, 1, 5, "E22 5.5: retries per intent before it is parked"),
    ("max_completion_days", 500, 1, 999, "SYSTEM 5.11 storage.max_rows: eligible days read for one view"),
    (
        "min_counting_members",
        3,
        3,
        32,
        "D10 privacy floor: members who must count before any crew goal total; never below 3",
    ),
    ("roster_max", 32, 1, 256, "E22 5.5: roster members read for the crew goal"),
)


def specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            "participation." + key,
            "integer",
            None,
            Decimal(low),
            Decimal(high),
            None,
            "team assumption: " + basis + " " + str(default),
        )
        for key, default, low, high, basis in _SETTINGS
    )


def defaults() -> dict[str, int]:
    return {"participation." + key: default for key, default, _, _, _ in _SETTINGS}


@dataclass(frozen=True, slots=True)
class Limits:
    projection_batch: int
    projection_attempts: int
    max_completion_days: int
    roster_max: int
    min_counting_members: int


def limits(settings: HubSettings) -> Limits:
    return Limits(
        settings.integer("participation.projection_batch"),
        settings.integer("participation.projection_attempts"),
        settings.integer("participation.max_completion_days"),
        settings.integer("participation.roster_max"),
        max(MIN_COUNTING_FLOOR, settings.integer("participation.min_counting_members")),
    )
