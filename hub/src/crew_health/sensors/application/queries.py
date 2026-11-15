"""Sensor reads (E23 6, UC-Q): channel, reading, status and series, composed on read from stored evaluations and
freshness recomputed against the S5.2 normalized time. Nothing here writes; a stale or unknown time never reads as
current, and the stored `stale` of a channel only a new eligible reading can lift (D3, E23 3.4)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.sensor_channel_schema import SensorChannel
from crew_health.contracts_gen.schemas.sensor_reading_schema import SensorReading
from crew_health.contracts_gen.schemas.sensor_series_schema import SensorSeries
from crew_health.contracts_gen.schemas.sensor_status_schema import SensorStatus
from crew_health.kernel.errors import ProblemError
from crew_health.sensors.adapters.config_reader import SensorConfig
from crew_health.sensors.application.evaluate import stored_evaluation
from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.application.store import ChannelState, EnvironmentRow, ReadingRecord, View
from crew_health.sensors.domain.aggregation import DirectWindows
from crew_health.sensors.domain.card import CardInput, ChannelView, compose_card
from crew_health.sensors.domain.evaluation import Evaluation, EvaluationInput, evaluate_limits, limits_for, no_reading_evaluation
from crew_health.sensors.domain.freshness import FreshnessDecision, TimeFacts, decide_freshness
from crew_health.sensors.domain.reading import round_wire


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def opt(value: datetime | None) -> str | None:
    return None if value is None else iso(value)


def number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def decide_now(
    view: View, channel: ChannelState, environment: EnvironmentRow | None, *, sticky: bool = True
) -> FreshnessDecision:
    """Freshness at this view's `now`. A previously stale channel stays stale until eligible new data arrives."""
    reference = channel.reference
    anchor: TimeFacts | None = None
    if environment is not None:
        if reference is not None and reference.corrected_at is not None and reference.corrected_at < environment.activated_at:
            reference = None  # a reading before activation is never current in the new place (DE23-13)
        if channel.quiet_anchor_at is not None and channel.quiet_anchor_environment == environment.environment_id:
            anchor = TimeFacts("known", channel.quiet_anchor_at, 0)
    decision = decide_freshness(
        now=view.now,
        stale_after_seconds=channel.facts.stale_after_seconds,
        has_history=channel.has_history,
        reference=reference,
        quiet_anchor=anchor,
    )
    if sticky and channel.freshness == "stale" and decision.state == "current":
        return FreshnessDecision("stale", decision.clock_state, channel.stale_since, None, "stale_until_new_data")
    return decision


def channel_model(view: View, channel: ChannelState, environment: EnvironmentRow | None) -> SensorChannel:
    decision = decide_now(view, channel, environment)
    f = channel.facts
    return SensorChannel.model_validate(
        {
            "channel_id": f.channel_id,
            "sensor_type_id": f.sensor_type_id,
            "hazard_id": f.hazard_id,
            "environment_ids": list(f.environment_ids),
            "location": channel.location,
            "location_label": f.location_label,
            "adapter_id": f.adapter_id,
            "origin_mode": f.origin_mode,
            "input_unit": channel.input_unit,
            "expected_interval_seconds": f.expected_interval_seconds,
            "stale_after_seconds": f.stale_after_seconds,
            "last_received_at": opt(channel.last_received_at),
            "last_observed_at": opt(channel.last_observed_at),
            "freshness": decision.state,
            "source_label": f.source_label,
            "dataset": f.dataset,
            "display_note": f.display_note,
            "trusted": channel.trusted,
        }
    )


def reading_model(record: ReadingRecord, channel: ChannelState, config: SensorConfig) -> SensorReading:
    kind = config.sensor_type(channel.facts.sensor_type_id)
    if kind is None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "sensor type not in the release", retryable=True)
    conversion: Any = None if record.conversion_json is None else json.loads(record.conversion_json)
    replay: Any = None if record.replay_json is None else json.loads(record.replay_json)
    return SensorReading.model_validate(
        {
            "reading_id": record.reading_id,
            "channel_id": record.channel_id,
            "sensor_type_id": kind.id,
            "hazard_id": kind.hazard_id,
            "value": float(record.value),
            "unit": record.unit,
            "original_value": float(record.original_value),
            "original_unit": record.original_unit,
            "conversion": conversion,
            "aggregation": {"kind": record.aggregation_kind, "window_seconds": record.aggregation_window},
            "observed_at": iso(record.observed_at),
            "window_start": opt(record.window_start),
            "received_at": iso(record.received_at),
            "origin_mode": record.origin_mode,
            "adapter_id": record.adapter_id,
            "adapter_sequence": record.adapter_sequence,
            "quality_status": record.quality_status,
            "location": channel.location,
            "replay": replay,
            "provenance": {
                "origin_mode": record.origin_mode,
                "source_label": channel.facts.source_label,
                **({} if record.provenance_trace is None else {"trace": record.provenance_trace}),
                **({} if channel.facts.dataset is None else {"dataset": channel.facts.dataset}),
            },
        }
    )


