"""The hub to worker wire contract (E14 R3 proposal) is pinned by golden files the hub tests parse too."""

import json
from pathlib import Path

import pytest

from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.api.app import create_app
from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.service import ForecastService

from .conftest import FakeBackend, LocalClient

WIRE = Path(__file__).parent / "fixtures" / "wire"


def golden(name: str) -> object:
    return json.loads((WIRE / name).read_text(encoding="utf-8"))


@pytest.mark.trace("E14-03", "R12")
def test_worker_answers_the_golden_request_with_exactly_the_golden_response(policy: ForecastPolicy) -> None:
    app = create_app(ForecastService(policy, FakeBackend(), 1), WorkerSettings(), "secret")
    with LocalClient(app) as client:
        response = client.post(
            "/v1/forecast",
            json=golden("forecast-request.json"),
            headers={"Authorization": "Bearer secret"},
        )
    assert response.status_code == 200
    assert response.json() == golden("forecast-response.json")


@pytest.mark.trace("E14-03", "V69")
def test_worker_answers_the_golden_request_with_the_golden_unavailable_shape(policy: ForecastPolicy) -> None:
    app = create_app(ForecastService(policy, FakeBackend("weights_absent"), 1), WorkerSettings(), "secret")
    with LocalClient(app) as client:
        response = client.post(
            "/v1/forecast",
            json=golden("forecast-request.json"),
            headers={"Authorization": "Bearer secret"},
        )
    assert response.json() == golden("unavailable-response.json")
