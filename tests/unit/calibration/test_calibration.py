"""Independent arithmetic, reproducibility and failure-path checks for CX-CAL."""

import copy
import json
import math
import runpy
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from scipy.integrate import quad
from scipy.stats import norm, t, truncnorm

from pharmacy import calibration as cal
from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K as R
from pharmacy.constants import SECONDS_PER_DAY
from pharmacy.errors import (
    ComputationError,
    ConfigLoadError,
    ConfigReferenceError,
    ConfigSchemaError,
    InputError,
)
from pharmacy.microclimate import exchange_time_constant_s
from pharmacy.types import JSONValue

VECTOR = Path("tests/data/vectors/validation-du2011.json")
FLIGHT = Path("data/pharmacy/calibration/du2011-flight-condition.json")


@pytest.fixture
def bundle() -> ConfigBundle:
    return load_bundle()


@pytest.fixture
def temporary_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree("config/pharmacy", target)
    return target


def test_committed_values_reproduce_exactly(bundle: ConfigBundle) -> None:
    before = copy.deepcopy(bundle.documents)
    result = cal.calibrate(bundle, VECTOR, FLIGHT)
    assert list(result.priors) == bundle.reaction_classes["total_rate_priors"]
    station = next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit")
    assert result.multiplier == station["rate_multiplier"]
    assert result.multiplier_provenance == station["rate_multiplier_calibration"]
    assert len(result.rows) == 36
    assert len(result.multiplier_provenance["rows"]) == 19
    assert bundle.documents == before
    stats = {s["name"]: s["value"] for s in result.multiplier_provenance["statistics"]}
    assert stats["flight_temperature_k"] == 296.15
    assert stats["flight_temperature_sd_k"] == 2.25
    assert stats["flight_rh_percent"] == 24
    assert stats["flight_rh_sd_percent"] == 0.5
    assert stats["censored_ground_rows"] == stats["censored_flight_rows"] == 0
    censored = [r.number for r in result.rows if r.ground.censored]
    assert censored == [8]


def test_two_point_rate_and_censored_likelihood() -> None:
    result = cal.two_point_rate(100, 80, 1, 2, 1000)
    assert result.observed_per_s == pytest.approx(math.log(1.25) / 1000)
    assert math.exp(result.ln_per_s) == pytest.approx(result.observed_per_s)
    assert result.log_loss_sd == pytest.approx(math.hypot(0.01, 0.025))
    assert not result.censored
    flat = cal.two_point_rate(100, 100, 1, 1, 1000)
    assert flat.observed_per_s == 0
    assert math.exp(flat.ln_per_s) == pytest.approx(math.sqrt(2) / 100 * norm.ppf(0.75) / 1000)
    gain = cal.two_point_rate(100, 101, 1, 1, 1000)
    y, sd = math.log(100 / 101), math.hypot(1 / 100, 1 / 101)
    expected = truncnorm.ppf(0.5, -y / sd, math.inf, loc=y, scale=sd)
    assert gain.observed_per_s < 0
    assert gain.censored
    assert math.exp(gain.ln_per_s) == pytest.approx(expected / 1000)
    assert math.exp(gain.ln_per_s) < math.exp(flat.ln_per_s)


@pytest.mark.parametrize(
    "args",
    [
        (0, 100, 1, 1, 1),
        (100, -1, 1, 1, 1),
        (100, 99, -1, 1, 1),
        (100, 99, 1, -1, 1),
        (100, 99, 1, 1, 0),
        (100, 100, 0, 0, 1),
        (math.nan, 100, 1, 1, 1),
        (100, math.inf, 1, 1, 1),
    ],
)
def test_rate_rejects_bad_inputs(args: tuple[float, ...]) -> None:
    with pytest.raises(InputError):
        cal.two_point_rate(*args)


def test_rate_numerical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(truncnorm, "ppf", lambda *args, **kwargs: math.nan)
    with pytest.raises(ComputationError, match="unresolved"):
        cal.two_point_rate(100, 100, 1, 1, 1)
    with pytest.raises(ComputationError, match="overflow"):
        cal.two_point_rate(100, 50, 1, 1, math.nextafter(0, 1))


