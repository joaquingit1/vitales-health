"""The hub client parses exactly the worker's golden wire files (E14 R3 proposal), so the two ends cannot drift."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.adapters.worker_client import _parse, request_body
from crew_health.trends.application.ports import (
    DailyValue,
    ForecastRequest,
    WorkerForecast,
    WorkerUnavailable,
)

WIRE = Path(__file__).parents[3] / "workers" / "forecast" / "tests" / "fixtures" / "wire"


def golden_request() -> ForecastRequest:
    raw = json.loads((WIRE / "forecast-request.json").read_text(encoding="utf-8"))
    unit = Unit(raw["unit"])
    return ForecastRequest(
        raw["request_id"],
        raw["policy_version"],
        tuple(DailyValue(p["mission_day"], Quantity(Decimal(p["value"]), unit)) for p in raw["series"]),
    )


@pytest.mark.trace("E14-03", "R12")
def test_hub_sends_exactly_the_golden_request_body() -> None:
    expected = json.loads((WIRE / "forecast-request.json").read_text(encoding="utf-8"))
    assert json.loads(request_body(golden_request())) == expected


@pytest.mark.trace("E14-03", "R12", "V32")
def test_hub_parses_the_golden_worker_forecast_as_experimental_quantities() -> None:
    result = _parse((WIRE / "forecast-response.json").read_bytes(), golden_request())
    assert isinstance(result, WorkerForecast)
    assert result.interval_level == Decimal("0.8")
    assert [step.mission_day for step in result.steps] == [15, 16, 17]
    assert all(step.median == Quantity(Decimal("14.25"), Unit("/min")) for step in result.steps)


@pytest.mark.trace("E14-03", "V69")
def test_hub_parses_the_golden_unavailable_answer_as_a_typed_reason() -> None:
    result = _parse((WIRE / "unavailable-response.json").read_bytes(), golden_request())
    assert result == WorkerUnavailable("weights_absent")
