"""Offline Du 2011 calibration. Never imported by the estimation pipeline.

The reproducible method, assumptions and CLI commands are documented in
``data/pharmacy/calibration/README.md``. Input failures raise InputError or the
configuration loader's ConfigError subclasses; numerical failures raise
ComputationError. No sampling, clock, network or chemistry cache is used.
"""

import argparse
import copy
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

from jsonschema import Draft202012Validator
from scipy.special import exprel
from scipy.stats import norm, t, truncnorm

from pharmacy import __version__
from pharmacy.config import (
    ConfigBundle,
    canonical_sha256,
    document_from_json,
    load_bundle,
    load_json,
    reference_problems,
    schema_registry,
    validate_instance,
)
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K as R
from pharmacy.constants import SECONDS_PER_DAY
from pharmacy.errors import (
    ComputationError,
    ConfigLoadError,
    ConfigReferenceError,
    ConfigSchemaError,
    InputError,
)
from pharmacy.kinetics import humidity_integral, rate_constant
from pharmacy.microclimate import exchange_time_constant_s
from pharmacy.types import (
    CalibrationProvenance,
    CalibrationStatistic,
    DoseFormFamily,
    JSONValue,
    PackageDoc,
    Prior,
    SourcedValue,
    TotalRatePrior,
)


@dataclass(frozen=True)
class RateObservation:
    """Signed observed rate and positive likelihood representative, both in 1/s."""

    observed_per_s: float
    ln_per_s: float
    censored: bool
    log_loss_sd: float


@dataclass(frozen=True)
class Predictive:
    """Pooled within-family variance and central-interval-equivalent normal spreads."""

    means: Mapping[str, float]
    pooled_variance: float
    degrees_of_freedom: int
    normal_sd: Mapping[str, float]


@dataclass(frozen=True)
class Condition:
    temperature_k: float
    temperature_sd_k: float
    rh_percent: float
    rh_sd_percent: float


@dataclass(frozen=True)
class FamilyPhysics:
    ea: float
    ea_sd: float
    b: float
    b_sd: float


@dataclass(frozen=True)
class CalibratedRow:
    number: int
    label: str
    family: DoseFormFamily
    package_id: str
    ground: RateObservation
    flight: RateObservation
    ground_factor: float
    flight_factor: float
    ln_reference_rate: float
    conversion_variance: float
    flight_residual: float


@dataclass(frozen=True)
class CalibrationResult:
    priors: tuple[TotalRatePrior, ...]
    multiplier: Prior
    multiplier_provenance: CalibrationProvenance
    rows: tuple[CalibratedRow, ...]


def _finite(*values: float) -> None:
    if not all(math.isfinite(x) for x in values):
        raise InputError("calibration values must be finite")


def two_point_rate(
    initial: float, final: float, initial_sd: float, final_sd: float, duration_s: float
) -> RateObservation:
    """First-order rate from two assays; retain nonpositive observations explicitly.

    For log loss y <= 0, use the median of the Gaussian measurement likelihood
    N(y, s_y^2), restricted to nonnegative true loss (flat prior on true loss).
    s_y is the delta-method assay sd. This is a positive-truncated likelihood
    imputation, not a detection floor or a dropped row. Positive y is unchanged.
    Zero noise with nonpositive y cannot identify a positive rate: InputError.
    """
    _finite(initial, final, initial_sd, final_sd, duration_s)
    if min(initial, final, duration_s) <= 0 or min(initial_sd, final_sd) < 0:
        raise InputError("assays and duration must be positive; assay sd must be nonnegative")
    loss = math.log(initial) - math.log(final)
    sd = math.hypot(initial_sd / initial, final_sd / final)
    representative = loss
    if loss <= 0:
        if sd == 0:
            raise InputError("nonpositive loss needs positive assay uncertainty for censoring")
        representative = float(truncnorm.ppf(0.5, -loss / sd, math.inf, loc=loss, scale=sd))
    if not math.isfinite(representative) or representative <= 0:
        raise ComputationError("positive-truncated loss likelihood is numerically unresolved")
    result = RateObservation(
        loss / duration_s, math.log(representative) - math.log(duration_s), loss <= 0, sd
    )
    if not all(math.isfinite(x) for x in (result.observed_per_s, result.ln_per_s, result.log_loss_sd)):
        raise ComputationError("two-point rate overflow")
    return result


