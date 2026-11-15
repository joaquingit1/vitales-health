from __future__ import annotations

import copy
from pathlib import Path

import pytest

from crew_health.model.adapters.isolation import refuse_holdout
from crew_health.model.domain.policy import check_gate
from crew_health.model.domain.release import ModelRefused, validate_manifest


@pytest.mark.trace("R22", "R23", "T-E17-001", "T-E17-043")
def test_manifest_requires_provenance_and_stub_is_profile_fenced(manifest: dict[str, object]) -> None:
    validate_manifest(manifest, profile="eval")
    with pytest.raises(ModelRefused, match="stub_in_flight"):
        validate_manifest(manifest, profile="flight")
    manifest.pop("basis")
    with pytest.raises(ModelRefused, match="manifest_invalid"):
        validate_manifest(manifest, profile="eval")


@pytest.mark.trace("R22", "T-E17-050", "T-E17-051")
@pytest.mark.parametrize(
    "change",
    [
        {"release_id": "other"},
        {"verdict": "fail"},
        {"partition": "dev"},
        {"completeness": "partial"},
        {"failures": [{"gate": "cross_person_leak", "count": 1}]},
        {"regressions": {"grounding": -0.5}},
        {"latency_regression_fraction": 0.5},
    ],
)
def test_promotion_rejects_wrong_or_incomplete_gate(
    change: dict[str, object], gate: dict[str, object], policy: dict[str, object]
) -> None:
    gate.update(change)
    with pytest.raises(ModelRefused):
        check_gate("model_candidate", gate, policy)


@pytest.mark.trace("R22", "T-E17-050")
def test_exact_full_holdout_gate_passes(gate: dict[str, object], policy: dict[str, object]) -> None:
    check_gate("model_candidate", gate, policy)


@pytest.mark.trace("V33", "R23", "T-E17-060")
def test_holdout_cannot_be_read_through_symlink(tmp_path: Path) -> None:
    dev, holdout = tmp_path / "dev", tmp_path / "holdout"
    dev.mkdir()
    holdout.mkdir()
    (holdout / "secret.json").write_text("{}", encoding="utf-8")
    (dev / "alias.json").symlink_to(holdout / "secret.json")
    with pytest.raises(ModelRefused, match="holdout_path_refused"):
        refuse_holdout(dev / "alias.json", dev=dev, holdout=holdout)


@pytest.mark.trace("R22", "T-E17-001")
def test_nonfinite_or_unknown_manifest_fields_are_rejected(manifest: dict[str, object]) -> None:
    candidate = copy.deepcopy(manifest)
    candidate["rogue"] = True
    with pytest.raises(ModelRefused):
        validate_manifest(candidate, profile="eval")
    candidate = copy.deepcopy(manifest)
    candidate["decoding"] = {"temperature": float("nan")}
    with pytest.raises(ModelRefused):
        validate_manifest(candidate, profile="eval")


@pytest.mark.trace("R22", "T-E17-050", "T-E17-051")
@pytest.mark.parametrize("field", ["regressions", "latency_regression_fraction"])
def test_nonfinite_gate_numbers_fail_closed(
    field: str, gate: dict[str, object], policy: dict[str, object]
) -> None:
    gate[field] = (
        {"grounding": float("nan"), "assessment": 0, "acquisition": 0}
        if field == "regressions"
        else float("nan")
    )
    with pytest.raises(ModelRefused):
        check_gate("model_candidate", gate, policy)


@pytest.mark.trace("R22", "T-E17-050", "T-E17-051")
@pytest.mark.parametrize(
    "change",
    [
        {"run_ids": "run_bad"},
        {"run_ids": [1]},
        {"case_set_sha256": "x", "paired_case_set_sha256": "x"},
        {"latency_regression_fraction": False},
    ],
)
def test_gate_requires_typed_identity_and_numeric_metrics(
    change: dict[str, object], gate: dict[str, object], policy: dict[str, object]
) -> None:
    gate.update(change)
    with pytest.raises(ModelRefused):
        check_gate("model_candidate", gate, policy)


@pytest.mark.trace("R22", "R23", "T-E17-001", "T-E17-034")
@pytest.mark.parametrize(
    "path, value",
    [
        ("release_id", "../escape"),
        ("status", "rogue"),
        ("thinking_mode", "off"),
        ("runtime.parallel_slots", 2),
        ("runtime.context_tokens", 0),
        ("basis", {}),
    ],
)
def test_release_refuses_unsafe_identity_runtime_or_missing_numeric_basis(
    path: str, value: object, manifest: dict[str, object]
) -> None:
    from crew_health.model.domain.release import object_value

    if "." in path:
        parent, key = path.split(".")
        object_value(manifest[parent])[key] = value
    else:
        manifest[path] = value
    with pytest.raises(ModelRefused):
        validate_manifest(manifest, profile="eval")
