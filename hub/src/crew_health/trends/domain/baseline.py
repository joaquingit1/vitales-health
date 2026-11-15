"""Deterministic observation selection, baseline aggregation and comparison."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from crew_health.kernel.units import Quantity
from crew_health.trends.domain.errors import TrendsInvariant
from crew_health.trends.domain.model import (
    BaselinePolicy,
    BaselineSummary,
    Comparison,
    Exclusion,
    ExclusionReason,
    InsufficientBaseline,
    Partition,
    Period,
    Sample,
    Selection,
    SummaryState,
)


def order(sample: Sample) -> tuple[datetime, str]:
    return sample.observed_at or datetime.min.replace(tzinfo=UTC), sample.observation_id


def _lineage(sample: Sample, by_id: dict[str, Sample]) -> tuple[str, ...]:
    result: list[str] = []
    previous = sample.supersedes
    for _ in range(len(by_id)):
        if previous is None or previous == sample.observation_id or previous in result:
            break
        result.append(previous)
        ancestor = by_id.get(previous)
        previous = ancestor.supersedes if ancestor is not None else None
    return tuple(result)


def effective(samples: tuple[Sample, ...]) -> Selection:
    """Resolve bounded correction chains without recursion or input-order effects."""
    ordered = tuple(sorted(samples, key=order))
    by_id = {sample.observation_id: sample for sample in ordered}
    removed = {sample.supersedes for sample in ordered if sample.supersedes is not None}
    exclusions = tuple(
        Exclusion(sample.observation_id, "superseded", sample.observed_at)
        for sample in ordered
        if sample.observation_id in removed
    )
    retained = tuple(
        replace(sample, superseded_ids=_lineage(sample, by_id))
        for sample in ordered
        if sample.observation_id not in removed
    )
    return Selection(retained, exclusions)


def _same_partition(sample: Sample, partition: Partition) -> bool:
    return (sample.patient_id, sample.mission_id, sample.origin_mode) == (
        partition.person_id,
        partition.mission_id,
        partition.origin_mode,
    )


def _unusable(sample: Sample, now: datetime) -> ExclusionReason | None:
    if sample.quality_status != "accepted":
        return "not_accepted"
    if sample.time_state != "known" and not (
        sample.time_state == "replay" and sample.origin_mode == "REPLAY"
    ):
        return "time_unknown"
    if sample.observed_at is None or sample.observed_at > now:
        return "future_time"
    return None


def select(samples: tuple[Sample, ...], partition: Partition, now: datetime) -> Selection:
    """Partition first so a foreign correction cannot hide the owner's reading."""
    ordered = tuple(sorted(samples, key=order))
    partitioned = tuple(sample for sample in ordered if _same_partition(sample, partition))
    selected = effective(partitioned)
    exclusions = list(selected.exclusions)
    exclusions.extend(
        Exclusion(sample.observation_id, "other_partition", sample.observed_at)
        for sample in ordered
        if not _same_partition(sample, partition)
    )
    usable: list[Sample] = []
    for sample in selected.samples:
        reason = _unusable(sample, now)
        if reason is None:
            usable.append(sample)
        else:
            exclusions.append(Exclusion(sample.observation_id, reason, sample.observed_at))
    positions = {sample.observation_id: index for index, sample in enumerate(ordered)}
    return Selection(
        tuple(usable), tuple(sorted(exclusions, key=lambda item: positions[item.observation_id]))
    )


def readings(selection: Selection, mission_day: int) -> tuple[Sample | None, Sample | None]:
    ordered = tuple(sorted(selection.samples, key=order))
    current = tuple(sample for sample in ordered if sample.mission_day == mission_day)
    return (current[-1] if current else None, ordered[-1] if ordered else None)


def _cohort(sample: Sample) -> tuple[str, str | None, str, tuple[tuple[str, str], ...]]:
    return sample.acquisition_method, sample.device_id, sample.operator_id, sample.context


def _baseline_exclusion(
    sample: Sample, current: Sample, policy: BaselinePolicy, periods: tuple[Period, ...]
) -> ExclusionReason | None:
    if sample.observation_id == current.observation_id:
        return "current_reading"
    if sample.observed_at is None or current.observed_at is None:
        raise TrendsInvariant("baseline_time_missing")
    start = current.observed_at - timedelta(days=policy.window_days)
    if not start <= sample.observed_at < current.observed_at:
        return "outside_window"
    return _incomparable(sample, current, periods)


