"""Experimental result qualification and deterministic input fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.domain.model import DailyValue


@dataclass(frozen=True, slots=True)
class ForecastStep:
    mission_day: int
    low: Quantity
    median: Quantity
    high: Quantity


@dataclass(frozen=True, slots=True)
class WorkerForecast:
    model_id: str
    model_revision: str
    model_sha256: str
    policy_version: str
    interval_level: Decimal
    steps: tuple[ForecastStep, ...]


def validate_forecast(
    result: WorkerForecast, *, input_unit: Unit, last_input_day: int, steps_max: int
) -> bool:
    if not result.interval_level.is_finite() or not Decimal(0) < result.interval_level < Decimal(1):
        return False
    if not result.steps or len(result.steps) > steps_max:
        return False
    if result.steps[0].mission_day != last_input_day + 1:
        return False
    previous = last_input_day
    for step in result.steps:
        if step.mission_day <= previous:
            return False
        previous = step.mission_day
        for quantity in (step.low, step.median, step.high):
            if not quantity.value.is_finite() or quantity.unit != input_unit:
                return False
        if not step.low.value <= step.median.value <= step.high.value:
            return False
    return True


def fingerprint(key_json: str, series: tuple[DailyValue, ...], observation_ids: tuple[str, ...]) -> str:
    payload = {
        "key": json.loads(key_json),
        "unit": series[0].value.unit.code if series else None,
        "days": [point.mission_day for point in series],
        "values": [str(point.value.value) for point in series],
        "observation_ids": observation_ids,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
