"""Model output remains untrusted until its complete horizon is validated."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


class InvalidModelOutput(ValueError):
    pass


@dataclass(frozen=True)
class ForecastStep:
    mission_day: int
    low: Decimal
    median: Decimal
    high: Decimal


def validate_steps(steps: Sequence[ForecastStep], last_day: int, horizon: int) -> None:
    if horizon < 1 or len(steps) != horizon:
        raise InvalidModelOutput("horizon_mismatch")
    for offset, step in enumerate(steps, start=1):
        if step.mission_day != last_day + offset:
            raise InvalidModelOutput("day_mismatch")
        if not all(
            isinstance(value, Decimal) and value.is_finite() for value in (step.low, step.median, step.high)
        ):
            raise InvalidModelOutput("nonfinite_value")
        if not step.low <= step.median <= step.high:
            raise InvalidModelOutput("interval_order")


def prediction_steps(
    prediction: Sequence[tuple[Decimal, Decimal, Decimal]], last_day: int, horizon: int
) -> list[ForecastStep]:
    try:
        steps = [
            ForecastStep(last_day + offset, *values) for offset, values in enumerate(prediction, start=1)
        ]
        validate_steps(steps, last_day, horizon)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidModelOutput("invalid_prediction") from exc
    return steps