def test_pooled_variance_and_t_against_hand_values() -> None:
    # A: mean=2, RSS=2. B: mean=6, RSS=8. N=5,F=2,df=3.
    result = cal.pooled_predictive({"a": [1, 2, 3], "b": [4, 8]}, 0.90)
    assert result.means == {"a": 2, "b": 6}
    assert result.pooled_variance == pytest.approx(10 / 3)
    assert result.degrees_of_freedom == 3
    for family, count in (("a", 3), ("b", 2)):
        t_half_width = t.ppf(0.95, 3) * math.sqrt(10 / 3) * math.sqrt(1 + 1 / count)
        assert result.normal_sd[family] * norm.ppf(0.95) == pytest.approx(t_half_width)
    assert cal.pooled_predictive({"a": [2, 2]}, 0.9).normal_sd["a"] == 0


@pytest.mark.parametrize(
    ("groups", "coverage"),
    [
        ({}, 0.9),
        ({"a": []}, 0.9),
        ({"a": [1]}, 0.9),
        ({"a": [1, 2]}, 0),
        ({"a": [1, 2]}, 1),
        ({"a": [1, math.inf]}, 0.9),
        ({"a": [1, 2]}, math.nan),
    ],
)
def test_pooled_bad_input(groups: dict[str, list[float]], coverage: float) -> None:
    with pytest.raises(InputError):
        cal.pooled_predictive(groups, coverage)


def test_pooled_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ComputationError, match="overflow"):
        cal.pooled_predictive({"a": [-1e308, 1e308]}, 0.9)
    monkeypatch.setattr(t, "ppf", lambda *args: math.inf)
    with pytest.raises(ComputationError, match="nonfinite"):
        cal.pooled_predictive({"a": [1, 2]}, 0.9)


def test_package_integral_and_residual_closed_form(bundle: ConfigBundle, tmp_path: Path) -> None:
    # Replace only synthetic row 1 assays. Repackaged start RH tracks ambient, so
    # log(flight/ground physics) = Ea/R*(1/Tg-1/Tf)+B*(RHf-RHg).
    data = cast(dict[str, Any], json.loads(VECTOR.read_text()))
    row = data["vectors"][0]["expected"]["rows"][0]["parsed"]
    dt = (880 - 13) * SECONDS_PER_DAY
    ratio = math.exp(120000 / R * (1 / 295.15 - 1 / 296.15) + 0.04 * (24 - 40))
    kg, residual = 1e-9, 0.7
    row["day_13_control"]["value"] = row["day_13_flight"]["value"] = 100
    row["day_880_control"]["value"] = 100 * math.exp(-kg * dt)
    row["day_880_flight"]["value"] = 100 * math.exp(-kg * ratio * math.exp(residual) * dt)
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(data))
    result = cal.calibrate(bundle, path, FLIGHT)
    assert result.rows[0].flight_factor / result.rows[0].ground_factor == pytest.approx(ratio, rel=1e-12)
    assert result.rows[0].flight_residual == pytest.approx(residual, abs=1e-12)
    physics = cal.FamilyPhysics(120000, 24500, 0.04, 0.025)
    condition = cal.Condition(298.15, 0, 60, 0)
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    sealed = cal.mean_rate_factor(bundle, packages["original_blister"], condition, physics, dt)
    assert sealed == pytest.approx(math.exp(0.04 * (35 - 40)))
    bottle = cal.mean_rate_factor(bundle, packages["original_bottle"], condition, physics, dt)
    opened = cal.mean_rate_factor(bundle, packages["original_vial"], condition, physics, dt)
    assert sealed < bottle < opened
    assert opened == pytest.approx(math.exp(0.04 * 20))


@pytest.mark.parametrize(
    ("condition", "physics", "duration"),
    [
        (cal.Condition(0, 0, 40, 0), cal.FamilyPhysics(1, 0, 0, 0), 1),
        (cal.Condition(298, -1, 40, 0), cal.FamilyPhysics(1, 0, 0, 0), 1),
        (cal.Condition(298, 0, 101, 0), cal.FamilyPhysics(1, 0, 0, 0), 1),
        (cal.Condition(298, 0, 40, 0), cal.FamilyPhysics(1, 0, 0, 0), 0),
        (cal.Condition(298, 0, 40, 0), cal.FamilyPhysics(math.nan, 0, 0, 0), 1),
    ],
)
def test_mean_factor_bad_input(
    bundle: ConfigBundle, condition: cal.Condition, physics: cal.FamilyPhysics, duration: float
) -> None:
    with pytest.raises(InputError):
        cal.mean_rate_factor(bundle, bundle.packages["packages"][0], condition, physics, duration)


