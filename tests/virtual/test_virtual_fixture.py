# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Fast checks of the virtual-study machinery (spec 15.8) that need no engine chemistry: the
design fixture and its schema, the chamber zones, the truth integrator on a hand-built
truth, pull times, the prediction interval and the report writer."""

import itertools
import json
import math
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest

from pharmacy.config import load_bundle
from tests.oracle.precision import R_FLOAT, to_float
from tests.oracle.truth import PackageModel, TruthModel, TruthPathway, integrate_truth, integrate_truth_mp
from tests.virtual import studies

DESIGNS = studies.load_designs()


def _sourced_values(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if "value" in node and "unit" in node:
            found.append((path, node))
        for key, child in node.items():
            found.extend(_sourced_values(child, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, child in enumerate(node):
            found.extend(_sourced_values(child, f"{path}[{i}]"))
    return found


def test_fixture_validates_against_its_schema() -> None:
    schema = json.loads(studies.SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(DESIGNS)]
    assert not errors, errors


def test_every_value_carries_basis_sources_and_owner() -> None:
    values = _sourced_values(DESIGNS)
    assert values
    missing = [path for path, v in values if not {"basis", "source_ids", "to_be_set_by"} <= set(v)]
    assert not missing, missing


def test_ich_design_is_the_spec_design() -> None:
    ich = studies.design(DESIGNS, "ich")
    got = [
        (c["temperature_k"]["value"], c["rh_percent"]["value"], c["pull_months"]["value"])
        for c in ich["conditions"]
    ]
    assert got == [
        (298.15, 60.0, [0, 3, 6, 9, 12, 18, 24]),
        (303.15, 65.0, [0, 6, 9, 12]),
        (313.15, 75.0, [0, 3, 6]),
    ]
    assert ich["package"] == "declared"
    assert all("MDS01" in c["temperature_k"]["source_ids"] for c in ich["conditions"])


def test_isoconversion_design_spans_the_spec_ranges() -> None:
    iso = studies.design(DESIGNS, "isoconversion")
    temps = [c["temperature_k"]["value"] for c in iso["conditions"]]
    rhs = [c["rh_percent"]["value"] for c in iso["conditions"]]
    assert len(iso["conditions"]) == 5
    assert min(temps) == pytest.approx(323.15)
    assert max(temps) == pytest.approx(353.15)
    assert min(rhs) == 10.0
    assert max(rhs) == 75.0
    assert iso["package"] == "open_dish"
    assert all(c["pull_months"] is None for c in iso["conditions"])
    assert max(iso["isoconversion_pull_fractions"]["value"]) > 1.0, (
        "the last pull must bracket the specification edge"
    )


def test_seeds_and_replicates_are_the_spec_values() -> None:
    assert DESIGNS["mission_seed_base"]["value"] == 20261114
    assert studies.replicate_seed(DESIGNS, 7) == 20261121
    assert {k: v["value"] for k, v in DESIGNS["replicates"].items()} == dict.fromkeys(
        ("vs1", "vs2", "vs3", "vs4", "vs5"), 500
    )


def test_replicate_override_is_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("B3_VS3_REPLICATES", raising=False)
    assert studies.replicate_count(DESIGNS, "vs3") == 500
    monkeypatch.setenv("B3_VS3_REPLICATES", "4")
    assert studies.replicate_count(DESIGNS, "vs3") == 4


def test_generator_streams_are_independent_and_reproducible() -> None:
    a = studies.generator(1, 0).standard_normal(3)
    b = studies.generator(1, 0).standard_normal(3)
    c = studies.generator(1, 1).standard_normal(3)
    assert list(a) == list(b)
    assert list(a) != list(c)


def test_chamber_docs_set_the_condition_and_leave_the_bundle_alone() -> None:
    bundle = load_bundle()
    before = json.dumps(bundle.environment_profiles, sort_keys=True)
    iso = studies.design(DESIGNS, "isoconversion")
    condition = iso["conditions"][0]
    profiles, zones, profile_id, zone_id = studies.chamber_docs(bundle, iso, condition)
    profile = next(p for p in profiles["profiles"] if p["profile_id"] == profile_id)
    zone = next(z for z in zones["zones"] if z["zone_id"] == zone_id)
    assert profile["temperature"]["setpoint_k"]["value"] == condition["temperature_k"]["value"]
    assert profile["relative_humidity"]["setpoint_percent"]["value"] == condition["rh_percent"]["value"]
    assert profile["temperature"]["diurnal_amplitude_k"]["value"] == 0.0
    assert profile["relative_humidity"]["diurnal_amplitude_percent"]["value"] == 0.0
    assert profile["temperature"]["ou_sd_k"]["value"] == iso["chamber"]["temperature_sd_k"]["value"]
    assert zone["environment_profile_id"] == profile_id
    assert json.dumps(bundle.environment_profiles, sort_keys=True) == before


def test_chamber_truth_is_on_the_grid_around_the_setpoint() -> None:
    """Mean over 20 days within 5 standard errors of an OU mean, sd * sqrt(2 tau / T)."""
    bundle = load_bundle()
    ich = studies.design(DESIGNS, "ich")
    condition = ich["conditions"][2]
    duration = 20 * 86400.0
    t, temps, rhs = studies.chamber_truth(bundle, ich, condition, duration, 20261114)
    grid = ich["chamber"]["sampling_interval_s"]["value"]
    tau = ich["chamber"]["ou_time_constant_s"]["value"]
    assert t[0] == 0.0
    assert t[-1] >= duration
    assert all(math.isclose(b - a, grid) for a, b in itertools.pairwise(t))
    se_scale = math.sqrt(2 * tau / duration)
    mean_t = math.fsum(temps) / len(temps)
    mean_rh = math.fsum(rhs) / len(rhs)
    assert (
        abs(mean_t - condition["temperature_k"]["value"])
        <= 5 * ich["chamber"]["temperature_sd_k"]["value"] * se_scale
    )
    assert (
        abs(mean_rh - condition["rh_percent"]["value"])
        <= 5 * ich["chamber"]["rh_sd_percent"]["value"] * se_scale
    )


def test_truth_integrator_float_and_mpmath_agree_on_a_chamber() -> None:
    """A hand-built truth (one pathway) over 3 days of the 80 C isoconversion chamber, open
    dish: float and 50-digit trapezoids at the same substeps agree to 1e-12 relative."""
    bundle = load_bundle()
    iso = studies.design(DESIGNS, "isoconversion")
    condition = iso["conditions"][-1]
    t, temps, rhs = studies.chamber_truth(bundle, iso, condition, 3 * 86400.0, 20261114)
    tm = TruthModel((TruthPathway(-20.0, 100_000.0, 0.03),), 298.15, 60.0)
    result = integrate_truth(tm, t, temps, rhs, PackageModel.open_dish())
    exact = integrate_truth_mp(tm, t, temps, rhs, PackageModel.open_dish(), substeps=result.substeps)
    assert result.halving_relative_error < 1e-6
    assert abs(result.theta_at[-1] - to_float(exact)) <= 1e-12 * to_float(exact)


def test_ich_pull_times_are_the_design_months() -> None:
    ich = studies.design(DESIGNS, "ich")
    pulls = studies.pull_times_s(ich, ich["conditions"][0], cast(Any, None), cast(Any, None), 90.0)
    months = ich["conditions"][0]["pull_months"]["value"]
    assert pulls == [m * studies.SECONDS_PER_MONTH for m in months]


def test_pull_times_between_grid_nodes_are_interpolated() -> None:
    nodes, temps, rhs, idx = studies.with_nodes(
        [0.0, 10.0, 20.0], [300.0, 310.0, 330.0], [40.0, 50.0, 60.0], [0.0, 2.5, 15.0]
    )
    assert nodes == [0.0, 2.5, 10.0, 15.0, 20.0]
    assert temps == [300.0, 302.5, 310.0, 320.0, 330.0]
    assert rhs == [40.0, 42.5, 50.0, 55.0, 60.0]
    assert idx == [0, 1, 3]


def _fit(covariance: tuple[tuple[float, ...], ...]) -> Any:
    from pharmacy.fit import StudyFit

    return StudyFit(
        "F1",
        ("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh", "c0_percent"),
        (-18.0, 90_000.0, 0.02, 100.0),
        covariance,
        1.0,
        10,
        0.0,
        0.0,
        0.0,
        298.15,
        60.0,
        True,
        inputs_sha256=studies.observation_digest(()),
    )


def test_prediction_interval_collapses_to_the_closed_form_without_uncertainty() -> None:
    zero = tuple(tuple(0.0 for _ in range(4)) for _ in range(4))
    t = 24 * studies.SECONDS_PER_MONTH
    lo, hi = studies.predicted_potency_interval(_fit(zero), 303.15, 65.0, t, 1)
    ln_k = -18.0 - 90_000.0 / R_FLOAT * (1 / 303.15 - 1 / 298.15) + 0.02 * (65.0 - 60.0)
    expected = 100.0 * math.exp(-math.exp(ln_k) * t)
    assert lo == pytest.approx(expected, rel=1e-12)
    assert hi == pytest.approx(expected, rel=1e-12)


def test_interval_contains_uses_the_standard_error() -> None:
    cov = tuple(tuple(4.0 if i == j else 0.0 for j in range(4)) for i in range(4))
    inside, z = studies.interval_contains(_fit(cov), "ln_k_ref_per_s", -18.0 + 2 * 1.9)
    assert inside
    assert z == pytest.approx(-1.9)
    inside, z = studies.interval_contains(_fit(cov), "ln_k_ref_per_s", -18.0 - 2 * 2.0)
    assert not inside
    assert z == pytest.approx(2.0)


def test_write_report_merges_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(studies, "REPORT_PATH", tmp_path / "report.json")
    studies.write_report("A", {"x": 1})
    studies.write_report("B", {"y": 2})
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["sections"] == {"A": {"x": 1}, "B": {"y": 2}}


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_failed_study_replaces_stale_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    monkeypatch.setattr(studies, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setenv("PHARMACY_TEST_WORKERS", "1")
    studies.write_report("failed", {"old_success": True})

    def fail(r: int) -> int:
        raise error_type(f"replicate {r} failed")

    with pytest.raises(error_type, match="replicate 0 failed"):
        studies.study_map(fail, [0], label="failed")
    section = json.loads((tmp_path / "report.json").read_text())["sections"]["failed"]
    assert section["status"] == ("interrupted" if error_type is KeyboardInterrupt else "execution_failed")
    assert "old_success" not in section
    assert section["error"] == f"{error_type.__name__}: replicate 0 failed"
