"""Real Chronos-2 inference on a NASA daily series through the worker's own HTTP app.

Needs the optional model extra (`uv sync --extra model`) and the pinned weights in CREW_HEALTH_FORECAST_WEIGHTS_DIR.
Run with `uv run --extra model pytest -m slow`.
"""

import csv
import importlib.util
import os
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from crew_health_forecast.adapters.chronos_backend import ChronosBackend
from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.api.app import create_app
from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.service import ForecastService

from .conftest import LocalClient

# NASA OSDR Environmental Data App, RR-1 (SpaceX CRS-4), ISS habitat CO2; provenance in eda_rr1_provenance.json.
RR1 = Path(__file__).resolve().parents[3] / "docs" / "product" / "nasa-data" / "eda_rr1_telemetry.csv"

pytestmark = [pytest.mark.slow, pytest.mark.trace("E14-03", "R12", "V32", "V69")]


def rr1_daily_co2() -> list[dict[str, object]]:
    """Daily mean of every ISS CO2 reading per source date, one mission day per calendar day, at 0.1 ppm."""
    days: defaultdict[str, list[Decimal]] = defaultdict(list)
    with RR1.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            days[row["time"][:10]].append(Decimal(row["co2_iss"]))
    means = [sum(values, Decimal(0)) / len(values) for _, values in sorted(days.items())]
    return [
        {"mission_day": day, "value": str(mean.quantize(Decimal("0.1"), ROUND_HALF_EVEN))}
        for day, mean in enumerate(means, start=1)
    ]


def weights() -> Path:
    if importlib.util.find_spec("chronos") is None or importlib.util.find_spec("torch") is None:
        pytest.skip("model_package_absent: install the worker's model extra")
    raw = os.environ.get("CREW_HEALTH_FORECAST_WEIGHTS_DIR")
    if raw is None:
        pytest.skip("weights_absent: set CREW_HEALTH_FORECAST_WEIGHTS_DIR to the pinned weights")
    return Path(raw)


def test_pinned_chronos_forecasts_nasa_rr1_daily_co2_with_ordered_intervals(policy: ForecastPolicy) -> None:
    backend = ChronosBackend.load(weights(), policy)
    assert backend.status().ready, backend.status().reason
    series = rr1_daily_co2()
    assert len(series) == 43
    assert policy.min_context_days <= len(series) <= policy.max_context_days
    daily = sorted(Decimal(str(point["value"])) for point in series)
    recorded = (daily[0], daily[-1])
    app = create_app(ForecastService(policy, backend, 1), WorkerSettings(), "secret")
    with LocalClient(app) as client:
        health = client.get("/health").json()
        assert health["state"] == "ready" and health["reason"] is None
        assert health["model"] == {
            "id": policy.model.id,
            "revision": policy.model.revision,
            "sha256": policy.model.sha256,
        }
        body = {
            "request_id": "rr1:co2_daily",
            "policy_version": policy.version,
            "unit": "ppm",
            "series": series,
        }
        response = client.post("/v1/forecast", json=body, headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "forecast"
    assert result["unit"] == "ppm"
    assert result["context_days"] == len(series)
    assert result["model"]["revision"] == policy.model.revision
    steps = result["steps"]
    assert [step["mission_day"] for step in steps] == [
        len(series) + offset for offset in range(1, policy.horizon_days + 1)
    ]
    for step in steps:
        low, median, high = (Decimal(step[key]) for key in ("low", "median", "high"))
        assert all(value.is_finite() for value in (low, median, high))
        assert low <= median <= high
        # The median stays inside the range of daily means the habitat itself recorded during RR-1.
        assert recorded[0] <= median <= recorded[1]