def test_mean_factor_bad_package_and_overflow(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    package = copy.deepcopy(bundle.packages["packages"][0])
    package["moisture_model"]["sealed_rh_percent"] = None
    args = (bundle, package, cal.Condition(298, 0, 40, 0), cal.FamilyPhysics(1, 0, 0, 0), 1)
    with pytest.raises(InputError, match="sealed"):
        cal.mean_rate_factor(*args)
    package["moisture_model"]["kind"] = "none"
    monkeypatch.setattr(cal, "humidity_integral", lambda *args, **kwargs: math.inf)
    with pytest.raises(ComputationError):
        cal.mean_rate_factor(*args)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_rows",
        "overlap",
        "missing_row",
        "family",
        "alias_rows",
        "alias_condition",
        "no_solution",
        "no_physics",
        "missing_physics",
        "noncentral",
        "wrong_vector",
    ],
)
def test_invalid_bundle_selection(bundle: ConfigBundle, mutation: str) -> None:
    priors = bundle.reaction_classes["total_rate_priors"]
    by_family = {p["dose_form_family"]: p for p in priors}
    if mutation == "no_rows":
        by_family["solid"]["calibration"]["rows"] = None
    elif mutation == "overlap":
        by_family["solid"]["calibration"]["rows"] = [1, 1]
    elif mutation == "missing_row":
        by_family["solid"]["calibration"]["rows"] = [99]
    elif mutation == "family":
        by_family["solid"]["calibration"]["rows"] = [10]
    elif mutation == "alias_rows":
        by_family["suspension"]["calibration"]["rows"] = [1]
    elif mutation == "alias_condition":
        by_family["suspension"]["temperature_k"]["value"] += 1
    elif mutation == "no_solution":
        priors.remove(by_family["solution"])
    elif mutation == "no_physics":
        bundle.reaction_classes["classes"].clear()
    elif mutation == "missing_physics":
        bundle.reaction_classes["classes"][0]["priors"][0]["ea_j_per_mol"] = None
    elif mutation == "noncentral":
        bundle.settings["decision"]["lower_quantile"]["value"] = 0.1
    else:
        by_family["solid"]["calibration"]["vector_id"] = "V2"
    with pytest.raises(InputError):
        cal.calibrate(bundle, VECTOR, FLIGHT)


