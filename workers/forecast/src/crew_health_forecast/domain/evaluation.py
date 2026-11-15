"""Rolling origin holdout. No future value participates in development."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from crew_health_forecast.domain.result import prediction_steps

type Forecaster = Callable[[Sequence[Decimal], int], list[tuple[Decimal, Decimal, Decimal]]]


@dataclass(frozen=True)
class MethodScore:
    mae: Decimal | None
    mae_by_step: tuple[Decimal | None, ...]
    scored_steps: int


@dataclass(frozen=True)
class Coverage:
    value: Decimal | None
    covered_steps: int
    denominator: int


@dataclass(frozen=True)
class EvaluationReport:
    history_days: int
    missing_days: int
    segments: int
    horizon: int
    origins: int
    methods: dict[str, MethodScore]
    failures: int
    coverage: Coverage
    model_status: str


def _segments(series: Sequence[Decimal | None]) -> list[list[Decimal]]:
    segments: list[list[Decimal]] = []
    current: list[Decimal] = []
    for value in series:
        if value is None:
            if current:
                segments.append(current)
                current = []
        else:
            if not value.is_finite():
                raise ValueError("nonfinite_evaluation_value")
            current.append(value)
    if current:
        segments.append(current)
    return segments


def _score(errors: list[list[Decimal]]) -> MethodScore:
    counts = [len(step) for step in errors]
    total = sum(counts)
    mae = sum((sum(step, Decimal(0)) for step in errors), Decimal(0)) / total if total else None
    by_step = tuple(sum(step, Decimal(0)) / len(step) if step else None for step in errors)
    return MethodScore(mae, by_step, total)


def _model_errors(
    forecaster: Forecaster,
    development: Sequence[Decimal],
    held: Sequence[Decimal],
    errors: list[list[Decimal]],
) -> int:
    steps = prediction_steps(forecaster(development, len(held)), len(development), len(held))
    # Compute the complete origin before changing either scoring accumulator.
    origin_errors = [abs(step.median - actual) for step, actual in zip(steps, held, strict=True)]
    covered = sum(step.low <= actual <= step.high for step, actual in zip(steps, held, strict=True))
    for index, error in enumerate(origin_errors):
        errors[index].append(error)
    return covered


def evaluate(
    series: Sequence[Decimal | None],
    horizon: int,
    min_context: int,
    rolling_window: int,
    forecaster: Forecaster | None,
    *,
    unavailable_reason: str = "model_unavailable",
) -> EvaluationReport:
    if horizon < 1 or min_context < 1 or rolling_window < 1:
        raise ValueError("invalid_evaluation_bounds")
    segments = _segments(series)
    errors: dict[str, list[list[Decimal]]] = {
        name: [[] for _ in range(horizon)] for name in ("persistence", "rolling_mean")
    }
    if forecaster is not None:
        errors["model"] = [[] for _ in range(horizon)]
    origins = failures = covered = 0
    for segment in segments:
        for origin in range(min_context, len(segment) - horizon + 1):
            development = tuple(segment[:origin])
            held = segment[origin : origin + horizon]
            origins += 1
            if forecaster is not None:
                try:
                    covered += _model_errors(forecaster, development, held, errors["model"])
                except Exception:
                    # A failed origin stays in origins and failures, never in any scored denominator.
                    failures += 1
                    continue
            window = development[-rolling_window:]
            predictions = {
                "persistence": development[-1],
                "rolling_mean": sum(window, Decimal(0)) / len(window),
            }
            for name, prediction in predictions.items():
                for index, actual in enumerate(held):
                    errors[name][index].append(abs(prediction - actual))
    methods = {name: _score(error) for name, error in sorted(errors.items())}
    denominator = methods["model"].scored_steps if forecaster is not None else 0
    return EvaluationReport(
        len(series),
        sum(value is None for value in series),
        len(segments),
        horizon,
        origins,
        methods,
        failures,
        Coverage(Decimal(covered) / denominator if denominator else None, covered, denominator),
        "evaluated" if forecaster is not None else unavailable_reason,
    )