def pooled_predictive(groups: Mapping[str, Sequence[float]], coverage: float) -> Predictive:
    """Within-family RSS/(N-F); t predictive sd s sqrt(1+1/n), converted by t/z.

    Raises InputError for empty groups, N <= F, invalid coverage or nonfinite
    observations. No robust clipping, row deletion or family-specific shrinkage.
    """
    _finite(coverage)
    if not 0 < coverage < 1 or not groups or any(not values for values in groups.values()):
        raise InputError("nonempty families and coverage strictly between zero and one are required")
    for values in groups.values():
        _finite(*values)
    df = sum(len(values) for values in groups.values()) - len(groups)
    if df <= 0:
        raise InputError("pooled variance requires N > F")
    locations = {key: mean(groups[key]) for key in sorted(groups)}
    try:
        variance = math.fsum((x - locations[key]) ** 2 for key in sorted(groups) for x in groups[key]) / df
        ratio = float(t.ppf((1 + coverage) / 2, df) / norm.ppf((1 + coverage) / 2))
        spreads = {key: math.sqrt(variance * (1 + 1 / len(groups[key]))) * ratio for key in sorted(groups)}
    except OverflowError as exc:
        raise ComputationError("pooled variance overflow") from exc
    if not all(math.isfinite(x) for x in (variance, *locations.values(), *spreads.values())):
        raise ComputationError("nonfinite pooled predictive result")
    return Predictive(locations, variance, df, spreads)


def mean_rate_factor(
    bundle: ConfigBundle, package: PackageDoc, condition: Condition, physics: FamilyPhysics, duration_s: float
) -> float:
    """Theta/duration for k_ref=1 using the engine microclimate and humidity integral.

    Start at configured initial internal RH, or ambient if null (spec 4.3).
    Raises InputError for invalid conditions/duration; engine domain and numerical
    errors propagate as PharmacyError subclasses.
    """
    _finite(
        duration_s,
        condition.temperature_k,
        condition.temperature_sd_k,
        condition.rh_percent,
        condition.rh_sd_percent,
        physics.ea,
        physics.ea_sd,
        physics.b,
        physics.b_sd,
    )
    if duration_s <= 0 or condition.temperature_k <= 0 or not 0 <= condition.rh_percent <= 100:
        raise InputError("positive duration/temperature and RH in [0,100] required")
    if min(condition.temperature_sd_k, condition.rh_sd_percent, physics.ea_sd, physics.b_sd) < 0:
        raise InputError("standard uncertainties must be nonnegative")
    moisture = package["moisture_model"]
    tau = exchange_time_constant_s(package, condition.temperature_k)
    initial = moisture["initial_internal_rh_percent"]
    start = condition.rh_percent if initial is None else initial["value"]
    if moisture["kind"] == "sealed_constant":
        sealed = moisture["sealed_rh_percent"]
        if sealed is None:
            raise InputError("sealed package requires sealed_rh_percent")
        start = sealed["value"]
    reference = bundle.settings["reference_conditions"]
    base = rate_constant(
        0.0,
        physics.ea,
        physics.b,
        condition.temperature_k,
        condition.rh_percent,
        reference["temperature_k"]["value"],
        reference["relative_humidity_percent"]["value"],
    )
    numerics = bundle.settings["numerics"]
    integral = humidity_integral(
        physics.b * (start - condition.rh_percent),
        duration_s,
        tau,
        rel_tol=numerics["series_relative_tolerance"]["value"],
        max_terms=int(numerics["series_max_terms"]["value"]),
    )
    factor = base * (integral / duration_s)
    if not math.isfinite(factor) or factor <= 0:
        raise ComputationError("mean rate factor is nonpositive or nonfinite")
    return factor


