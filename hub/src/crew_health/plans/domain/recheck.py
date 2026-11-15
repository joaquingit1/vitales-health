"""Recheck calculations use injected UTC instants and calendar settings."""

from datetime import datetime, timedelta
from typing import Literal

from crew_health.plans.domain.model import RecheckSpec

RecheckWords = Literal["pending", "at", "cadence"]


def due_at(spec: RecheckSpec, last_done: datetime | None, *, month_days: int | None) -> datetime | None:
    if last_done is None:
        return None
    if spec.interval_seconds is not None:
        return last_done + timedelta(seconds=spec.interval_seconds)
    days = {"daily": 1, "weekly": 7, "every_n_days": spec.n, "monthly": month_days}.get(spec.cadence or "")
    return None if days is None else last_done + timedelta(days=days)


def due_words(spec: RecheckSpec, due: datetime | None) -> tuple[RecheckWords, str | None]:
    if spec.to_be_set_by is not None and spec.cadence is None and spec.interval_seconds is None:
        return "pending", spec.to_be_set_by
    if due is not None:
        return "at", None
    return "cadence", spec.cadence


def recheck_shape_ok(spec: RecheckSpec) -> bool:
    if spec.cadence is not None and spec.interval_seconds is not None:
        return False
    if spec.cadence is None and spec.interval_seconds is None:
        return spec.to_be_set_by is not None
    return spec.cadence != "every_n_days" or (spec.n is not None and spec.n >= 1)
