"""SkillRecord freshness (E31 design 3.2, CONTRACTS 24): a pure function of counted practices, catalog and now."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.roles.domain.rules import Calendar, Freshness

type PracticeSource = Literal["preflight", "drill", "event"]
type FreshnessState = Literal["fresh", "due", "stale", "never"]
type PracticeOrigin = Literal["LIVE", "SIM"]

DAY_SECONDS = 86_400  # CONTRACTS 24: daily is one day; the calendar sizes weeks and months.
_SOURCE_RANK: dict[str, int] = {"preflight": 0, "event": 1, "drill": 2}


@dataclass(frozen=True, slots=True)
class Practice:
    practice_id: str
    person_id: str
    skill_id: str
    source: PracticeSource
    source_ref_id: str
    practiced_at: datetime
    origin_mode: PracticeOrigin
    mission_id: str


@dataclass(frozen=True, slots=True)
class SkillRecord:
    person_id: str
    skill_id: str
    freshness: FreshnessState
    last: Practice | None
    trained_at: datetime | None
    due_at: datetime | None
    stale_at: datetime | None

    @property
    def is_holder(self) -> bool:
        """CONTRACTS 24 and SYSTEM 8.2: a holder's record is fresh or due; stale and never hold nothing."""
        return self.freshness in ("fresh", "due")


def period(f: Freshness, calendar: Calendar) -> timedelta:
    if f.cadence == "daily":
        days = 1
    elif f.cadence == "weekly":
        days = calendar.week_days
    elif f.cadence == "monthly":
        days = calendar.month_days
    elif f.cadence == "every_n_days" and f.n is not None:
        days = f.n
    else:
        raise ValueError("cadence rejected by loader check R-L3")
    return timedelta(seconds=days * DAY_SECONDS)


def _order(p: Practice) -> tuple[datetime, int, str]:
    return (p.practiced_at, _SOURCE_RANK[p.source], p.source_ref_id)


def skill_record(
    person_id: str,
    skill_id: str,
    practices: Iterable[Practice],
    f: Freshness,
    calendar: Calendar,
    now: datetime,
) -> SkillRecord:
    """`practices` are already one person, skill, mission and origin; only those at or before now count."""
    counted = [
        p for p in practices if p.practiced_at <= now and p.person_id == person_id and p.skill_id == skill_id
    ]
    if not counted:
        return SkillRecord(person_id, skill_id, "never", None, None, None, None)
    last = max(counted, key=_order)
    preflight = [p.practiced_at for p in counted if p.source == "preflight"]
    step = period(f, calendar)
    due_at = last.practiced_at + step * f.due_after_periods
    stale_at = last.practiced_at + step * f.stale_after_periods
    state: FreshnessState = "fresh" if now < due_at else ("due" if now < stale_at else "stale")
    return SkillRecord(
        person_id, skill_id, state, last, max(preflight) if preflight else None, due_at, stale_at
    )
