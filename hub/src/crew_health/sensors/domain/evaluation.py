"""Limit evaluation and the per channel status (CONTRACTS 13, E23 3.7)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.sensors.domain.aggregation import WindowSource, WindowStats
from crew_health.sensors.domain.channel import (
    ChannelFacts,
    Comparator,
    Freshness,
    Level,
    SensorTypeFacts,
    level_rank,
)
from crew_health.sensors.domain.errors import ConfigInconsistent, WindowScanBound
from crew_health.sensors.domain.plausibility import PlausibilityResult

Status = Literal[
    "no_reading",
    "stale",
    "beyond_limit",
    "within_limit",
    "insufficient_data",
    "threshold_pending",
    "no_limit_on_file",
]
LimitReason = Literal[
    "evaluated", "insufficient_coverage", "no_window", "scan_bound", "stale", "untrusted", "pending"
]
SCAN_BOUND_FAULT = "WINDOW_SCAN_BOUND"


@dataclass(frozen=True, slots=True)
class ClearFacts:
    comparator: Comparator
    value: Decimal | None
    dwell_seconds: int


@dataclass(frozen=True, slots=True)
class LimitFacts:
    limit_id: str
    sensor_type_id: str
    environments: tuple[str, ...]
    role: Literal["warning", "display_only"]
    level: Level
    comparator: Comparator
    value: Decimal | None
    unit: str
    aggregation_id: str
    min_coverage: Decimal
    clear: ClearFacts
    label: str
    card_mark: bool
    status: Literal["active", "threshold_pending"]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LimitResult:
    limit_id: str
    level: Level
    aggregation_id: str
    crossed: bool | None
    crossed_low: bool | None
    crossed_high: bool | None
    clear_holds: bool | None
    value: Decimal | None
    value_low: Decimal | None
    value_high: Decimal | None
    coverage: Decimal | None
    window_start: datetime | None
    window_end: datetime | None
    reason: LimitReason
    suspect_reason: str | None = (
        None  # a suspect reading is in this window: it may raise, clear_holds is None
    )


@dataclass(frozen=True, slots=True)
class Evaluation:
    status: Status
    level: Level | None
    limit_id: str | None
    aggregated_value: Decimal | None
    aggregation_label: str | None
    window_start: datetime | None
    window_end: datetime | None
    coverage: Decimal | None
    evaluated_at: datetime
    pending_limit_ids: tuple[str, ...]
    results: tuple[LimitResult, ...]
    faults: tuple[str, ...]
    conflict: bool
    unavailable_reason: str | None
    suspect_reason: str | None = None  # rate_exceeded or stuck_value: the evaluation may raise, never clears


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    sensor_type: SensorTypeFacts
    channel: ChannelFacts
    environment_id: str
    limits: tuple[LimitFacts, ...]
    windows: WindowSource
    window_end: datetime | None
    freshness: Freshness
    window_advanced: bool
    clock_trusted: bool
    evaluated_at: datetime
    suspects: tuple[
        tuple[datetime, str], ...
    ] = ()  # observation time and reason of each suspect reading kept


def crosses(comparator: Comparator, value: Decimal, threshold: Decimal) -> bool:
    if comparator == "above":
        return value > threshold
    if comparator == "at_or_above":
        return value >= threshold
    if comparator == "below":
        return value < threshold
    return value <= threshold


def limits_for(
    limits: Sequence[LimitFacts], sensor_type_id: str, environment_id: str
) -> tuple[LimitFacts, ...]:
    return tuple(
        limit
        for limit in limits
        if limit.sensor_type_id == sensor_type_id and environment_id in limit.environments
    )


def _limits_here(inp: EvaluationInput) -> tuple[LimitFacts, ...]:
    return limits_for(inp.limits, inp.sensor_type.id, inp.environment_id)


def no_reading_evaluation(
    limits: Sequence[LimitFacts], sensor_type_id: str, environment_id: str, evaluated_at: datetime
) -> Evaluation:
    """The evaluation of a sensor type that has no channel in the environment (E23 9: no_reading)."""
    pending = tuple(
        limit.limit_id
        for limit in limits_for(limits, sensor_type_id, environment_id)
        if limit.role == "warning" and limit.status == "threshold_pending"
    )
    return Evaluation(
        "no_reading", None, None, None, None, None, None, None, evaluated_at, pending, (), (), False, None
    )


def applicable_limits(inp: EvaluationInput) -> tuple[LimitFacts, ...]:
    """Only limits with role warning and status active of the active environment are evaluated."""
    return tuple(limit for limit in _limits_here(inp) if limit.role == "warning" and limit.status == "active")


def pending_limits(inp: EvaluationInput) -> tuple[LimitFacts, ...]:
    return tuple(
        limit
        for limit in _limits_here(inp)
        if limit.role == "warning" and limit.status == "threshold_pending"
    )


def _skipped(limit: LimitFacts, reason: LimitReason) -> LimitResult:
    return LimitResult(
        limit.limit_id,
        limit.level,
        limit.aggregation_id,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        reason,
    )


def _clear_holds(limit: LimitFacts, stats: WindowStats, inp: EvaluationInput) -> bool | None:
    if inp.freshness != "current" or not inp.window_advanced or not inp.clock_trusted:
        return None
    if limit.clear.value is None:
        return None
    return crosses(limit.clear.comparator, stats.low, limit.clear.value) and crosses(
        limit.clear.comparator, stats.high, limit.clear.value
    )


def _worse(limit: LimitFacts, stats: WindowStats) -> Decimal:
    return stats.high if limit.comparator in ("above", "at_or_above") else stats.low


def _evaluate_limit(limit: LimitFacts, inp: EvaluationInput, end: datetime) -> LimitResult:
    spec = inp.sensor_type.aggregation(limit.aggregation_id)
    if spec is None or limit.value is None:
        raise ConfigInconsistent("limit " + limit.limit_id)
    try:
        stats = inp.windows.stats(spec, end)
    except WindowScanBound:
        return _skipped(limit, "scan_bound")
    if stats is None:
        return _skipped(limit, "no_window")
    if spec.kind != "latest" and (stats.coverage is None or stats.coverage < limit.min_coverage):
        return _skipped(limit, "insufficient_coverage")  # the gate applies to windowed limits only
    low = crosses(limit.comparator, stats.low, limit.value)
    high = crosses(limit.comparator, stats.high, limit.value)
    return LimitResult(
        limit.limit_id,
        limit.level,
        limit.aggregation_id,
        low or high,
        low,
        high,
        _clear_holds(limit, stats, inp),
        _worse(limit, stats),
        stats.low,
        stats.high,
        stats.coverage,
        stats.window_start,
        stats.window_end,
        "evaluated",
    )


def _highest_crossed(results: Sequence[LimitResult]) -> LimitResult | None:
    """The highest crossed level; ties go to the earlier limit in document order."""
    best: LimitResult | None = None
    for result in results:
        if result.crossed and (best is None or level_rank(result.level) > level_rank(best.level)):
            best = result
    return best


def _variant_level(results: Sequence[LimitResult], *, high: bool) -> int:
    ranks = [level_rank(r.level) for r in results if (r.crossed_high if high else r.crossed_low)]
    return max(ranks, default=-1)


def pick_variant(stats: WindowStats, results: Sequence[LimitResult], marks: Sequence[LimitFacts]) -> Decimal:
    """The worse of the two variants: higher crossed level, else nearer a card mark, else higher value."""
    if stats.low == stats.high:
        return stats.high
    level_low = _variant_level(results, high=False)
    level_high = _variant_level(results, high=True)
    if level_low != level_high:
        return stats.low if level_low > level_high else stats.high
    values = [m.value for m in marks if m.value is not None]
    if values:
        near_low = min(abs(stats.low - v) for v in values)
        near_high = min(abs(stats.high - v) for v in values)
        if near_low != near_high:
            return stats.low if near_low < near_high else stats.high
    return stats.high


def _display(
    inp: EvaluationInput, end: datetime, results: Sequence[LimitResult]
) -> tuple[WindowStats | None, Decimal | None, bool]:
    spec = inp.sensor_type.aggregation(inp.sensor_type.display_aggregation_id)
    if spec is None:
        raise ConfigInconsistent("display aggregation " + inp.sensor_type.display_aggregation_id)
    try:
        stats = inp.windows.stats(spec, end)
    except WindowScanBound:
        return None, None, True
    if stats is None:
        return None, None, False
    marks = [m for m in _limits_here(inp) if m.card_mark]
    return stats, pick_variant(stats, results, marks), False


def _status(
    inp: EvaluationInput, results: Sequence[LimitResult], pending: Sequence[LimitFacts]
) -> tuple[Status, LimitResult | None]:
    crossed = _highest_crossed(results)
    if crossed is not None:
        return "beyond_limit", crossed
    if any(r.reason == "evaluated" for r in results):
        return "within_limit", None
    if applicable_limits(inp):
        return "insufficient_data", None
    return ("threshold_pending" if pending else "no_limit_on_file"), None


def _closing(
    inp: EvaluationInput,
    status: Status,
    chosen: LimitResult | None,
    results: tuple[LimitResult, ...],
    display: tuple[WindowStats | None, Decimal | None, bool],
    pending: Sequence[LimitFacts],
) -> Evaluation:
    stats, value, display_bound = display
    spec = inp.sensor_type.aggregation(inp.sensor_type.display_aggregation_id)
    faults = tuple(
        sorted(
            {SCAN_BOUND_FAULT for r in results if r.reason == "scan_bound"}
            | ({SCAN_BOUND_FAULT} if display_bound else set())
        )
    )
    conflict = any(r.value_low != r.value_high for r in results if r.reason == "evaluated") or (
        stats is not None and stats.low != stats.high
    )
    return Evaluation(
        status=status,
        level=None if chosen is None else chosen.level,
        limit_id=None if chosen is None else chosen.limit_id,
        aggregated_value=value,
        aggregation_label=None if spec is None or stats is None else spec.label,
        window_start=None if stats is None else stats.window_start,
        window_end=None if stats is None else stats.window_end,
        coverage=None if stats is None else stats.coverage,
        evaluated_at=inp.evaluated_at,
        pending_limit_ids=tuple(p.limit_id for p in pending),
        results=results,
        faults=faults,
        conflict=conflict,
        unavailable_reason=None,
    )


def _empty(inp: EvaluationInput, status: Status, reason: LimitReason) -> Evaluation:
    pending = pending_limits(inp)
    results = tuple(_skipped(limit, reason) for limit in applicable_limits(inp))
    return Evaluation(
        status,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        inp.evaluated_at,
        tuple(p.limit_id for p in pending),
        results,
        (),
        False,
        None,
    )


def evaluate_limits(inp: EvaluationInput) -> Evaluation:
    """Evaluate every active warning limit of the environment on its own aggregation (3.7)."""
    end = inp.window_end
    if end is None:
        return _empty(inp, "no_reading", "no_window")
    pending = pending_limits(inp)
    if inp.freshness != "current":
        skipped = tuple(_skipped(limit, "stale") for limit in applicable_limits(inp))
        return _closing(inp, "stale", None, skipped, _display(inp, end, ()), pending)
    results = tuple(_evaluate_limit(limit, inp, end) for limit in applicable_limits(inp))
    status, chosen = _status(inp, results, pending)
    return _closing(inp, status, chosen, results, _display(inp, end, results), pending)


def evaluate_channel(inp: EvaluationInput, plausibility: PlausibilityResult) -> Evaluation:
    """Gate on plausibility (R-E23-2). An impossible or unproved reading evaluates nothing. A suspect one (rate or
    stuck) evaluates for raising only: every limit result loses its clear proof, so nothing clears or lowers."""
    if not plausibility.raises:
        empty = _empty(inp, "insufficient_data", "untrusted")
        return replace(empty, unavailable_reason=plausibility.reason)
    evaluation = evaluate_limits(inp)
    marks = tuple(sorted(inp.suspects))
    results: list[LimitResult] = []
    now = plausibility.reason if plausibility.suspect else None
    for result in evaluation.results:
        found = now if now is not None else _suspect_in(result, marks)
        results.append(result if found is None else replace(result, clear_holds=None, suspect_reason=found))
    reasons = [r.suspect_reason for r in results if r.suspect_reason is not None]
    return replace(
        evaluation, results=tuple(results), suspect_reason=now or (reasons[-1] if reasons else None)
    )


def _suspect_in(result: LimitResult, marks: Sequence[tuple[datetime, str]]) -> str | None:
    """The reason of the newest suspect reading inside this result's window (start, end], else None. A suspect
    reading stays in the evidence of every window that holds it, so none of them can prove a clear."""
    end = result.window_end
    if end is None:
        return None
    start = result.window_start
    inside = [m for m in marks if m[0] <= end and (m[0] > start if start is not None else m[0] == end)]
    return inside[-1][1] if inside else None
