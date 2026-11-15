from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec


def specs() -> tuple[SettingSpec, ...]:
    bounds = (
        ("page_limit", 1, 500, "bounded query pages"),
        ("max_open_per_person", 1, 2000, "bounded open work"),
        ("max_active_overrides_per_person", 1, 500, "bounded temporary changes"),
        ("max_assistant_rechecks_per_person_day", 1, 20, "bounded assistant prompts"),
        ("max_overlay_intervals", 1, 1000, "bounded hold computation"),
        ("miss_grace_ms", 1000, 3600000, "late deadlines count as unseen"),
        ("release_cache_entries", 1, 64, "bounded parsed releases"),
        ("max_items", 1, 500, "bounded materialization"),
        ("max_bindings", 1, 500, "bounded input fanout"),
        ("max_slots_per_binding", 1, 64, "bounded trigger slots"),
        ("max_roster", 1, 64, "bounded audience fanout"),
        ("history_days", 1, 400, "bounded miss and week history"),
    )
    return tuple(
        SettingSpec(
            "routine." + k,
            "integer",
            None,
            Decimal(lo),
            Decimal(hi),
            None,
            "team default: " + why + ", E25 5.4",
        )
        for k, lo, hi, why in bounds
    )


@dataclass(frozen=True, slots=True)
class RoutineSettings:
    page_limit: int
    max_open_per_person: int
    max_active_overrides_per_person: int
    max_assistant_rechecks_per_person_day: int
    max_overlay_intervals: int
    miss_grace_ms: int
    release_cache_entries: int
    max_items: int
    max_bindings: int
    max_slots_per_binding: int
    max_roster: int
    history_days: int

    @classmethod
    def read(cls, settings: HubSettings) -> RoutineSettings:
        return cls(*(settings.integer(spec.key) for spec in specs()))
