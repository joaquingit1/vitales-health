import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI

from crew_health_forecast.__main__ import main
from crew_health_forecast.adapters.chronos_backend import ChronosBackend
from crew_health_forecast.adapters.settings import WorkerSettings, default_policy_path
from crew_health_forecast.application.policy import ForecastPolicy, load_policy
from crew_health_forecast.evaluate import EvaluationInput, daily_values, run

from .conftest import FakeBackend, LocalClient


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_cli_real_execution_writes_temporal_report(tmp_path: Path) -> None:
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(
        json.dumps(
            {
                "unit": "/min",
                "series": [
                    {"mission_day": day, "value": str(day) if day != 18 else None} for day in range(1, 22)
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("CREW_HEALTH_FORECAST_")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "crew_health_forecast.evaluate",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["policy_version"] == "1"
    assert report["unit"] == "/min"
    assert report["history_days"] == 21
    assert report["missing_days"] == 1
    assert report["segments"] == 2
    assert report["origins"] == 1
    assert report["methods"]["persistence"]["mae"] == "2"
    assert report["methods"]["rolling_mean"]["mae"] == "5"
    assert report["model_status"] == "weights_absent"
    assert report["model"]["sha256"] == load_policy(default_policy_path()).model.sha256
    assert report["coverage"]["denominator"] == 0


@pytest.mark.trace("E14-03", "R26")
def test_cli_does_not_fabricate_missing_days(policy: ForecastPolicy) -> None:
    document = EvaluationInput.model_validate(
        {
            "unit": "1",
            "series": [
                {"mission_day": 5, "value": "1"},
                {"mission_day": 8, "value": None},
                {"mission_day": 9, "value": "3"},
            ],
        }
    )
    assert daily_values(document, policy) == [Decimal(1), None, None, None, Decimal(3)]
    assert daily_values(EvaluationInput(unit="1", series=[]), policy) == []


@pytest.mark.trace("E14-03", "R26")
@pytest.mark.parametrize(
    ("points", "reason"),
    [
        ([{"mission_day": 3, "value": "1"}, {"mission_day": 2, "value": "1"}], "days_not_increasing"),
        ([{"mission_day": 3, "value": "1"}, {"mission_day": 3, "value": "1"}], "days_not_increasing"),
        (
            [{"mission_day": 1, "value": "1"}, {"mission_day": 100000000000000, "value": "1"}],
            "too_many_points",
        ),
        ([{"mission_day": 1, "value": "NaN"}], "nonfinite_value"),
    ],
)
def test_cli_invalid_series(policy: ForecastPolicy, points: list[dict[str, object]], reason: str) -> None:
    document = EvaluationInput.model_validate({"unit": "1", "series": points})
    with pytest.raises(ValueError, match=reason):
        daily_values(document, policy)


@pytest.mark.trace("E14-03", "R26")
def test_cli_bounds_file_size(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"x" * 1025)
    with pytest.raises(ValueError, match="input_too_large"):
        run(source, tmp_path / "output", WorkerSettings(max_body_bytes=1024))


@pytest.mark.trace("E14-03", "R26")
def test_cli_bounds_point_count(policy: ForecastPolicy) -> None:
    document = EvaluationInput.model_validate(
        {"unit": "1", "series": [{"mission_day": day, "value": "1"} for day in range(1, 4)]}
    )
    with pytest.raises(ValueError, match="too_many_points"):
        daily_values(document, replace(policy, max_series_points=2))


@pytest.mark.trace("E14-03", "V69")
def test_non_loopback_cli_refuses_without_flag() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CREW_HEALTH_FORECAST_")}
    env["CREW_HEALTH_FORECAST_HOST"] = "0.0.0.0"
    result = subprocess.run(
        [sys.executable, "-m", "crew_health_forecast"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert "non_loopback_requires_container_network" in result.stderr


@pytest.mark.trace("E14-03", "V75")
def test_corrupt_policy_cli_refuses_start(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("broken", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("CREW_HEALTH_FORECAST_")}
    env["CREW_HEALTH_FORECAST_POLICY"] = str(path)
    result = subprocess.run(
        [sys.executable, "-m", "crew_health_forecast"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert "PolicyInvalid" in result.stderr
    assert default_policy_path().is_file()


@pytest.mark.trace("E14-03", "R26", "AGENTS-3.2")
def test_cli_checks_each_gap_before_allocating(policy: ForecastPolicy) -> None:
    document = EvaluationInput.model_validate(
        {
            "unit": "1",
            "series": [
                {"mission_day": 1, "value": "1"},
                {"mission_day": 10000, "value": "2"},
                {"mission_day": 2, "value": "3"},
            ],
        }
    )
    with pytest.raises(ValueError, match="too_many_points"):
        daily_values(document, policy)


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_cli_scores_ready_backend_and_records_its_identity(
    tmp_path: Path, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(
        json.dumps(
            {"unit": "/min", "series": [{"mission_day": day, "value": str(day)} for day in range(1, 18)]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ChronosBackend, "load", lambda weights_dir, policy: backend)
    run(source, output, WorkerSettings())
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["model_status"] == "evaluated"
    assert report["model"]["id"] == "test/model"
    assert report["methods"]["model"]["mae"] == "2"
    assert report["coverage"]["covered_steps"] == 1
    assert report["coverage"]["denominator"] == 3
    assert backend.calls == 1


@pytest.mark.trace("E14-03", "V69")
def test_server_entrypoint_composes_settings_and_startup_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token"
    token.write_text("entrypoint-test-token", encoding="utf-8")
    for name in os.environ:
        if name.startswith("CREW_HEALTH_FORECAST_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CREW_HEALTH_FORECAST_TOKEN_FILE", str(token))
    monkeypatch.setenv("CREW_HEALTH_FORECAST_WEIGHTS_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_HEALTH_FORECAST_HOST", "::1")
    monkeypatch.setenv("CREW_HEALTH_FORECAST_PORT", "8095")
    monkeypatch.setattr(sys, "argv", ["crew_health_forecast"])
    observed: list[str] = []

    def serve(app: FastAPI, *, host: str, port: int, access_log: bool) -> None:
        assert host == "::1" and port == 8095 and access_log is False
        with LocalClient(app) as client:
            health = client.get("/health").json()
            assert health["reason"] == "weights_hash_mismatch"
            assert health["model"]["id"] == "amazon/chronos-2"
            assert client.post("/v1/forecast").status_code == 401
        observed.append("served")

    monkeypatch.setattr("uvicorn.run", serve)
    main()
    assert observed == ["served"]
