import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from crew_health_forecast.adapters.settings import default_policy_path
from crew_health_forecast.application.policy import ForecastPolicy, PolicyInvalid, load_policy
from crew_health_forecast.application.service import ForecastRequest, ForecastResponse, ForecastService

from .conftest import FakeBackend


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_shipped_policy_validates() -> None:
    path = default_policy_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(path.with_suffix(".schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)
    policy = load_policy(path)
    assert policy.model.id == "amazon/chronos-2"
    assert re.fullmatch(r"[0-9a-f]{40}", policy.model.revision)
    assert policy.model.sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", policy.model.sha256)
    assert policy.horizon_days == 3
    assert policy.low.as_tuple().exponent == -1


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_policy_edit_changes_forecast_horizon_without_code(
    tmp_path: Path, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    raw = json.loads(default_policy_path().read_text(encoding="utf-8"))
    raw["horizon_days"] = 5
    scratch = tmp_path / "policy.json"
    scratch.write_text(json.dumps(raw), encoding="utf-8")
    policy = load_policy(scratch)
    result = ForecastService(policy, backend, 1).forecast(forecast_request, 2)
    assert isinstance(result, ForecastResponse)
    assert len(result.steps) == 5


@pytest.mark.trace("AGENTS-1.1", "V75")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("horizon_days", "3"),
        ("horizon_days", 0),
        ("horizon_days", True),
        ("min_context_days", 513),
        ("max_context_days", 0),
        ("basis", ""),
        ("extra", "unknown"),
        ("cadence", "hour"),
        ("interval", {"low": "0.5", "high": "0.9"}),
        ("interval", {"low": "0.1", "high": "0.4"}),
        ("interval", {"low": "0", "high": "1"}),
        ("model", {"id": "amazon/chronos-2", "revision": "x", "sha256": "bad"}),
    ],
)
def test_invalid_policy_refuses_startup(tmp_path: Path, key: str, value: object) -> None:
    raw = json.loads(default_policy_path().read_text(encoding="utf-8"))
    raw[key] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PolicyInvalid):
        load_policy(path)


@pytest.mark.trace("AGENTS-1.1", "V75")
@pytest.mark.parametrize("content", ["{not json", "{}", "null", '{"version":"1"}'])
def test_corrupt_or_incomplete_policy(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PolicyInvalid):
        load_policy(path)


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_missing_policy(tmp_path: Path) -> None:
    with pytest.raises(PolicyInvalid):
        load_policy(tmp_path / "absent")


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_policy_is_frozen(policy: ForecastPolicy) -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        policy.horizon_days = 50  # type: ignore[misc]


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_every_policy_value_carries_provenance_with_nasa_sources_checked_first() -> None:
    raw = json.loads(default_policy_path().read_text(encoding="utf-8"))
    provenance = raw["provenance"]
    assert set(provenance) == set(raw) - {"version", "basis", "cadence", "provenance"}
    for entry in provenance.values():
        assert entry["nasa_checked"] and all(
            item.startswith("NASA-SOURCES.md ") for item in entry["nasa_checked"]
        )
        assert entry["basis"] == "sourced" or entry["to_be_set_by"]


@pytest.mark.trace("AGENTS-1.1", "V75")
@pytest.mark.parametrize(
    "edit",
    [
        lambda p: p.pop("horizon_days"),
        lambda p: p["interval"].update(nasa_checked=[]),
        lambda p: p["interval"].update(basis="sourced", package=[]),
    ],
)
def test_missing_or_unsourced_provenance_refuses_startup(tmp_path: Path, edit: object) -> None:
    raw = json.loads(default_policy_path().read_text(encoding="utf-8"))
    edit(raw["provenance"])  # type: ignore[operator]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PolicyInvalid):
        load_policy(path)