def evaluation_wire(evaluation: Evaluation, unit: str) -> dict[str, Any]:
    return {
        "status": evaluation.status,
        "level": evaluation.level,
        "limit_id": evaluation.limit_id,
        "aggregated_value": number(evaluation.aggregated_value),
        "unit": unit,
        "aggregation_label": evaluation.aggregation_label,
        "window_start": opt(evaluation.window_start),
        "window_end": opt(evaluation.window_end),
        "coverage": number(evaluation.coverage),
        "evaluated_at": iso(evaluation.evaluated_at),
        "pending_limit_ids": list(evaluation.pending_limit_ids),
    }


def _channel_view(
    rt: Runtime, view: View, config: SensorConfig, channel: ChannelState, env: EnvironmentRow, release_id: str
) -> ChannelView:
    kind = config.sensor_type(channel.facts.sensor_type_id)
    if kind is None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "sensor type not in the release", retryable=True)
    decision = decide_now(view, channel, env)
    limits = limits_for(config.limits, kind.id, env.environment_id)
    stored = stored_evaluation(rt, view, channel, env.profile_id, release_id)
    if stored is None or decision.state != "current":
        shell = EvaluationInput(
            kind, channel.facts, env.environment_id, limits,
            DirectWindows((), channel.facts.expected_interval_seconds, rt.limits.window_scan_max_readings),
            channel.window_end if stored is not None else None,
            decision.state, False, decision.clock_state == "trusted", view.now,
        )
        evaluation = evaluate_limits(shell) if stored is not None else no_reading_evaluation(limits, kind.id, env.environment_id, view.now)
    else:
        evaluation = stored
    trace = _trace(rt, view, channel)
    return ChannelView(channel.facts, evaluation, decision.state, decision.stale_since, trace)


def _trace(rt: Runtime, view: View, channel: ChannelState) -> str | None:
    if channel.facts.origin_mode != "REPLAY":
        return None
    return f"Replay of {channel.facts.dataset}, original dates kept." if channel.facts.dataset else None


def status_model(rt: Runtime, view: View, sensor_type_id: str, *, release_id: str | None = None) -> SensorStatus:
    env = rt.store.environment(view)
    release = view.release
    config = rt.config_of(release)
    kind = config.sensor_type(sensor_type_id)
    if kind is None:
        raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
    if env is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_initialized", retryable=True)
    channels = [c for c in rt.store.channels(view) if c.facts.sensor_type_id == kind.id and env.environment_id in c.facts.environment_ids]
    views = tuple(_channel_view(rt, view, config, c, env, release_id or release.release_id) for c in channels)
    hazard = config.hazard(kind.hazard_id)
    if hazard is None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "hazard not in the release", retryable=True)
    unsupported = _unsupported(release)
    card = compose_card(
        CardInput(
            kind, env.environment_id, views, config.limits, hazard, config.effect_filters, (), unsupported,
            env.mission_mode, "No channel", view.now,
        )
    )
    latest = None if card.reading_state == "no_reading" else _latest(rt, view, card.channel_id, channels, config, env)
    ev = card.evaluation
    return SensorStatus.model_validate(
        {
            "sensor_type_id": kind.id,
            "hazard_id": kind.hazard_id,
            "environment_id": env.environment_id,
            "name": card.name,
            "long_name": card.long_name,
            "body_region": card.body_region,
            "channel_id": card.channel_id,
            "reading_state": card.reading_state,
            "latest_reading": None if latest is None else latest.model_dump(mode="json", exclude_unset=True),
            "display_value": {"value": number(card.display_value.value), "unit": card.display_value.unit, "text": card.display_value.text},
            "context_label": card.context_label,
            "source_label": card.source_label,
            "origin_mode": card.origin_mode,
            "evaluation": evaluation_wire(ev, kind.unit),
            "shown_level": card.shown_level,
            "linked_warning_ids": list(card.linked_warning_ids),
            "display_range": [float(card.display_range[0]), float(card.display_range[1])],
            "limit_marks": [
                {"limit_id": m.limit_id, "value": float(m.value), "unit": m.unit, "comparator": m.comparator, "role": m.role, "label": m.label}
                for m in card.limit_marks
            ],
            "stale_since": opt(card.stale_since),
            "trace": card.trace,
            "effects": [{"text": e.text, "body_region": e.body_region} for e in card.effects],
            "todo": list(card.todo),
            "here": card.here,
            "citation": {"text": card.citation.text, "source_ids": list(card.citation.source_ids)},
            "series_href": f"/v1/sensors/{kind.id}/series",
        }
    )