def _family_physics(bundle: ConfigBundle, family: DoseFormFamily) -> FamilyPhysics:
    candidates = [
        p
        for c in sorted(bundle.reaction_classes["classes"], key=lambda c: c["class_id"])
        if c["driver"] == "thermal" and c["role"] == "destructive" and family in c["applies_to_families"]
        for p in c["priors"]
        if p["dose_form_family"] == family
    ]
    if not candidates:
        raise InputError(f"no destructive thermal priors for {family}")
    moments: list[float] = []
    for field in ("ea_j_per_mol", "b_per_percent_rh"):
        priors = [p[field] for p in candidates]
        if any(p is None for p in priors):
            raise InputError(f"missing {family} {field} prior")
        values = [p for p in priors if p is not None]
        mu = mean(p["mu"] for p in values)
        variance = mean(p["sigma"] ** 2 + (p["mu"] - mu) ** 2 for p in values)
        moments.extend((mu, math.sqrt(variance)))
    return FamilyPhysics(*moments)


def _ground_condition(prior: TotalRatePrior) -> Condition:
    return Condition(
        prior["temperature_k"]["value"],
        prior["temperature_sd_k"]["value"],
        prior["rh_percent"]["value"],
        prior["rh_sd_percent"]["value"],
    )


def _flight_condition(bundle: ConfigBundle, path: Path) -> Condition:
    data = load_json(path)
    if not isinstance(data, dict) or set(data) != {"rh_percent", "rh_sd_percent"}:
        raise InputError("flight condition file must contain exactly rh_percent and rh_sd_percent")
    sources = {s["source_id"] for s in bundle.sources["sources"]}
    for name, value in data.items():
        validator = Draft202012Validator(
            {
                "$ref": "https://crew-health-check.invalid/schemas/pharmacy/common.schema.json#/$defs/SourcedValue"
            },
            registry=schema_registry(),
        )
        errors = sorted(e.message for e in validator.iter_errors(value))
        if errors:
            raise ConfigSchemaError(name, errors)
        sourced = cast(SourcedValue, value)
        _finite(sourced["value"])
        if sourced["unit"] != "percent" or sourced["basis"] != "sourced" or not sourced["source_ids"]:
            raise InputError("flight humidity requires sourced percent values")
        if set(sourced["source_ids"]) - sources:
            raise InputError("unknown flight humidity source")
    rh = cast(SourcedValue, data["rh_percent"])["value"]
    sd = cast(SourcedValue, data["rh_sd_percent"])["value"]
    if not 0 <= rh <= 100 or sd < 0:
        raise InputError("invalid flight humidity or standard uncertainty")
    station = next((z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit"), None)
    if station is None:
        raise InputError("station_kit missing")
    profile = next(
        (
            p
            for p in bundle.environment_profiles["profiles"]
            if p["profile_id"] == station["environment_profile_id"]
        ),
        None,
    )
    if profile is None:
        raise InputError("station profile missing")
    temp = profile["temperature"]
    return Condition(
        temp["setpoint_k"]["value"],
        (temp["nominal_max_k"]["value"] - temp["nominal_min_k"]["value"]) / 4,
        rh,
        sd,
    )


def rate_weighted_internal_rh(
    bundle: ConfigBundle, condition: Condition, physics: FamilyPhysics, package: PackageDoc, duration_s: float
) -> float:
    """Internal RH weighted by the instantaneous rate over the stored duration (spec 9.3).

    With x = B (RH_start - RH_out), r = exp(-duration/tau) and J the engine humidity integral,
    dJ/dx = tau exp(x r) (1 - r) exprel(x (1 - r)), so the weighted RH is
    RH_out + (RH_start - RH_out) (dJ/dx) / J. It is the derivative of ln(Theta/duration)
    with respect to B plus RH_out, reduces to the time mean at B = 0 and to the constant
    internal RH for sealed or non-exchanging packages.
    """
    moisture = package["moisture_model"]
    sealed = moisture["sealed_rh_percent"]
    if moisture["kind"] == "sealed_constant" and sealed is not None:
        return sealed["value"]
    initial = moisture["initial_internal_rh_percent"]
    start = condition.rh_percent if initial is None else initial["value"]
    tau = exchange_time_constant_s(package, condition.temperature_k)
    delta = start - condition.rh_percent
    if tau <= 0 or delta == 0:
        return condition.rh_percent
    relaxed = -math.expm1(-duration_s / tau)
    x = physics.b * delta
    numerics = bundle.settings["numerics"]
    integral = humidity_integral(
        x,
        duration_s,
        tau,
        rel_tol=numerics["series_relative_tolerance"]["value"],
        max_terms=int(numerics["series_max_terms"]["value"]),
    )
    if not math.isfinite(integral) or integral <= 0:
        raise ComputationError("humidity integral must be positive and finite")
    derivative = tau * math.exp(x * (1 - relaxed)) * relaxed * float(exprel(x * relaxed))
    weighted = condition.rh_percent + delta * derivative / integral
    if not math.isfinite(weighted):
        raise ComputationError("rate-weighted internal RH is nonfinite")
    return weighted


def _conversion_variance(
    bundle: ConfigBundle,
    condition: Condition,
    physics: FamilyPhysics,
    package: PackageDoc,
    duration_s: float,
) -> float:
    """The three 9.3 variance terms; the B term uses the rate-weighted internal RH."""
    reference = bundle.settings["reference_conditions"]
    x = (1 / reference["temperature_k"]["value"] - 1 / condition.temperature_k) / R
    d_rh = reference["relative_humidity_percent"]["value"] - rate_weighted_internal_rh(
        bundle, condition, physics, package, duration_s
    )
    return (
        (x * physics.ea_sd) ** 2
        + (d_rh * physics.b_sd) ** 2
        + (physics.ea * condition.temperature_sd_k / (R * condition.temperature_k**2)) ** 2
    )


def _dataset(path: Path, vector_id: str) -> dict[str, Any]:
    data = load_json(path)
    validate_instance(data, "pharmacy/test-vectors.schema.json", str(path))
    vectors = cast(dict[str, Any], data)["vectors"]
    matches = [v for v in vectors if v["vector_id"] == vector_id]
    if len(matches) != 1:
        raise InputError(f"expected exactly one vector {vector_id}")
    return cast(dict[str, Any], matches[0])


def _family(label: str) -> DoseFormFamily:
    text = label.lower()
    # Dataset terminology only. No implicit solid fallback for unknown forms.
    if any(word in text for word in ("cream", "ointment", "suppository")):
        return "semisolid"
    if "powder" in text:
        return "solid"
    if any(word in text for word in ("solution", "injection")):
        return "solution"
    if any(word in text for word in ("tablet", "capsule", "oral contraceptive")):
        return "solid"
    raise InputError(f"unrecognized Du dose form: {label}")


def _package_id(row: Mapping[str, Any], family: DoseFormFamily) -> str:
    if row["repackaged"] is True:
        return "repackaged_bag"
    return "original_bottle" if family == "solid" else "original_vial"


def _row_rate(row: Mapping[str, Any], arm: str, days: Sequence[float]) -> RateObservation:
    first, last = row[f"day_{days[0]:g}_{arm}"], row[f"day_{days[-1]:g}_{arm}"]
    if first is None or last is None:
        raise InputError(f"row {row['row_number']} missing {arm} endpoint")
    return two_point_rate(
        float(first["value"]),
        float(last["value"]),
        float(first["sd"]),
        float(last["sd"]),
        (days[-1] - days[0]) * SECONDS_PER_DAY,
    )


def _stat(name: str, value: float, unit: str = "1") -> CalibrationStatistic:
    if not math.isfinite(value):
        raise ComputationError(f"nonfinite calibration statistic {name}")
    return {"name": name, "value": value, "unit": unit}


def calibrate(bundle: ConfigBundle, vector_path: Path, flight_condition_path: Path) -> CalibrationResult:
    """Derive priors and the station multiplier, without modifying bundle or files.

    Inputs must follow the validated Du vector and config contracts. Also checks
    selection disjointness (except the suspension alias), family agreement and
    complete endpoint assays. Provenance contains config versions and input hashes.
    """
    vector = _dataset(vector_path, "V1")
    try:
        return _calibrate(bundle, vector, vector_path, flight_condition_path)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InputError(f"invalid calibration input: {exc}") from exc


def _calibrate(
    bundle: ConfigBundle, vector: dict[str, Any], vector_path: Path, flight_condition_path: Path
) -> CalibrationResult:
    days = [float(d) for d in vector["inputs"]["time_points_days"]]
    if len(days) < 2 or days != sorted(set(days)):
        raise InputError("calibration time points must be increasing and distinct")
    duration = (days[-1] - days[0]) * SECONDS_PER_DAY
    flight_condition = _flight_condition(bundle, flight_condition_path)
    priors = {p["dose_form_family"]: p for p in bundle.reaction_classes["total_rate_priors"]}
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    rows = {int(r["parsed"]["row_number"]): r["parsed"] for r in vector["expected"]["rows"]}
    if len(rows) != len(vector["expected"]["rows"]):
        raise InputError("duplicate Du row numbers")
    if "suspension" in priors:
        if (
            "solution" not in priors
            or priors["suspension"]["calibration"]["rows"] != priors["solution"]["calibration"]["rows"]
        ):
            raise InputError("suspension must reuse the solution calibration rows")
        if _ground_condition(priors["suspension"]) != _ground_condition(priors["solution"]):
            raise InputError("suspension must reuse the solution storage condition")
    selected: dict[int, DoseFormFamily] = {}
    for family in sorted(priors):
        if family == "suspension":
            continue
        selection = priors[family]["calibration"]
        if selection["vector_id"] != vector["vector_id"] or not selection["rows"]:
            raise InputError("calibration requires explicit rows of V1")
        for number in selection["rows"]:
            if number in selected:
                raise InputError("calibration rows must be disjoint between fitted families")
            selected[number] = family
    calibrated = []
    for number in sorted(rows):
        row = rows[number]
        family = _family(row["product_api"])
        if number in selected and selected[number] != family:
            raise InputError(f"calibration row {number} has wrong family")
        physics = _family_physics(bundle, family)
        condition = _ground_condition(priors[family])
        package_id = _package_id(row, family)
        ground = _row_rate(row, "control", days)
        flight = _row_rate(row, "flight", days)
        ground_factor = mean_rate_factor(bundle, packages[package_id], condition, physics, duration)
        flight_factor = mean_rate_factor(bundle, packages[package_id], flight_condition, physics, duration)
        calibrated.append(
            CalibratedRow(
                number,
                row["product_api"],
                family,
                package_id,
                ground,
                flight,
                ground_factor,
                flight_factor,
                ground.ln_per_s - math.log(ground_factor),
                _conversion_variance(bundle, condition, physics, packages[package_id], duration),
                flight.ln_per_s - ground.ln_per_s - math.log(flight_factor / ground_factor),
            )
        )
    if set(selected) - rows.keys():
        raise InputError("calibration names missing Du rows")
    training = [row for row in calibrated if row.number in selected]
    groups: dict[str, Sequence[float]] = {
        family: [r.ln_reference_rate for r in training if r.family == family]
        for family in sorted(set(selected.values()))
    }
    decision = bundle.settings["decision"]
    coverage = decision["upper_quantile"]["value"] - decision["lower_quantile"]["value"]
    if not math.isclose(decision["lower_quantile"]["value"], (1 - coverage) / 2):
        raise InputError("calibration requires a central decision interval")
    pooled = pooled_predictive(groups, coverage)
    versions = "; ".join(f"{name}={bundle.documents[name].version}" for name in sorted(bundle.documents))
    note = (
        "Pooled within-family variance, no product weighting or trimming. Family Ea/B are equal-weight "
        "mixtures over configured destructive thermal classes. Package mean rates use engine physics "
        "from day-13 package start to day 880; unknown repackaging uses the original-container rule. "
        "Nonpositive loss uses the median positive-truncated Gaussian assay likelihood; zero-noise "
        "nonpositive loss is an error. Normal sd matches the central Student-t predictive interval "
        "at decision coverage before independent spec 9.3 conversion variance is added in quadrature. "
        "Conversion uncertainty is averaged within family, not divided by n, since calibration "
        "conditions and chemistry priors are shared. Suspension is an alias, not a fourth fitted family. "
        f"Config versions: {versions}. Vector SHA256={canonical_sha256(load_json(vector_path))}. "
        f"Flight input SHA256={canonical_sha256(load_json(flight_condition_path))}."
    )
    common = [
        _stat("sample_count", len(training)),
        _stat("fitted_families", len(groups)),
        _stat("pooled_between_product_variance_ln", pooled.pooled_variance),
        _stat("predictive_df", pooled.degrees_of_freedom),
        _stat("coverage", coverage),
    ]
    output = []
    for original in bundle.reaction_classes["total_rate_priors"]:
        prior = copy.deepcopy(original)
        family = "solution" if prior["dose_form_family"] == "suspension" else prior["dose_form_family"]
        members = [row for row in training if row.family == family]
        conversion = mean(r.conversion_variance for r in members)
        sd = math.sqrt(pooled.normal_sd[family] ** 2 + conversion)
        physics = _family_physics(bundle, family)
        prior["ln_k_ref_per_s"].update({"mu": pooled.means[family], "sigma": sd, "note": note})
        statistics = [
            *common,
            _stat("family_sample_count", len(members)),
            _stat("family_mean_ln_k_ref_per_s", pooled.means[family], "ln(1/s)"),
            _stat("student_t_equivalent_normal_sd_ln", pooled.normal_sd[family]),
            _stat("reference_conversion_variance_ln", conversion),
            _stat("stored_normal_sd_ln", sd),
            _stat("ea_mean_j_per_mol", physics.ea, "J/mol"),
            _stat("ea_sd_j_per_mol", physics.ea_sd, "J/mol"),
            _stat("b_mean_per_percent_rh", physics.b, "1/percent"),
            _stat("b_sd_per_percent_rh", physics.b_sd, "1/percent"),
            _stat("censored_ground_rows", sum(r.ground.censored for r in members)),
        ]
        prior["calibration_provenance"] = {
            "basis": prior["ln_k_ref_per_s"]["basis"],
            "source_ids": list(vector["source_ids"]),
            "to_be_set_by": prior["ln_k_ref_per_s"]["to_be_set_by"],
            "note": note,
            "method": "pooled_within_family_student_t_predictive_v1",
            "vector_id": vector["vector_id"],
            "rows": [r.number for r in members],
            "statistics": statistics,
            "engine_version": __version__,
        }
        output.append(prior)
    residuals = [r.flight_residual for r in training]
    residual_fit = pooled_predictive({"flight": residuals}, coverage)
    location = mean(residuals)
    spread = residual_fit.normal_sd["flight"]
    flight_note = (
        note + " Flight residual = ln(k_flight/k_ground) minus ln(physics_flight/physics_ground). "
        "Location is the mean ln residual (the multiplier's median); spread is the sample sd with "
        "Student-t predictive multiplier sqrt(1+1/N), df=N-1, the exact predictive pivot for a normal "
        "sample. No held-out row enters either fit."
    )
    multiplier: Prior = {
        "family": "lognormal",
        "mu": location,
        "sigma": spread,
        "lower": None,
        "upper": None,
        "unit": "1",
        "basis": "derived",
        "source_ids": list(vector["source_ids"]),
        "to_be_set_by": None,
        "note": flight_note,
    }
    provenance: CalibrationProvenance = {
        "basis": "derived",
        "source_ids": list(vector["source_ids"]),
        "to_be_set_by": None,
        "note": flight_note,
        "method": "paired_physics_residual_mean_student_t_predictive_v2",
        "vector_id": vector["vector_id"],
        "rows": [r.number for r in training],
        "engine_version": __version__,
        "statistics": [
            _stat("sample_count", len(training)),
            _stat("residual_mean_ln", location),
            _stat("residual_median_ln", median(residuals)),
            _stat("residual_sample_sd_ln", math.sqrt(residual_fit.pooled_variance)),
            _stat("predictive_df", residual_fit.degrees_of_freedom),
            _stat("coverage", coverage),
            _stat("stored_normal_sd_ln", spread),
            _stat("flight_temperature_k", flight_condition.temperature_k, "K"),
            _stat("flight_temperature_sd_k", flight_condition.temperature_sd_k, "K"),
            _stat("flight_rh_percent", flight_condition.rh_percent, "percent"),
            _stat("flight_rh_sd_percent", flight_condition.rh_sd_percent, "percent"),
            _stat("censored_ground_rows", sum(r.ground.censored for r in training)),
            _stat("censored_flight_rows", sum(r.flight.censored for r in training)),
        ],
    }
    return CalibrationResult(tuple(output), multiplier, provenance, tuple(calibrated))


def write_calibration(
    config_dir: Path, vector_path: Path, flight_condition_path: Path, version: str
) -> CalibrationResult:
    """Recompute and write only owned prior/multiplier values, bumping both versions.

    Validate both complete documents and references before writing. Reuse of an old
    version raises InputError. IO failures raise ConfigLoadError. Config versions in
    provenance are the resulting versions, so an immediate rerun reproduces exactly.
    """
    bundle = load_bundle(config_dir)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise InputError("calibration version must be major.minor.patch")
    members = ("reaction-classes", "storage-zones")
    docs = dict(bundle.documents)
    for name in members:
        if tuple(map(int, version.split("."))) <= tuple(map(int, docs[name].version.split("."))):
            raise InputError("calibration write requires a newer document version")
        data = copy.deepcopy(docs[name].data)
        data["version"] = version
        docs[name] = document_from_json(data, docs[name].path)
    result = calibrate(ConfigBundle(docs), vector_path, flight_condition_path)
    docs["reaction-classes"].data["total_rate_priors"] = cast(JSONValue, list(result.priors))
    for zone in ConfigBundle(docs).zones["zones"]:
        if zone["zone_id"] not in {"station_kit", "transit_kit", "moon_base_pharmacy", "mars_base_pharmacy"}:
            continue
        zone["rate_multiplier"] = copy.deepcopy(result.multiplier)
        provenance = copy.deepcopy(result.multiplier_provenance)
        zone["rate_multiplier_calibration"] = provenance
        if zone["zone_id"] != "station_kit":
            for record in (zone["rate_multiplier"], provenance):
                record["basis"] = "team_default"
                record["to_be_set_by"] = "pharmacist and flight surgeon"
                record["note"] += (
                    " Du ISS prior reused: no surface data exist; "
                    "pharmacist and flight surgeon must set this prior."
                )
    for name in members:
        docs[name] = document_from_json(docs[name].data, docs[name].path)
    problems = reference_problems(ConfigBundle(docs))
    if problems:
        raise ConfigReferenceError("calibration", problems)
    for name in members:
        path = config_dir / f"{name}.json"
        try:
            path.write_text(
                json.dumps(docs[name].data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise ConfigLoadError(str(exc), str(path)) from exc
    return result


def main(argv: Sequence[str] | None = None) -> None:
    """CLI: deterministic human-readable per-row rates, priors and multiplier."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vector", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--flight-condition", type=Path, required=True)
    parser.add_argument("--write-version", help="write owned values with this newer document version")
    args = parser.parse_args(argv)
    result = (
        write_calibration(args.config, args.vector, args.flight_condition, args.write_version)
        if args.write_version
        else calibrate(load_bundle(args.config), args.vector, args.flight_condition)
    )
    for row in result.rows:
        print(
            f"row {row.number:02d} {row.label}: ground={row.ground.observed_per_s:.12g}/s "
            f"flight={row.flight.observed_per_s:.12g}/s censored={row.ground.censored}/{row.flight.censored} "
            f"ln_k_ref={row.ln_reference_rate:.12g} residual={row.flight_residual:.12g}"
        )
    for prior in result.priors:
        rate = prior["ln_k_ref_per_s"]
        print(f"{prior['dose_form_family']}: mu={rate['mu']:.15g} sigma={rate['sigma']:.15g}")
    print(
        f"station factor: median={math.exp(result.multiplier['mu']):.15g} "
        f"mu={result.multiplier['mu']:.15g} sigma={result.multiplier['sigma']:.15g}"
    )


if __name__ == "__main__":
    main()
