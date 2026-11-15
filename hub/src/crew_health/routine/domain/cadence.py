from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import ConfigInvalid, Finding
from crew_health.routine.domain.release import Cadence, Calendar


def period_days(c: Cadence, cal: Calendar) -> int | None:
    if c.word in ("continuous", "daily"):
        return 1
    if c.word == "every_n_days":
        if c.n is None or c.n < 1:
            raise ConfigInvalid((Finding("error", "/cadence/n", "Expected a positive period"),))
        return c.n
    if c.word == "weekly":
        return cal.week_days
    if c.word == "monthly":
        return cal.month_days
    return None


def next_periodic_day(
    *, period: int, anchor_day: int, last_final_day: int | None, today: int, today_has_periodic: bool
) -> int:
    candidate = (
        last_final_day + period if last_final_day is not None and last_final_day >= anchor_day else anchor_day
    )
    return max(candidate, today + 1 if today_has_periodic else today)


@dataclass(frozen=True, slots=True)
class CadenceCandidate:
    override_id: str
    cadence: Cadence
    started_at: datetime
    basis: str
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_utc(self.started_at)


@dataclass(frozen=True, slots=True)
class EffectiveCadence:
    cadence: Cadence
    governing_override_id: str | None
    basis: str
    source_ids: tuple[str, ...]


def effective_cadence(
    base: Cadence,
    base_basis: str,
    base_source_ids: tuple[str, ...],
    candidates: Sequence[CadenceCandidate],
    cal: Calendar,
) -> EffectiveCadence:
    base_period = period_days(base, cal)
    eligible: list[tuple[int, CadenceCandidate]] = []
    for candidate in candidates:
        period = period_days(candidate.cadence, cal)
        if period is not None and (base_period is None or period <= base_period):
            eligible.append((period, candidate))
    if not eligible:
        return EffectiveCadence(base, None, base_basis, base_source_ids)
    shortest = min(p for p, _ in eligible)
    winner = max((c for p, c in eligible if p == shortest), key=lambda c: (c.started_at, c.override_id))
    return EffectiveCadence(winner.cadence, winner.override_id, winner.basis, winner.source_ids)
