"""Day labels from the profile's day_label (CONTRACTS 12; E23 4.2 DayLabelPort)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.mission.domain.errors import InvalidValue

Counter = Literal["mission_day", "environment_day"]


@dataclass(frozen=True, slots=True)
class DayLabelFacts:
    counter: Counter
    template: str
    short_template: str
    inline_template: str
    day_length_seconds: int


@dataclass(frozen=True, slots=True)
class ProfilePeriod:
    """One profile as it was active: the history the inline label reads."""

    environment_id: str
    activated_at: datetime
    superseded_at: datetime | None
    environment_day_start: datetime
    day_label: DayLabelFacts


def environment_day(at: datetime, start: datetime, day_length_seconds: int) -> int | None:
    """floor((t - start) / day length) + 1. None before the profile became active."""
    if day_length_seconds < 1:
        raise InvalidValue("day length")
    if at < start:
        return None
    return int((at - start).total_seconds()) // day_length_seconds + 1


def render(template: str, *, day: int, total: int | None, unknown: str) -> str:
    """Fill {day} and {total}. A missing total is the configured unknown word, never an invented number."""
    shown_total = unknown if total is None else str(total)
    try:
        return template.format_map({"day": str(day), "total": shown_total})
    except (KeyError, IndexError, ValueError) as error:
        raise InvalidValue("day template placeholder") from error


def period_at(history: Sequence[ProfilePeriod], instant: datetime) -> ProfilePeriod | None:
    """The profile that was active at the instant (activated_at <= instant < superseded_at)."""
    for period in sorted(history, key=lambda p: p.activated_at, reverse=True):
        if period.activated_at <= instant and (
            period.superseded_at is None or instant < period.superseded_at
        ):
            return period
    return None


def inline_label_at(
    history: Sequence[ProfilePeriod],
    instant: datetime,
    *,
    mission_day_at: Callable[[datetime], int],
    total: int | None,
    unknown: str,
) -> str | None:
    """The inline label of the profile active at the instant, so a transit era keeps its transit words."""
    period = period_at(history, instant)
    if period is None:
        return None
    label = period.day_label
    if label.counter == "mission_day":
        number: int | None = mission_day_at(instant)
    else:
        number = environment_day(instant, period.environment_day_start, label.day_length_seconds)
    if number is None:
        return None
    return render(label.inline_template, day=number, total=total, unknown=unknown)
