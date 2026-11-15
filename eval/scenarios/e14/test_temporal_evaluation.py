"""E14-03: real evaluation CLI, forward holdouts and explicit unavailable model status."""

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from .support import ROOT, worker_command, worker_environment

pytestmark = pytest.mark.trace("E14-03", "R12", "R26")


def object_map(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def policy_integer(key: str) -> int:
    policy = object_map(
        json.loads(
            (ROOT / "workers/forecast/config/forecast-policy.json").read_text(
                encoding="utf-8",
            )
        )
    )
    value = policy[key]
    assert isinstance(value, int)
    return value


def evaluate_file(path: Path, name: str, values: tuple[int | None, ...]) -> dict[str, object]:
    source, output = path / f"{name}.json", path / f"{name}.report.json"
    source.write_text(
        json.dumps(
            {
                "unit": "/min",
                "series": [
                    {"mission_day": day, "value": None if value is None else str(value)}
                    for day, value in enumerate(values, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [*worker_command("crew_health_forecast.evaluate"), "--input", str(source), "--output", str(output)],
        cwd=ROOT,
        env=worker_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return object_map(json.loads(output.read_text(encoding="utf-8")))


def method(report: dict[str, object], name: str) -> dict[str, object]:
    return object_map(object_map(report["methods"])[name])


def test_report_has_honest_missingness_denominators_and_model_status(tmp_path: Path) -> None:
    minimum, horizon = policy_integer("min_context_days"), policy_integer("horizon_days")
    values = (*tuple(range(1, minimum + horizon + 2)), None, 90, 91)
    report = evaluate_file(tmp_path, "missing", values)
    assert report["history_days"] == len(values)
    assert report["missing_days"] == 1
    assert report["segments"] == 2
    assert report["horizon"] == horizon
    assert report["origins"] == 2
    assert report["failures"] == 0
    assert report["model_status"] == "weights_absent"
    assert report["unit"] == "/min"
    assert object_map(report["coverage"]) == {"value": None, "covered_steps": 0, "denominator": 0}
    for name in ("persistence", "rolling_mean"):
        score = method(report, name)
        assert score["scored_steps"] == 2 * horizon
        by_step = score["mae_by_step"]
        assert isinstance(by_step, list)
        assert len(by_step) == horizon
        assert all(isinstance(value, str) for value in by_step)
    assert method(report, "persistence")["mae_by_step"] == ["1", "2", "3"]
    assert method(report, "persistence")["mae"] == "2"
    assert method(report, "rolling_mean")["mae_by_step"] == ["4", "5", "6"]
    assert method(report, "rolling_mean")["mae"] == "5"
    changed = evaluate_file(tmp_path, "unscored_future", (*values[:-2], 900, 910))
    assert report["input_sha256"] != changed["input_sha256"]
    for name in ("persistence", "rolling_mean"):
        assert method(report, name) == method(changed, name)
    assert changed["origins"] == report["origins"]


def test_changed_holdout_does_not_change_the_origin_prediction(tmp_path: Path) -> None:
    minimum, horizon = policy_integer("min_context_days"), policy_integer("horizon_days")
    development = tuple(range(1, minimum + 1))
    held = tuple(range(minimum + 1, minimum + horizon + 1))
    original = evaluate_file(tmp_path, "original", development + held)
    changed = evaluate_file(tmp_path, "changed_holdout", development + tuple(value + 100 for value in held))
    assert original["origins"] == changed["origins"] == 1
    for name in ("persistence", "rolling_mean"):
        old, new = method(original, name), method(changed, name)
        assert old["scored_steps"] == new["scored_steps"] == horizon
        old_steps, new_steps = old["mae_by_step"], new["mae_by_step"]
        assert isinstance(old_steps, list)
        assert isinstance(new_steps, list)
        assert len(old_steps) == len(new_steps) == horizon
        # All targets exceed the predictions, so target minus absolute error recovers the prediction.
        for target, old_error, new_error in zip(held, old_steps, new_steps, strict=True):
            assert isinstance(old_error, str)
            assert isinstance(new_error, str)
            assert Decimal(target) - Decimal(old_error) == Decimal(target + 100) - Decimal(new_error)
