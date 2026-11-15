"""Offline temporal evaluation CLI with input provenance and no accuracy claim."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import Field

from crew_health_forecast.adapters.chronos_backend import ChronosBackend
from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.application.policy import ForecastPolicy, load_policy
from crew_health_forecast.application.service import ModelInfo, WireModel
from crew_health_forecast.domain.evaluation import Forecaster, evaluate
from crew_health_forecast.domain.series import Unqualified, parse_value


class EvaluationPoint(WireModel):
    mission_day: Annotated[int, Field(ge=1)]
    value: str | None


class EvaluationInput(WireModel):
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    series: list[EvaluationPoint]


def daily_values(document: EvaluationInput, policy: ForecastPolicy) -> list[Decimal | None]:
    if not document.unit.strip():
        raise ValueError("unit_missing")
    if not document.series:
        return []
    if len(document.series) > policy.max_series_points:
        raise ValueError("too_many_points")
    if document.series[-1].mission_day - document.series[0].mission_day + 1 > policy.max_series_points:
        raise ValueError("too_many_points")
    values: list[Decimal | None] = []
    previous = document.series[0].mission_day - 1
    for point in document.series:
        if point.mission_day <= previous:
            raise ValueError("days_not_increasing")
        gap = point.mission_day - previous - 1
        if len(values) + gap + 1 > policy.max_series_points:
            raise ValueError("too_many_points")
        values.extend([None] * gap)
        value = None if point.value is None else parse_value(point.value)
        if isinstance(value, Unqualified):
            raise ValueError(value.reason)
        values.append(value)
        previous = point.mission_day
    return values


def run(input_path: Path, output_path: Path, settings: WorkerSettings) -> None:
    policy = load_policy(settings.policy_path)
    with input_path.open("rb") as stream:
        raw = stream.read(settings.max_body_bytes + 1)
    if len(raw) > settings.max_body_bytes:
        raise ValueError("input_too_large")
    document = EvaluationInput.model_validate_json(raw)
    values = daily_values(document, policy)
    backend = ChronosBackend.load(settings.weights_dir, policy)
    status = backend.status()
    forecaster: Forecaster | None = None
    if status.ready:

        def predict(context: Sequence[Decimal], horizon: int) -> list[tuple[Decimal, Decimal, Decimal]]:
            return backend.predict(context[-policy.max_context_days :], horizon, policy.low, policy.high)

        forecaster = predict
    report = evaluate(
        values,
        policy.horizon_days,
        policy.min_context_days,
        policy.rolling_window_days,
        forecaster,
        unavailable_reason=status.reason or "model_unavailable",
    )
    payload = asdict(report) | {
        "unit": document.unit,
        "policy_version": policy.version,
        "model": ModelInfo(id=status.model_id, revision=status.revision, sha256=status.sha256).model_dump(),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
    }
    output_path.write_text(
        json.dumps(payload, default=_decimal_text, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _decimal_text(value: object) -> str:
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise TypeError("non_serializable_report_value")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rolling origin temporal evaluation, no success threshold")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.input, args.output, WorkerSettings.from_env())


if __name__ == "__main__":
    main()