def _incomparable(sample: Sample, reference: Sample, periods: tuple[Period, ...]) -> ExclusionReason | None:
    """The comparability rules shared by the usual value and the forecast input."""
    if sample.observed_at is None:
        raise TrendsInvariant("comparison_time_missing")
    if sample.quantity.unit != reference.quantity.unit:
        return "unit_mismatch"
    if _cohort(sample) != _cohort(reference):
        return "cohort_mismatch"
    return _episode(sample, periods)


def _aggregate(samples: tuple[Sample, ...], policy: BaselinePolicy) -> Decimal:
    values = sorted(sample.quantity.value for sample in samples)
    if policy.method == "mean":
        return sum(values, Decimal(0)) / Decimal(len(values))
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal(2)


def baseline(
    selection: Selection,
    current: Sample,
    policy: BaselinePolicy,
    periods: tuple[Period, ...],
    *,
    exclusion_policy: str,
) -> BaselineSummary | InsufficientBaseline:
    if policy.min_samples < 1 or policy.window_days < 1 or policy.method not in ("mean", "median"):
        raise TrendsInvariant("baseline_policy_invalid")
    included: list[Sample] = []
    exclusions = list(selection.exclusions)
    for sample in sorted(selection.samples, key=order):
        reason = _baseline_exclusion(sample, current, policy, periods)
        if reason is None:
            included.append(sample)
        else:
            exclusions.append(Exclusion(sample.observation_id, reason, sample.observed_at))
    exclusions.sort(
        key=lambda item: (item.observed_at or datetime.min.replace(tzinfo=UTC), item.observation_id)
    )
    ids = tuple(sample.observation_id for sample in included)
    if len(included) < policy.min_samples:
        return InsufficientBaseline(
            len(included),
            policy.min_samples,
            policy.window_days,
            ids,
            tuple(exclusions),
            "exact_context.v1",
            exclusion_policy,
            policy.catalog_version,
        )
    value = Quantity(_aggregate(tuple(included), policy), current.quantity.unit)
    return BaselineSummary(
        value,
        policy.method,
        policy.window_days,
        policy.min_samples,
        len(included),
        ids,
        tuple(exclusions),
        "exact_context.v1",
        exclusion_policy,
        policy.catalog_version,
        policy.basis,
        policy.comparison,
    )


def comparable(selection: Selection, periods: tuple[Period, ...]) -> Selection:
    """Readings comparable with the newest unlinked reading, under the same rules as the usual value.

    The reference is the newest reading outside any episode, so a forecast input never mixes contexts, units or
    episode readings that the usual value keeps apart (PLAN 25 and 27).
    """
    ordered = tuple(sorted(selection.samples, key=order))
    unlinked = tuple(sample for sample in ordered if _episode(sample, periods) is None)
    reference = unlinked[-1] if unlinked else None
    kept: list[Sample] = []
    exclusions = list(selection.exclusions)
    for sample in ordered:
        reason = _episode(sample, periods) if reference is None else _incomparable(sample, reference, periods)
        if reason is None:
            kept.append(sample)
        else:
            exclusions.append(Exclusion(sample.observation_id, reason, sample.observed_at))
    return Selection(tuple(kept), tuple(exclusions))


def _episode(sample: Sample, periods: tuple[Period, ...]) -> ExclusionReason | None:
    if sample.episode_id is not None:
        return "episode_linked"
    at = sample.observed_at
    if at is not None and any(p.start <= at and (p.end is None or at < p.end) for p in periods):
        return "episode_period"
    return None


def validate_comparison(comparison: Comparison, decimals: int) -> None:
    """A band is nonnegative, no finer than the display precision, and cited when it claims a source."""
    if decimals < 0:
        raise TrendsInvariant("comparison_input_invalid")
    quantum = Decimal(1).scaleb(-decimals)
    band = comparison.band
    if not band.is_finite() or band < 0 or band != band.quantize(quantum, rounding=ROUND_HALF_EVEN):
        raise TrendsInvariant("comparison_band_invalid")
    if comparison.basis == "sourced" and not comparison.source_ids:
        raise TrendsInvariant("comparison_band_invalid")


def compare(today: Quantity, usual: Quantity, decimals: int, comparison: Comparison) -> SummaryState:
    if today.unit != usual.unit:
        raise TrendsInvariant("comparison_input_invalid")
    validate_comparison(comparison, decimals)
    quantum = Decimal(1).scaleb(-decimals)
    measured = today.value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    baseline_value = usual.value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    if abs(measured - baseline_value) <= comparison.band:
        return "within_usual"
    return "below_usual" if measured < baseline_value else "above_usual"