def _unsupported(release: Any) -> str:
    try:
        return str(json.loads(release.document(ConfigName.UI_COPY).raw)["designed_states"]["not_available_here"])
    except (ValueError, KeyError, TypeError) as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "ui-copy has no designed_states.not_available_here", retryable=True) from error


def _latest(rt: Runtime, view: View, channel_id: str | None, channels: Sequence[ChannelState], config: SensorConfig, env: EnvironmentRow) -> SensorReading | None:
    if channel_id is None:
        return None
    state = next((c for c in channels if c.facts.channel_id == channel_id), None)
    if state is None:
        return None
    records = [r for r in rt.store.readings_since(view, channel_id, since=None, up_to_rev=None, limit=rt.limits.window_scan_max_readings) if r.quality_status != "rejected" and _after_activation(r, env)]
    return None if not records else reading_model(max(records, key=lambda r: (r.observed_at, r.rev or 0)), state, config)


def _after_activation(record: ReadingRecord, env: EnvironmentRow) -> bool:
    """DE23-13: only readings after activation belong to the active place (REPLAY by receive time)."""
    basis = record.received_at if record.origin_mode == "REPLAY" else record.corrected_at
    return basis is not None and basis >= env.activated_at


def series_model(rt: Runtime, view: View, sensor_type_id: str, *, since: datetime | None, until: datetime | None) -> SensorSeries:
    env = rt.store.environment(view)
    config = rt.config_of(view.release)
    kind = config.sensor_type(sensor_type_id)
    if kind is None:
        raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
    if env is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_initialized", retryable=True)
    status = status_model(rt, view, sensor_type_id)
    if status.channel_id is None:
        raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
    limit = rt.limits.series_max_points
    records = [r for r in rt.store.readings_since(view, status.channel_id.root, since=since, up_to_rev=None, limit=limit) if r.quality_status != "rejected" and r.time_state in ("known", "replay") and _after_activation(r, env)]
    if len(records) > limit:
        raise ProblemError(ErrorCode.INVALID_VALUE, "series above sensors.series_max_points")
    spec = kind.aggregation(kind.display_aggregation_id)
    points = [
        {"t": iso(r.observed_at), "value": float(round_wire(r.canonical)), "coverage": 1.0}
        for r in records
        if until is None or r.observed_at <= until
    ]
    start = records[0].observed_at if records else view.now
    end = records[-1].observed_at if records else view.now
    return SensorSeries.model_validate(
        {
            "sensor_type_id": kind.id,
            "channel_id": status.channel_id.root,
            "aggregation_id": spec.id if spec is not None else kind.display_aggregation_id,
            "unit": kind.unit,
            "origin_mode": status.origin_mode.root,
            "from": iso(since or start),
            "to": iso(until or end),
            "points": points,
            "limit_marks": [m.model_dump(mode="json") for m in status.limit_marks],
            "provenance": {"origin_mode": status.origin_mode.root, "source_label": status.source_label},
        }
    )


def profile_sensor_types(view: View, environment_id: str) -> tuple[str, ...]:
    """The sensor types of the active environment, in the profile's own order (E23 6)."""
    document = json.loads(view.release.document(ConfigName.ENVIRONMENT_PROFILES).raw)
    for profile in document["profiles"]:
        if profile["environment_id"] == environment_id:
            return tuple(profile["sensor_type_ids"])
    return ()
