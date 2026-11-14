"""Prior-only deterministic approximation to V1, V6 and V2, not engine validation.

Run as a module with --output and --tolerance. Integrates over an approximate
normal log-Theta distribution, analytically marginalizing initial and final assay
noise. The mean exposure is the exact engine package integral. Its log variance
uses independent symmetric one-sd secants for Ea, B, temperature and ambient RH.
This does not build chemistry pathways, anchors, sensor histories or lot decisions.
"""

import argparse
import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.stats import binom, norm

from pharmacy.calibration import Condition, FamilyPhysics, calibrate, mean_rate_factor
from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.constants import SECONDS_PER_DAY
from pharmacy.errors import ComputationError, InputError
from pharmacy.types import TotalRatePrior


@dataclasses.dataclass(frozen=True)
class _Distribution:
    log_theta: float
    log_theta_sd: float
    initial: float
    initial_sd: float
    assay_sd: float


def _quantile(dist: _Distribution, probability: float, tolerance: float) -> float:
    """Gaussian mixture CDF, with the two independent assay errors marginalized."""
    if not 0 < probability < 1 or not 0 < tolerance < 1:
        raise InputError("probability and numerical tolerance must be inside (0,1)")
    if (
        not all(math.isfinite(v) for v in dataclasses.astuple(dist))
        or min(dist.initial, dist.initial_sd, dist.assay_sd, dist.log_theta_sd) <= 0
    ):
        raise InputError("finite log exposure and positive concentration/uncertainties required")
    tail = np.finfo(float).eps
    bound = float(norm.isf(tail / 2))

    def cdf(value: float) -> float:
        def integrand(z: float) -> float:
            log_theta = dist.log_theta + dist.log_theta_sd * z
            retention = 0.0 if log_theta > math.log(np.finfo(float).max) else math.exp(-math.exp(log_theta))
            sd = math.hypot(dist.initial_sd * retention, dist.assay_sd)
            return float(norm.cdf((value - dist.initial * retention) / sd) * norm.pdf(z))

        value, error = quad(integrand, -bound, bound, epsabs=tolerance, epsrel=tolerance)
        if not math.isfinite(value) or error > tolerance * max(1, abs(value)):
            raise ComputationError("offline predictive integral failed its error bound")
        return float(value)

    limit = bound * math.hypot(dist.initial_sd, dist.assay_sd)
    return float(brentq(lambda value: cdf(value) - probability, -limit, dist.initial + limit, xtol=tolerance))


def _physics(prior: TotalRatePrior) -> FamilyPhysics:
    provenance = prior["calibration_provenance"]
    assert provenance is not None
    stats = {s["name"]: s["value"] for s in provenance["statistics"]}
    return FamilyPhysics(
        stats["ea_mean_j_per_mol"],
        stats["ea_sd_j_per_mol"],
        stats["b_mean_per_percent_rh"],
        stats["b_sd_per_percent_rh"],
    )


def _distribution(
    bundle: ConfigBundle,
    prior: TotalRatePrior,
    package_id: str,
    condition: Condition,
    days: float,
    zone_mu: float,
    zone_sd: float,
    initial: float,
    initial_sd: float,
    assay_sd: float,
) -> _Distribution:
    physics = _physics(prior)
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == package_id)
    duration = days * SECONDS_PER_DAY

    def log_factor(c: Condition, p: FamilyPhysics) -> float:
        return math.log(mean_rate_factor(bundle, package, c, p, duration))

    mu = prior["ln_k_ref_per_s"]["mu"] + log_factor(condition, physics) + math.log(duration) + zone_mu
    variance = prior["ln_k_ref_per_s"]["sigma"] ** 2 + zone_sd**2
    for field, sd in (("ea", physics.ea_sd), ("b", physics.b_sd)):
        high = dataclasses.replace(physics, **{field: getattr(physics, field) + sd})
        low = dataclasses.replace(physics, **{field: getattr(physics, field) - sd})
        variance += ((log_factor(condition, high) - log_factor(condition, low)) / 2) ** 2
    for field, sd in (("temperature_k", condition.temperature_sd_k), ("rh_percent", condition.rh_sd_percent)):
        high_condition = dataclasses.replace(condition, **{field: getattr(condition, field) + sd})
        low_condition = dataclasses.replace(condition, **{field: getattr(condition, field) - sd})
        variance += ((log_factor(high_condition, physics) - log_factor(low_condition, physics)) / 2) ** 2
    return _Distribution(mu, math.sqrt(variance), initial, initial_sd, assay_sd)