@pytest.mark.parametrize(
    "mutation", ["missing_endpoint", "unknown_form", "duplicate", "time", "no_vector", "bad_key"]
)
def test_invalid_vector(bundle: ConfigBundle, tmp_path: Path, mutation: str) -> None:
    data = json.loads(VECTOR.read_text())
    vector = data["vectors"][0]
    row = vector["expected"]["rows"][0]["parsed"]
    if mutation == "missing_endpoint":
        row["day_880_control"] = None
    elif mutation == "unknown_form":
        row["product_api"] = "unknown"
    elif mutation == "duplicate":
        vector["expected"]["rows"][1]["parsed"]["row_number"] = 1
    elif mutation == "time":
        vector["inputs"]["time_points_days"] = [13, 13]
    elif mutation == "no_vector":
        data["vectors"] = data["vectors"][1:]
    else:
        del row["product_api"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(InputError):
        cal.calibrate(bundle, path, FLIGHT)


@pytest.mark.parametrize(
    "mutation", ["shape", "schema", "unit", "basis", "source", "range", "sd", "station", "profile"]
)
def test_invalid_flight_input(bundle: ConfigBundle, tmp_path: Path, mutation: str) -> None:
    data = json.loads(FLIGHT.read_text())
    if mutation == "shape":
        data["extra"] = 0
    elif mutation == "schema":
        del data["rh_percent"]["note"]
    elif mutation == "unit":
        data["rh_percent"]["unit"] = "K"
    elif mutation == "basis":
        data["rh_percent"]["basis"] = "derived"
    elif mutation == "source":
        data["rh_percent"]["source_ids"] = ["MDP999"]
    elif mutation == "range":
        data["rh_percent"]["value"] = 101
    elif mutation == "sd":
        data["rh_sd_percent"]["value"] = -1
    elif mutation == "station":
        bundle.zones["zones"][:] = [z for z in bundle.zones["zones"] if z["zone_id"] != "station_kit"]
    else:
        bundle.environment_profiles["profiles"].clear()
    path = tmp_path / "flight.json"
    path.write_text(json.dumps(data))
    with pytest.raises((InputError, ConfigSchemaError)):
        cal.calibrate(bundle, VECTOR, path)


def test_missing_file_and_nonfinite_stat(bundle: ConfigBundle, tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError):
        cal.calibrate(bundle, tmp_path / "absent.json", FLIGHT)
    with pytest.raises(ComputationError):
        cal._stat("bad", math.inf)


def test_no_suspension_is_supported(bundle: ConfigBundle) -> None:
    bundle.reaction_classes["total_rate_priors"][:] = [
        p for p in bundle.reaction_classes["total_rate_priors"] if p["dose_form_family"] != "suspension"
    ]
    assert len(cal.calibrate(bundle, VECTOR, FLIGHT).priors) == 3


def _newer_version(config: Path) -> str:
    """A version strictly newer than both owned documents (the next major), so these tests
    never depend on the committed version numbers."""
    bundle = load_bundle(config)
    majors = [
        int(bundle.documents[name].version.split(".")[0]) for name in ("reaction-classes", "storage-zones")
    ]
    return f"{max(majors) + 1}.0.0"


def test_writer_preserves_unowned_values_and_reproduces(temporary_config: Path) -> None:
    before = load_bundle(temporary_config)
    version = _newer_version(temporary_config)
    result = cal.write_calibration(temporary_config, VECTOR, FLIGHT, version)
    after = load_bundle(temporary_config)
    assert after.documents["reaction-classes"].version == after.documents["storage-zones"].version == version
    assert cal.calibrate(after, VECTOR, FLIGHT) == result
    assert before.reaction_classes["classes"] == after.reaction_classes["classes"]
    for old, new in zip(before.zones["zones"], after.zones["zones"], strict=True):
        for key in old:
            if key not in {"rate_multiplier", "rate_multiplier_calibration"}:
                assert cast(dict[str, Any], old)[key] == cast(dict[str, Any], new)[key]
        if new["zone_id"] in {"transit_kit", "moon_base_pharmacy", "mars_base_pharmacy"}:
            assert new["rate_multiplier"]["mu"] == result.multiplier["mu"]
            assert new["rate_multiplier"]["sigma"] == result.multiplier["sigma"]
            assert new["rate_multiplier"]["basis"] == "team_default"
    assert before.zones["zones"][0] == after.zones["zones"][0]


@pytest.mark.parametrize("version", ["invalid", "0.0.0"])
def test_writer_bad_version(temporary_config: Path, version: str) -> None:
    with pytest.raises(InputError):
        cal.write_calibration(temporary_config, VECTOR, FLIGHT, version)


def test_writer_errors(temporary_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cal, "reference_problems", lambda _: ["synthetic dangling source"])
    with pytest.raises(ConfigReferenceError):
        cal.write_calibration(temporary_config, VECTOR, FLIGHT, _newer_version(temporary_config))
    monkeypatch.setattr(cal, "reference_problems", lambda _: [])

    def fail_write(self: Path, data: str, **kwargs: Any) -> int:
        raise OSError("synthetic read-only file")

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(ConfigLoadError, match="read-only"):
        cal.write_calibration(temporary_config, VECTOR, FLIGHT, _newer_version(temporary_config))


def test_main_paths(
    temporary_config: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    args = [str(VECTOR), "--config", str(temporary_config), "--flight-condition", str(FLIGHT)]
    cal.main(args)
    assert "station factor:" in capsys.readouterr().out
    cal.main([*args, "--write-version", _newer_version(temporary_config)])
    assert "solid: mu=" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cal.main([])
    monkeypatch.setattr(sys, "argv", ["pharmacy.calibration", *args])
    # run_path covers the executable entry without importing a second module instance.
    runpy.run_path(str(Path(cal.__file__)), run_name="__main__")
    assert "row 36" in capsys.readouterr().out


def test_sourced_input_json_rejects_nan(bundle: ConfigBundle, tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"rh_percent": NaN}')
    with pytest.raises(ConfigLoadError):
        cal.calibrate(bundle, VECTOR, path)


def test_schema_failure(bundle: ConfigBundle, tmp_path: Path) -> None:
    path = tmp_path / "bad-schema.json"
    path.write_text(json.dumps(cast(JSONValue, {})))
    with pytest.raises(ConfigSchemaError):
        cal.calibrate(bundle, path, FLIGHT)


def test_zero_mean_b_keeps_b_uncertainty(bundle: ConfigBundle) -> None:
    physics = cal.FamilyPhysics(0, 0, 0, 0.025)
    condition = cal.Condition(298.15, 0, 60, 0)
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    values = {}
    for package_id in ("original_vial", "original_bottle", "original_blister"):
        values[package_id] = cal._conversion_variance(
            bundle, condition, physics, packages[package_id], SECONDS_PER_DAY
        )
    assert values["original_vial"] == pytest.approx((20 * 0.025) ** 2)
    assert values["original_blister"] == pytest.approx((5 * 0.025) ** 2)
    assert 0 < values["original_bottle"] < values["original_blister"]


def test_committed_other_zone_values(bundle: ConfigBundle) -> None:
    result = cal.calibrate(bundle, VECTOR, FLIGHT)
    for zone in bundle.zones["zones"]:
        if zone["zone_id"] in {"transit_kit", "moon_base_pharmacy", "mars_base_pharmacy"}:
            expected = copy.deepcopy(result.multiplier)
            provenance = copy.deepcopy(result.multiplier_provenance)
            for record in (expected, provenance):
                record["basis"] = "team_default"
                record["to_be_set_by"] = "pharmacist and flight surgeon"
                record["note"] += (
                    " Du ISS prior reused: no surface data exist; "
                    "pharmacist and flight surgeon must set this prior."
                )
            assert zone["rate_multiplier"] == expected
            assert zone["rate_multiplier_calibration"] == provenance


def test_conversion_variance_matches_spec_93(bundle: ConfigBundle) -> None:
    condition = cal.Condition(295.15, 1.5, 60, 2)
    physics = cal.FamilyPhysics(120000, 24500, 0.04, 0.025)
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == "original_vial")
    variance = cal._conversion_variance(bundle, condition, physics, package, SECONDS_PER_DAY)
    x = (1 / 298.15 - 1 / 295.15) / R
    expected = (x * 24500) ** 2 + (20 * 0.025) ** 2 + (120000 * 1.5 / (R * 295.15**2)) ** 2
    assert variance == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("b", [0.0, 0.04, -0.03, 0.12])
@pytest.mark.parametrize("days", [1.0, 400.0, 867.0])
def test_rate_weighted_rh_is_the_derivative_of_the_mean_rate(
    bundle: ConfigBundle, b: float, days: float
) -> None:
    """Independent check by quadrature: the weighted RH equals RH_out plus
    the integral of dRH exp(B dRH) over the integral of exp(B dRH)."""
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == "original_bottle")
    condition = cal.Condition(296.15, 0, 24, 0)
    physics = cal.FamilyPhysics(120000, 0, b, 0.025)
    duration = days * SECONDS_PER_DAY
    tau = exchange_time_constant_s(package, condition.temperature_k)
    start = package["moisture_model"]["initial_internal_rh_percent"]
    assert start is not None
    delta = start["value"] - condition.rh_percent

    def gap(time_s: float) -> float:
        return delta * math.exp(-time_s / tau)

    numerator = quad(lambda s: gap(s) * math.exp(b * gap(s)), 0, duration, epsabs=0, epsrel=1e-12)[0]
    denominator = quad(lambda s: math.exp(b * gap(s)), 0, duration, epsabs=0, epsrel=1e-12)[0]
    expected = condition.rh_percent + numerator / denominator
    assert cal.rate_weighted_internal_rh(bundle, condition, physics, package, duration) == pytest.approx(
        expected, rel=1e-9
    )


