"""The sensor evaluator behind S5.10.2 (E23 3.7, 4.4): aggregate and evaluate one immutable input.

An input names exactly which readings it may see (`rev` up to its high water), so a retry or a late recovery
evaluates the original membership under the original release and never a later row. A newer stored evaluation is
never replaced by an older input (the store keeps the higher input revision). Only a typed computation failure is
isolated by E01; storage, integrity and configuration failures escape and abort the unit of work.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from crew_health.kernel.evaluation import EvaluationInput as KernelInput
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.sensors.application.runtime import (
    EVALUATOR_VERSION,
    WINDOW_SCAN_BOUND,
    Runtime,
)
from crew_health.sensors.application.store import ChannelState, EvaluationRow, ReadingRecord
from crew_health.sensors.domain.aggregation import DirectWindows
from crew_health.sensors.domain.errors import ConfigInconsistent, WindowScanBound
from crew_health.sensors.domain.evaluation import (
    Evaluation,
    EvaluationInput,
    evaluate_channel,
    limits_for,
)
from crew_health.sensors.domain.plausibility import assess
from crew_health.sensors.domain.reading import (
    ReadingFact,
    advance_window_end,
    collapse_instants,
    latest_instant,
)

EVALUATION = TypeAdapter(Evaluation)
_MARGIN = timedelta(seconds=1)


def fact_of(record: ReadingRecord) -> ReadingFact:
    return ReadingFact(
        record.reading_id,
        record.channel_id,
        record.adapter_sequence,
        record.corrected_at,
        record.received_at,
        record.time_state,
        record.canonical,
        record.quality_status,
        record.origin_mode,
        record.segment_id,
    )


def evaluation_id(rt: Runtime, channel_id: str, profile_id: str, release_id: str) -> str:
    return rt.ids.derived("evl", channel_id, profile_id, release_id)


class SensorEvaluator:
    """kernel.evaluation.Evaluator for the `sensors.evaluate` registration."""

    def __init__(self, rt: Runtime) -> None:
        self._rt = rt

    def evaluate(self, uow: UnitOfWork, value: KernelInput) -> None:
        rt = self._rt
        row = rt.store.input(uow, value.input_ref)
        if row is None:
            raise ConfigInconsistent("evaluation input is not stored: " + value.input_ref)
        channel = rt.store.channel(uow, row.channel_id)
        environment = rt.store.environment(uow)
        if channel is None or environment is None:
            return  # nothing is evaluated before a mission exists, never under a guessed profile
        if environment.environment_id not in channel.facts.environment_ids:
            return
        release = rt.release_of(uow, value.release_id)
        config = rt.config_of(release)
        kind = config.sensor_type(channel.facts.sensor_type_id)
        if kind is None:
            raise ConfigInconsistent("sensor type is not in the release: " + channel.facts.sensor_type_id)
        evaluation = self._compute(rt, uow, config, kind, channel, row.high_rev, environment, value)
        evaluation_key = evaluation_id(rt, channel.facts.channel_id, environment.profile_id, release.release_id)
        rt.store.save_evaluation(
            uow,
            EvaluationRow(
                evaluation_key,
                channel.facts.channel_id,
                environment.profile_id,
                release.release_id,
                row.high_rev,
                evaluation.window_end,
                evaluation.status,
                EVALUATION.dump_json(evaluation).decode(),
            ),
        )

    def _compute(
        self, rt: Runtime, uow: UnitOfWork, config, kind, channel: ChannelState, high_rev: int, environment, value: KernelInput  # type: ignore[no-untyped-def]
    ) -> Evaluation:
        windowed = [a.window_seconds for a in kind.aggregations if a.window_seconds is not None]
        since = None if channel.window_end is None else channel.window_end - timedelta(seconds=max(windowed, default=0)) - _MARGIN
        records = rt.store.readings_since(uow, channel.facts.channel_id, since=since, up_to_rev=high_rev, limit=rt.limits.window_scan_max_readings)
        facts = [fact_of(r) for r in records]
        shell = EvaluationInput(
            kind,
            channel.facts,
            environment.environment_id,
            limits_for(config.limits, kind.id, environment.environment_id),
            DirectWindows((), channel.facts.expected_interval_seconds, rt.limits.window_scan_max_readings),
            None,
            channel.freshness,
            False,
            channel.clock_state == "trusted",
            uow.now,
        )
        last = facts[-1] if facts else None
        verdict = assess(kind, last) if last is not None else assess(kind, _placeholder(channel))
        try:
            instants = collapse_instants(
                facts, activated_at=environment.activated_at, max_rows=rt.limits.window_scan_max_readings
            )
        except WindowScanBound:
            rt.fault(WINDOW_SCAN_BOUND, uow.now)
            return replace(evaluate_channel(shell, verdict), faults=(WINDOW_SCAN_BOUND,))
        end = advance_window_end(None, latest_instant(instants))
        advanced = end is not None and (len(instants) < 2 or instants[-2].at < end)
        full = replace(
            shell,
            windows=DirectWindows(instants, channel.facts.expected_interval_seconds, rt.limits.window_scan_max_readings),
            window_end=end,
            window_advanced=advanced,
        )
        return evaluate_channel(full, verdict)


def _placeholder(channel: ChannelState) -> ReadingFact:
    from decimal import Decimal

    return ReadingFact("none", channel.facts.channel_id, 0, None, None, "unknown", Decimal(0), "not_assessed", channel.facts.origin_mode, None)


def stored_evaluation(rt: Runtime, view: UnitOfWork | ReadSnapshot, channel: ChannelState, profile_id: str, release_id: str) -> Evaluation | None:
    row = rt.store.evaluation(view, channel.facts.channel_id, profile_id, release_id)
    return None if row is None else EVALUATION.validate_json(row.evaluation_json)


def version() -> str:
    return EVALUATOR_VERSION


def high_water(at: datetime | None, previous: datetime | None) -> datetime | None:
    return advance_window_end(previous, at)