def _coverage_row(
    dataset: str,
    number: int,
    label: str,
    measured: float,
    dist: _Distribution,
    lower: float,
    upper: float,
    tolerance: float,
) -> dict[str, Any]:
    lo, mid, hi = [_quantile(dist, p, tolerance) for p in (lower, 0.5, upper)]
    return {
        "dataset": dataset,
        "row": number,
        "label": label,
        "measured": measured,
        "assay_sd": dist.assay_sd,
        "lower": lo,
        "median": mid,
        "upper": hi,
        "covered": lo <= measured <= hi,
    }


def _run(tolerance: float) -> dict[str, Any]:
    bundle = load_bundle()
    vector_path = Path("tests/data/vectors/validation-du2011.json")
    result = calibrate(bundle, vector_path, Path("data/pharmacy/calibration/du2011-flight-condition.json"))
    priors = {p["dose_form_family"]: p for p in result.priors}
    stats = {s["name"]: s["value"] for s in result.multiplier_provenance["statistics"]}
    flight = Condition(
        stats["flight_temperature_k"],
        stats["flight_temperature_sd_k"],
        stats["flight_rh_percent"],
        stats["flight_rh_sd_percent"],
    )
    station_zone = next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit")
    profile = next(
        p
        for p in bundle.environment_profiles["profiles"]
        if p["profile_id"] == station_zone["environment_profile_id"]
    )
    rh = profile["relative_humidity"]
    station = dataclasses.replace(
        flight,
        rh_percent=rh["setpoint_percent"]["value"],
        rh_sd_percent=(rh["nominal_max_percent"]["value"] - rh["nominal_min_percent"]["value"]) / 4,
    )
    data = json.loads(vector_path.read_text())["vectors"]
    vector = next(v for v in data if v["vector_id"] == "V1")
    parsed = {r["parsed"]["row_number"]: r["parsed"] for r in vector["expected"]["rows"]}
    training = set(result.multiplier_provenance["rows"])
    lower = bundle.settings["decision"]["lower_quantile"]["value"]
    upper = bundle.settings["decision"]["upper_quantile"]["value"]
    zone_mu, zone_sd = result.multiplier["mu"], result.multiplier["sigma"]
    days = vector["inputs"]["time_points_days"]
    coverage = []
    for row in result.rows:
        if row.number % 2 or row.number in training:
            continue
        p, prior = parsed[row.number], priors[row.family]
        ground = Condition(
            prior["temperature_k"]["value"],
            prior["temperature_sd_k"]["value"],
            prior["rh_percent"]["value"],
            prior["rh_sd_percent"]["value"],
        )
        initial = p[f"day_{days[0]}_control"]
        for arm, condition, mu, sd in (("control", ground, 0.0, 0.0), ("flight", flight, zone_mu, zone_sd)):
            measured = p[f"day_{days[-1]}_{arm}"]
            dist = _distribution(
                bundle,
                prior,
                row.package_id,
                condition,
                days[-1] - days[0],
                mu,
                sd,
                initial["value"],
                initial["sd"],
                measured["sd"],
            )
            coverage.append(
                _coverage_row(
                    f"V1-{arm}", row.number, row.label, measured["value"], dist, lower, upper, tolerance
                )
            )
    v6 = next(
        v
        for v in json.loads(Path("tests/data/vectors/validation-iss-short.json").read_text())["vectors"]
        if v["vector_id"] == "V6"
    )
    # Class-prior basis in the existing validation report: rows 2 to 6. Rows 7 to 9
    # use a computed amide anchor; aspirin (row 1) is the study's calibration anchor.
    for number, row_data in enumerate(v6["expected"]["rows"], 1):
        if number not in (2, 3, 4, 5, 6):
            continue
        p = row_data["parsed"]
        assay_sd = 100 * p["measured_mean_sd_sd_mg"]["sd"] / p["label"]["value"]
        dist = _distribution(
            bundle,
            priors["solid"],
            "repackaged_bag",
            station,
            v6["inputs"]["storage_days"],
            zone_mu,
            zone_sd,
            100,
            1.5,
            assay_sd,
        )
        coverage.append(
            _coverage_row(
                "V6-class-prior", number, p["product"], p["percent_lc"], dist, lower, upper, tolerance
            )
        )
    summaries = []
    for dataset in sorted({r["dataset"] for r in coverage}):
        cases = [r for r in coverage if r["dataset"] == dataset]
        n, k = len(cases), sum(r["covered"] for r in cases)
        required = int(binom.ppf(lower, n, upper - lower))
        summaries.append(
            {
                "dataset": dataset,
                "n": n,
                "covered": k,
                "required": required,
                "passed": k >= required,
                "misses": [r["row"] for r in cases if not r["covered"]],
            }
        )
    counts = []
    v2 = next(v for v in data if v["vector_id"] == "V2")
    formulation = {8: 7, 12: 11, 26: 25, 36: 35}  # Spec 15.5, one count per formulation.
    for day, documented in zip(days, v2["expected"]["rows"], strict=True):
        predicted_failures, measured_failures = set(), set()
        for row in result.rows:
            p = parsed[row.number]
            initial = p[f"day_{days[0]}_control"]
            if day == days[0]:
                prediction = initial["value"]
            else:
                # Central rate prediction, before final measurement noise. Initial
                # assay uncertainty shifts the exact median slightly; not integrated.
                prior = priors[row.family]
                package = next(p for p in bundle.packages["packages"] if p["package_id"] == row.package_id)
                duration = (day - days[0]) * SECONDS_PER_DAY
                factor = mean_rate_factor(bundle, package, flight, _physics(prior), duration)
                log_theta = prior["ln_k_ref_per_s"]["mu"] + math.log(factor) + math.log(duration) + zone_mu
                prediction = initial["value"] * math.exp(-math.exp(log_theta))
            limit = p["usp_range_percent"]["low"]
            number = formulation.get(row.number, row.number)
            if prediction < limit:
                predicted_failures.add(number)
            if p[f"day_{day}_flight"] is not None and p[f"day_{day}_flight"]["value"] < limit:
                measured_failures.add(number)
        counts.append(
            {
                "day": day,
                "predicted": len(predicted_failures),
                "observed_grid": len(measured_failures),
                "documented": documented["parsed"]["failed_flight"],
                "predicted_formulations": sorted(predicted_failures),
                "observed_formulations": sorted(measured_failures),
            }
        )
    return {
        "method": __doc__,
        "absolute_tolerance": tolerance,
        "rows": coverage,
        "summaries": summaries,
        "v2": counts,
        "v6_excluded": {
            "1": "aspirin calibration anchor",
            "7": "computed basis: melatonin",
            "8": "computed basis: zolpidem",
            "9": "computed basis: modafinil",
        },
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, required=True)
    args = parser.parse_args()
    report = _run(args.tolerance)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for r in report["rows"]:
        print(
            f"{r['dataset']} {r['row']:02d} {r['label']}: {r['measured']:.3f} "
            f"[{r['lower']:.3f}, {r['upper']:.3f}] {'covered' if r['covered'] else 'MISS'}"
        )
    for summary in report["summaries"]:
        print(summary)
    for count in report["v2"]:
        print("V2", count)


if __name__ == "__main__":
    _main()