def test_rate_weighted_rh_limits(bundle: ConfigBundle) -> None:
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    physics = cal.FamilyPhysics(120000, 0, 0.04, 0.025)
    condition = cal.Condition(298.15, 0, 60, 0)
    sealed = packages["original_blister"]["moisture_model"]["sealed_rh_percent"]
    assert sealed is not None
    assert cal.rate_weighted_internal_rh(
        bundle, condition, physics, packages["original_blister"], SECONDS_PER_DAY
    ) == pytest.approx(sealed["value"])
    assert cal.rate_weighted_internal_rh(
        bundle, condition, physics, packages["original_vial"], SECONDS_PER_DAY
    ) == pytest.approx(60)


def test_rate_weighted_rh_rejects_nonfinite(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == "original_bottle")
    monkeypatch.setattr(cal, "humidity_integral", lambda *args, **kwargs: 0.0)
    with pytest.raises(ComputationError, match="humidity integral"):
        cal.rate_weighted_internal_rh(
            bundle,
            cal.Condition(298.15, 0, 60, 0),
            cal.FamilyPhysics(1, 0, 0.04, 0),
            package,
            SECONDS_PER_DAY,
        )
    monkeypatch.setattr(cal, "humidity_integral", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(cal, "exprel", lambda value: math.inf)
    with pytest.raises(ComputationError, match="weighted internal RH"):
        cal.rate_weighted_internal_rh(
            bundle,
            cal.Condition(298.15, 0, 60, 0),
            cal.FamilyPhysics(1, 0, 0.04, 0),
            package,
            SECONDS_PER_DAY,
        )
