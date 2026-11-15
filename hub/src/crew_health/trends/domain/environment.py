"""Habitat series remain place context, with original dates and source modes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.units import Quantity
from crew_health.trends.domain.model import OriginMode, QualityStatus, Unavailable


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    observed_at: datetime
    value: Quantity
    quality_status: QualityStatus
    origin_mode: OriginMode


@dataclass(frozen=True, slots=True)
class EnvironmentSeries:
    channel_id: str
    origin_mode: OriginMode | Literal["mixed"]
    points: tuple[SeriesPoint, ...]
    personal_exposure: Literal[False]
    place_context_only: Literal[True]
    original_dates_kept: Literal[True]
    pressure_basis: Unavailable


def build_series(
    channel_id: str, points: tuple[SeriesPoint, ...], *, truncated: bool
) -> EnvironmentSeries | Unavailable:
    if truncated:
        return Unavailable("trends", "series_truncated")
    accepted = tuple(
        sorted(
            (point for point in points if point.quality_status == "accepted"),
            key=lambda point: point.observed_at,
        )
    )
    origins = {point.origin_mode for point in accepted}
    origin: OriginMode | Literal["mixed"] = next(iter(origins)) if len(origins) == 1 else "mixed"
    return EnvironmentSeries(
        channel_id,
        origin,
        accepted,
        False,
        True,
        True,
        Unavailable("E23", "pressure_basis_not_provided"),
    )
