"""The bus payloads E24 consumes (E23 4.3, R-E24-14): built from stored state, published in the unit of work that
changed it. Facts only; nothing here decides a warning."""

from __future__ import annotations

from datetime import timedelta

from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.application.ports import (
    ChannelFreshnessChanged,
    LimitOutcome,
    SensorEvaluated,
)
from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.application.store import ChannelState, ReadingRecord
from crew_health.sensors.domain.card import format_value
from crew_health.sensors.domain.channel import SensorTypeFacts
from crew_health.sensors.domain.config import SensorConfig
from crew_health.sensors.domain.evaluation import Evaluation


def _outcomes(
    config: SensorConfig, kind: SensorTypeFacts, evaluation: Evaluation
) -> tuple[LimitOutcome, ...]:
    limits = {limit.limit_id: limit for limit in config.limits}
    unit = config.canonical_unit(kind.id)
    found: list[LimitOutcome] = []
    for r in evaluation.results:
        limit = limits.get(r.limit_id)
        spec = next((a for a in kind.aggregations if a.id == r.aggregation_id), None)
        if limit is None or spec is None:
            continue
        found.append(
            LimitOutcome(
                r.limit_id, r.crossed, r.clear_holds, r.value, unit, r.coverage, spec.id, spec.label,
                spec.window_seconds,
                "" if r.value is None else format_value(r.value, kind.decimals, kind.trim_trailing_zeros),
                kind.unit_display,
                "" if limit.value is None else format_value(limit.value, kind.decimals, kind.trim_trailing_zeros),
            )
        )  # fmt: skip
    return tuple(found)


def _reading_at(rt: Runtime, uow: UnitOfWork, channel: ChannelState) -> ReadingRecord | None:
    end = channel.window_end
    if end is None:
        return None
    rows = rt.store.readings_since(
        uow,
        channel.facts.channel_id,
        since=end - timedelta(seconds=1),
        up_to_rev=None,
        limit=rt.limits.window_scan_max_readings,
    )
    at_end = [r for r in rows if r.corrected_at == end or r.observed_at == end]
    return (at_end or list(rows) or [None])[-1]  # type: ignore[list-item]


def sensor_evaluated(
    rt: Runtime, uow: UnitOfWork, config: SensorConfig, kind: SensorTypeFacts, channel: ChannelState,
    evaluation: Evaluation, release_id: str,
) -> SensorEvaluated | None:  # fmt: skip
    """None when no accepted reading carries the evaluation: nothing is published for a reading that is not there."""
    reading = _reading_at(rt, uow, channel)
    end = channel.window_end
    if reading is None or end is None:
        return None
    reference = channel.reference
    clock = channel.clock_state
    f = channel.facts
    return SensorEvaluated(
        release_id, f.sensor_type_id, f.channel_id, channel.location, f.origin_mode, reading.reading_id, end,
        reading.observed_at, reading.corrected_at,
        reading.uncertainty_ms if reference is None else reference.uncertainty_ms,
        reading.time_state, reading.adapter_id, reading.segment_id or "", reading.source_clock_version or 0,
        channel.reference_at if f.origin_mode == "REPLAY" else None,
        "trusted" if clock == "trusted" else ("uncertain" if clock == "uncertain" else "unknown"),
        "stale" if channel.freshness == "stale" else "current", f.stale_after_seconds,
        _outcomes(config, kind, evaluation), kind.name, f.location_label, f.source_label,
        None if channel.last_received_at is None else f"{channel.last_received_at:%H:%M}", f.dataset,
    )  # fmt: skip


def freshness_changed(
    rt: Runtime, uow: UnitOfWork, config: SensorConfig, channel: ChannelState
) -> ChannelFreshnessChanged | None:
    kind = config.sensor_type(channel.facts.sensor_type_id)
    if kind is None:
        return None
    f = channel.facts
    reading = _reading_at(rt, uow, channel)
    return ChannelFreshnessChanged(
        uow.release.release_id, f.channel_id, f.sensor_type_id, channel.freshness, channel.stale_since,
        None if reading is None else reading.reading_id, f.origin_mode, kind.name, f.location_label,
        f.source_label, f.dataset,
        None if reading is None else reading.observed_at, None if reading is None else reading.corrected_at,
        None if reading is None else reading.uncertainty_ms, None if reading is None else reading.time_state,
        None if reading is None else reading.adapter_id, None if reading is None else reading.segment_id,
        None if reading is None else reading.source_clock_version,
        channel.reference_at if f.origin_mode == "REPLAY" else None,
    )  # fmt: skip
