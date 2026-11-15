"""Qualify daily observations without changing, sorting or filling them."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, TypedDict


class SeriesPolicy(Protocol):
    @property
    def min_context_days(self) -> int: ...

    @property
    def max_context_days(self) -> int: ...

    @property
    def max_series_points(self) -> int: ...


class RawPoint(TypedDict):
    mission_day: int
    value: object


@dataclass(frozen=True)
class DailyPoint:
    mission_day: int
    value: Decimal


@dataclass(frozen=True)
class QualifiedSeries:
    points: tuple[DailyPoint, ...]
    unit: str

    @property
    def context_days(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class Unqualified:
    reason: str


def parse_value(value: object) -> Decimal | Unqualified:
    if not isinstance(value, str):
        return Unqualified("malformed_value")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return Unqualified("malformed_value")
    if not parsed.is_finite():
        return Unqualified("nonfinite_value")
    if re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", value) is None:
        return Unqualified("malformed_value")
    return parsed


def parse_series(
    raw_points: Sequence[RawPoint], unit: str | None, policy: SeriesPolicy
) -> QualifiedSeries | Unqualified:
    if not unit or not unit.strip():
        return Unqualified("unit_missing")
    if not raw_points:
        return Unqualified("empty")
    if len(raw_points) > policy.max_series_points:
        return Unqualified("too_many_points")
    points: list[DailyPoint] = []
    for raw in raw_points:
        value = parse_value(raw["value"])
        if isinstance(value, Unqualified):
            return value
        day = raw["mission_day"]
        if points and day <= points[-1].mission_day:
            return Unqualified("days_not_increasing")
        if points and day != points[-1].mission_day + 1:
            return Unqualified("history_not_contiguous")
        points.append(DailyPoint(day, value))
    if len(points) < policy.min_context_days:
        return Unqualified("history_too_short")
    return QualifiedSeries(tuple(points[-policy.max_context_days :]), unit)
