# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Virtual stability studies VS-1 to VS-5 (spec 15.8, marker slow). Pass rules are the spec's,
fixed before code; a failed rule is a finding reported with its replicates in
tests/virtual/report.json, never a reason to change configuration or the rule.

Replicate counts come from the fixture (the spec's 500) and may be lowered for a local run
with B3_VS1_REPLICATES ... B3_VS5_REPLICATES; the report records the count used. The truth
spot check runs the spec's five replicates.

Interpretations where the spec is silent are requests in docs/requests/B3.md: VS truth with
z = 0 (R22), single-pathway products for VS-1 (R23), the VS-2 prediction interval and the
fit conditions (R15), the kinds select_model compares (R14), the VS-4 templates and the
validated-range premise of VS-3 and VS-4 (R24).
"""

import copy
import dataclasses
import functools
import math
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.types import LotDoc, RateLawKind, ScenarioDoc, StabilityModelDoc, iso_to_seconds, seconds_to_iso
from tests.oracle.draws import EA, LN_K, Z_RH, Z_T, B, OracleDraw
from tests.oracle.lot_truth import lot_true_potency
from tests.oracle.precision import to_float
from tests.oracle.truth import PackageModel, integrate_truth, integrate_truth_mp
from tests.scenarios import builders
from tests.scenarios.bundles import configured_lot, lot_package_ids, stability_model, write_test_bundle
from tests.validation.products import v1_cases
from tests.virtual.studies import (
    FITTABLE_KINDS,
    SECONDS_PER_MONTH,
    Truth,
    chamber_truth,
    design,
    draw_truth,
    fit_kind,
    generator,
    interval_contains,
    load_designs,
    observation_digest,
    potency,
    predicted_potency_interval,
    pull_times_s,
    replicate_count,
    replicate_seed,
    run_study,
    study_map,
    true_rate,
    write_report,
)

if TYPE_CHECKING:
    from pharmacy.fit import StudyFit, StudyObservation

pytestmark = pytest.mark.slow

DESIGNS = load_designs()
DAY = 86400.0
STREAM_VS5_ASSAY = 3
CLOSED_LOOP_ZONES = (
    "ground_storage",
    "transit_kit",
    "moon_base_pharmacy",
    "station_kit",
    "mars_base_pharmacy",
)
PROFILES = ("station", "transit", "moon", "mars")
VS2_TRUTH_KINDS: tuple[RateLawKind, ...] = ("F1", "A2", "R3", "D3")
VS2_TEMPERATURE_K, VS2_RH_PERCENT, VS2_MONTHS = 298.15, 60.0, 24.0
TRUTH_SPOT_CHECKS = 5


def _product_entry(role: str) -> dict[str, Any]:
    entry: dict[str, Any] = next(p for p in DESIGNS["products"] if p["role"] == role)
    return entry


@pytest.fixture(scope="module")
def bundle_vs(tmp_path_factory: pytest.TempPathFactory) -> ConfigBundle:
    """The configured bundle plus the validation-row products the fixture names."""
    rows = {p["validation_row"] for p in DESIGNS["products"] if p["product_source"] == "validation_row"}
    products = [c.product for c in v1_cases("control", all_rows=True) if c.row_number in rows]
    return write_test_bundle(tmp_path_factory.mktemp("vs-bundle"), products=products)


def _ingredient(bundle: ConfigBundle, product_id: str) -> tuple[str, str, float]:
    product = next(p for p in bundle.formulary["products"] if p["product_id"] == product_id)
    ingredient = product["ingredients"][0]
    lower = ingredient["specification"]["lower_percent"]["value"]
    return ingredient["ingredient_id"], product["default_package_id"], lower


def _fraction_rule(hits: int, n: int, low: float, high: float) -> dict[str, Any]:
    fraction = hits / n if n else None
    passed = fraction is not None and low <= fraction <= high
    return {"hits": hits, "n": n, "fraction": fraction, "band": [low, high], "passed": passed}


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


# =========================================================================================
# Truth integrator spot check (spec 15.8: mpmath at 50 digits checks five replicates)
# =========================================================================================


def test_vs_truth_float_agrees_with_mpmath(bundle_vs: ConfigBundle) -> None:
    """Five replicates of the accelerated ICH chamber: the float trapezoid (at the substeps
    the halving reached) equals the 50-digit trapezoid at the same substeps within the
    float rounding bound (64 unit roundoffs per sub-step: rate, relaxation and compensated
    sum), and the halving error is below 1e-6 (the spec's truth accuracy). The spec fixes no
    tolerance for this spot check; a first run against 1e-12 measured 1.6e-11 over 4380
    grid intervals, which is rounding, and the bound is now derived instead of chosen."""
    product_id = _product_entry("closed_loop")["product_id"]
    ingredient_id, package_id, spec_lower = _ingredient(bundle_vs, product_id)
    ich = design(DESIGNS, "ich")
    condition = ich["conditions"][-1]
    packages = {p["package_id"]: p for p in bundle_vs.packages["packages"]}
    package = PackageModel.from_doc(packages[package_id])
    rows: list[dict[str, Any]] = []
    for r in range(TRUTH_SPOT_CHECKS):
        seed = replicate_seed(DESIGNS, r)
        truth = draw_truth(bundle_vs, product_id, ingredient_id, [package_id], ["ground_storage"], seed)
        tm = truth.thermal(bundle_vs)
        pulls = pull_times_s(ich, condition, truth, tm, spec_lower)
        t, temps, rhs = chamber_truth(bundle_vs, ich, condition, pulls[-1], seed)
        result = integrate_truth(tm, t, temps, rhs, package)
        exact = integrate_truth_mp(tm, t, temps, rhs, package, substeps=result.substeps)
        rel = abs(result.theta_at[-1] - to_float(exact)) / to_float(exact)
        bound = (len(t) - 1) * result.substeps * 64 * sys.float_info.epsilon
        rows.append(
            {
                "replicate": r,
                "theta_float": result.theta_at[-1],
                "theta_mp": str(exact),
                "relative_difference": rel,
                "rounding_bound": bound,
                "halving_relative_error": result.halving_relative_error,
                "substeps": result.substeps,
                "intervals": len(t) - 1,
            }
        )
    write_report(
        "VS truth integrator",
        {"replicates": TRUTH_SPOT_CHECKS, "condition": condition["condition_id"], "rows": rows},
    )
    assert all(row["relative_difference"] <= row["rounding_bound"] for row in rows), rows
    assert all(row["halving_relative_error"] < 1e-6 for row in rows), rows


# =========================================================================================
# VS-1 Parameter recovery
# =========================================================================================


def _vs1_replicate(bundle: ConfigBundle, product_id: str, design_id: str, r: int) -> dict[str, Any]:
    ingredient_id, package_id, spec_lower = _ingredient(bundle, product_id)
    study_design = design(DESIGNS, design_id)
    package = package_id if study_design["package"] == "declared" else None
    seed = replicate_seed(DESIGNS, r)
    truth = draw_truth(bundle, product_id, ingredient_id, [package_id], ["ground_storage"], seed)
    target = truth.single_pathway(bundle)
    study = run_study(bundle, study_design, truth, package, seed, spec_lower)
    row: dict[str, Any] = {
        "replicate": r,
        "halving_error": study.halving_relative_error,
        "error": None,
        "inside": {},
        "z": {},
    }
    try:
        fit = fit_kind(bundle, study.observations, truth.kind, truth)
    except Exception as error:
        row["error"] = f"replicate {r}: {type(error).__name__}: {error}"
        return row
    for name, value in zip((LN_K, EA, B), target, strict=True):
        row["inside"][name], row["z"][name] = interval_contains(fit, name, value)
    return row


@pytest.mark.parametrize("design_id", ["ich", "isoconversion"])
@pytest.mark.parametrize("role", ["vs1_solid", "vs1_solution", "vs1_semisolid"])
def test_vs1_parameter_recovery(bundle_vs: ConfigBundle, role: str, design_id: str) -> None:
    """Each 95% interval of (ln k*, Ea, B) contains the truth in a fraction within
    [0.921, 0.979]; the mean of (estimate - truth)/se lies within +-0.134. A fit that raises
    counts as a miss and is listed."""
    product_id = _product_entry(role)["product_id"]
    n = replicate_count(DESIGNS, "vs1")
    names = (LN_K, EA, B)
    hits = dict.fromkeys(names, 0)
    zs: dict[str, list[float]] = {name: [] for name in names}
    failures: list[str] = []
    worst = 0.0
    rows = study_map(
        functools.partial(_vs1_replicate, bundle_vs, product_id, design_id),
        list(range(n)),
        label=f"VS-1 {role} {design_id}",
    )
    for row in rows:
        worst = max(worst, row["halving_error"])
        if row["error"] is not None:
            failures.append(row["error"])
            continue
        for name in names:
            hits[name] += int(row["inside"][name])
            zs[name].append(row["z"][name])
    rules = {name: _fraction_rule(hits[name], n, 0.921, 0.979) for name in names}
    means = {name: statistics.fmean(zs[name]) if zs[name] else math.nan for name in names}
    report = {
        "replicates": n,
        "product_id": product_id,
        "design_id": design_id,
        "kind": "true kind of the prior model",
        "coverage": rules,
        "rows": rows,
        "mean_standardized_error": {k: _finite(v) for k, v in means.items()},
        "mean_band": [-0.134, 0.134],
        "fit_failures": failures,
        "max_truth_halving_relative_error": worst,
    }
    write_report(f"VS-1 {role} {design_id}", report)
    assert worst < 1e-6, f"truth step-halving error {worst} not below 1e-6"
    problems = [f"{k}: {v['fraction']} outside {v['band']}" for k, v in rules.items() if not v["passed"]]
    problems += [f"{k}: mean z {m} outside +-0.134" for k, m in means.items() if not abs(m) <= 0.134]
    assert not problems, "; ".join(problems) + (f"; fit failures: {failures[:3]}" if failures else "")


# =========================================================================================
# VS-2 Model selection
# =========================================================================================


def _select(
    bundle: ConfigBundle, observations: Sequence[StudyObservation], truth: Truth
) -> tuple[StudyFit | None, list[str]]:
    from pharmacy.fit import select_model

    fits, errors = [], []
    for kind in FITTABLE_KINDS:
        try:
            fits.append(fit_kind(bundle, observations, kind, truth))
        except Exception as error:
            errors.append(f"{kind}: {type(error).__name__}: {error}")
    return (select_model(fits) if fits else None), errors


def _vs2_replicate(bundle: ConfigBundle, task: tuple[RateLawKind, int]) -> dict[str, Any]:
    kind, r = task
    product_id = _product_entry("closed_loop")["product_id"]
    ingredient_id, package_id, spec_lower = _ingredient(bundle, product_id)
    seed = replicate_seed(DESIGNS, r)
    truth = draw_truth(bundle, product_id, ingredient_id, [package_id], ["ground_storage"], seed, kind=kind)
    study = run_study(bundle, design(DESIGNS, "isoconversion"), truth, None, seed, spec_lower)
    selected, errors = _select(bundle, study.observations, truth)
    row: dict[str, Any] = {
        "truth": kind,
        "replicate": r,
        "fit_errors": errors,
        "selected": None if selected is None else selected.rate_law_kind,
    }
    if selected is None or selected.rate_law_kind == kind:
        return row
    horizon = VS2_MONTHS * SECONDS_PER_MONTH
    theta = true_rate(truth.thermal(bundle), VS2_TEMPERATURE_K, VS2_RH_PERCENT) * horizon
    value = potency(kind, truth.draw.c0_percent, theta)
    low, high = predicted_potency_interval(selected, VS2_TEMPERATURE_K, VS2_RH_PERCENT, horizon, seed)
    row.update(true_percent=value, interval=[low, high], inside=low <= value <= high, below=value < low)
    return row


def test_vs2_model_selection(bundle_vs: ConfigBundle) -> None:
    """Decision 7: bound the unsafe lower-tail misses; report two-sided coverage and confusion."""
    n = replicate_count(DESIGNS, "vs2")
    tasks = [(kind, r) for r in range(n) for kind in VS2_TRUTH_KINDS]
    rows = study_map(functools.partial(_vs2_replicate, bundle_vs), tasks, label="VS-2")
    confusion: dict[str, Counter[str]] = {k: Counter() for k in VS2_TRUTH_KINDS}
    for row in rows:
        if row["selected"] is not None:
            confusion[row["truth"]][row["selected"]] += 1
    no_selection = [row for row in rows if row["selected"] is None]
    missed = [row for row in rows if row["selected"] is not None and row["selected"] != row["truth"]]
    m = len(missed)
    below = sum(row["below"] for row in missed)
    inside = sum(row["inside"] for row in missed)
    bound = 0.025 + 3 * math.sqrt(0.025 * (1 - 0.025) / m) if m else None
    passed = not no_selection and (m == 0 or below / m <= cast(float, bound))
    report = {
        "replicates_per_kind": n,
        "kinds_compared": list(FITTABLE_KINDS),
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "misselected": m,
        "inside_prediction_interval": inside,
        "two_sided_coverage": inside / m if m else None,
        "below_prediction_interval": below,
        "maximum_lower_tail_fraction": bound,
        "lower_tail_fraction": below / m if m else None,
        "applicability": "evaluated" if m else "not applicable: no misselections",
        "no_selection": no_selection,
        "rows": rows,
        "passed": passed,
    }
    write_report("VS-2", report)
    assert passed, report


# =========================================================================================
# VS-3 to VS-5 Closed loop
# =========================================================================================


@functools.cache
def closed_loop_model(r: int) -> tuple[Truth, StabilityModelDoc, float]:
    """Replicate r: the truth, the draft measured model from the ICH plus isoconversion study
    (fit every kind, select_model, model_document) and the study truth's halving error."""
    from pharmacy.fit import model_document

    base = load_bundle()
    lot = configured_lot(DESIGNS["closed_loop"]["lot_id"])
    product_id = lot["product_id"]
    ingredient_id = DESIGNS["closed_loop"]["ingredient_id"]
    assay = next(a for a in lot["initial_assay"] if a["ingredient_id"] == ingredient_id)
    seed = replicate_seed(DESIGNS, r)
    package_ids = sorted({p["package_id"] for p in lot["package_history"]})
    initial = (assay["mean_percent"]["value"], assay["sd_percent"]["value"])
    truth = draw_truth(base, product_id, ingredient_id, package_ids, CLOSED_LOOP_ZONES, seed, initial=initial)
    product = next(p for p in base.formulary["products"] if p["product_id"] == product_id)
    spec_lower = product["ingredients"][0]["specification"]["lower_percent"]["value"]
    ich = run_study(base, design(DESIGNS, "ich"), truth, product["default_package_id"], seed, spec_lower)
    iso = run_study(base, design(DESIGNS, "isoconversion"), truth, None, seed, spec_lower)
    observations = [*ich.observations, *iso.observations]
    selected, errors = _select(base, observations, truth)
    if selected is None:
        raise AssertionError(f"replicate {r}: no kind could be fitted: {errors}")
    digest = observation_digest(observations)
    document = model_document(
        selected, f"vs3_r{r:04d}", product_id, ingredient_id, None, digest, ["MDS01", "MDS14"]
    )
    return truth, document, max(ich.halving_relative_error, iso.halving_relative_error)


def _flat(draw: OracleDraw) -> OracleDraw:
    """The truth draw with the extrapolation z set to 0 (request B3-R22)."""
    values = {pid: {**v, Z_T: 0.0, Z_RH: 0.0} for pid, v in draw.pathway_values.items()}
    return dataclasses.replace(draw, pathway_values=values)


@dataclasses.dataclass(frozen=True, slots=True)
class LoopReplicate:
    r: int
    bundle: ConfigBundle
    simulated: Any
    start_s: float
    lot: LotDoc
    truth_at: dict[float, float]
    halving_relative_error: float


def prepare_replicate(
    r: int, directory: Path, scenario: ScenarioDoc, lot: LotDoc, days: Sequence[float]
) -> LoopReplicate:
    """Replicate r of a closed loop: the measured model in a test bundle (with ``lot`` added
    when it is not configured), the mission simulated with seed 20261114 + r, and the true
    potency of ``lot`` at ``days`` from mission start (from the simulator's truth)."""
    from pharmacy.envsim import simulate_scenario

    truth, document, study_error = closed_loop_model(r)
    configured = {x["lot_id"] for x in load_bundle().lots["lots"]}
    extra = [] if lot["lot_id"] in configured else [lot]
    bundle = write_test_bundle(directory / f"bundle-{r:04d}", stability_models=[document], extra_lots=extra)
    mission = copy.deepcopy(scenario)
    mission["mission"]["seed"] = replicate_seed(DESIGNS, r)
    simulated = simulate_scenario(mission, bundle.environment_profiles, bundle.zones)
    start = iso_to_seconds(mission["mission"]["start_utc"])
    times = sorted({start + d * DAY for d in days})
    zone_truth = {
        z["zone_id"]: (
            *builders.truth_values(simulated, z["zone_id"], "temperature_k"),
            builders.truth_values(simulated, z["zone_id"], "relative_humidity")[1],
        )
        for z in mission["zones"]
    }
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    true = lot_true_potency(
        truth.model, _flat(truth.draw), lot, zone_truth, zones, packages, bundle.settings, times
    )
    truth_at = dict(zip(times, true.potency_total_percent, strict=True))
    return LoopReplicate(
        r, bundle, simulated, start, lot, truth_at, max(study_error, true.halving_relative_error)
    )


def simulated_assay(rep: LoopReplicate, day: float) -> dict[str, Any]:
    """One onboard assay of the truth at ``day`` with the lot's configured assay sd."""
    ingredient_id = DESIGNS["closed_loop"]["ingredient_id"]
    sd = next(a for a in rep.lot["initial_assay"] if a["ingredient_id"] == ingredient_id)["sd_percent"][
        "value"
    ]
    t = rep.start_s + day * DAY
    noise = float(generator(replicate_seed(DESIGNS, rep.r), STREAM_VS5_ASSAY).standard_normal())
    return {
        "assay_id": f"vs5-r{rep.r:04d}",
        "lot_id": rep.lot["lot_id"],
        "ingredient_id": ingredient_id,
        "measured_at_utc": seconds_to_iso(t),
        "measured_at_s": t,
        "measurand": "total",
        "value_percent": rep.truth_at[t] + sd * noise,
        "sd_percent": sd,
        "site": "onboard",
        "method": "virtual assay (B3, spec 15.8 VS-5)",
        "laboratory": "virtual",
        "note": "simulated from the truth",
    }


def engine_row(rep: LoopReplicate, day: float, assays: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """The engine's estimate of the lot at ``day`` from the simulator's readings."""
    from pharmacy import exposure, pipeline
    from tests.oracle.draws import distance_humidity, distance_temperature, measured_margin
    from tests.oracle.microclimate import internal_rh_trace

    t = rep.start_s + day * DAY
    inputs = cast(dict[str, Any], builders.engine_inputs(rep.simulated, seconds_to_iso(t)))
    inputs["assays"] = [*inputs["assays"], *assays]
    series, assay_obs, inspections = pipeline.validate_inputs(rep.bundle, cast(Any, inputs))
    ingredient_id = DESIGNS["closed_loop"]["ingredient_id"]
    model = stability_model(rep.bundle, rep.lot["product_id"], ingredient_id, lot_package_ids(rep.lot, t))
    estimate = pipeline.estimate_potency(
        rep.bundle,
        rep.lot["lot_id"],
        ingredient_id,
        inputs["as_of_utc"],
        series,
        assay_obs,
        inspections,
        model=model,
    )
    zones = {z["zone_id"]: z for z in rep.bundle.zones["zones"]}
    packages = {p["package_id"]: p for p in rep.bundle.packages["packages"]}
    history = exposure.build_lot_exposure(
        rep.lot, zones, packages, {z: [s for s in series if s.zone_id == z] for z in zones}, t
    )
    trace = internal_rh_trace(history.segments, packages)
    history_outside = any(
        distance_temperature(p, segment.temperature_k, measured_margin(p, rep.bundle.settings)) > 0
        or distance_humidity(p, [trace.rh_in_start_high[i], trace.rh_in_end_high[i]]) > 0
        for i, segment in enumerate(history.segments)
        for p in model.pathways
        if p.driver == "thermal"
    )
    median = next(q.value for q in estimate.quantiles if q.probability == 0.5)
    interval = estimate.coverage_interval
    truth = rep.truth_at[t]
    return {
        "replicate": rep.r,
        "day": day,
        "truth": truth,
        "median": median,
        "low": interval.low,
        "high": interval.high,
        "probability": interval.probability,
        "covered": interval.low <= truth <= interval.high,
        "outside_validated_range": "outside_validated_range" in estimate.flags,
        "history_outside_validated_range": history_outside,
        "width": interval.high - interval.low,
        "assay_ids_used": list(estimate.assay_ids_used),
        "estimate_basis": estimate.estimate_basis,
    }


def _median_bias_rule(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Median of (engine median - truth) within 3 standard errors of 0; the standard error of
    a sample median is taken as sqrt(pi/2) s / sqrt(n)."""
    errors = [row["median"] - row["truth"] for row in rows]
    if len(errors) < 2:
        return {"n": len(errors), "median_error": None, "se": None, "passed": False}
    se = math.sqrt(math.pi / 2) * statistics.stdev(errors) / math.sqrt(len(errors))
    median = statistics.median(errors)
    return {"n": len(errors), "median_error": median, "se": se, "passed": abs(median) <= 3 * se}


def _days(key: str) -> list[float]:
    return [float(d) for d in DESIGNS["closed_loop"][key]["value"]]


def _loop_replicate(directory: Path, r: int) -> tuple[list[dict[str, Any]], float]:
    lot = configured_lot(DESIGNS["closed_loop"]["lot_id"])
    rep = prepare_replicate(r, directory, builders.load_demo(), lot, _days("assessment_days"))
    return [engine_row(rep, d) for d in _days("assessment_days")], rep.halving_relative_error


def test_vs3_closed_loop(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Before day 180 the 90% interval contains the truth in [0.86, 0.94]; after the 45 C
    excursion the fraction is at least 0.86; every history leaving the validated
    range must carry outside_validated_range; the median of (engine median - truth) lies within 3 standard
    errors of 0; truth halving errors below 1e-6."""
    directory = tmp_path_factory.mktemp("vs3")
    n = replicate_count(DESIGNS, "vs3")
    excursion = float(DESIGNS["closed_loop"]["excursion_day"]["value"])
    completed = study_map(functools.partial(_loop_replicate, directory), list(range(n)), label="VS-3")
    rows = [row for result, _ in completed for row in result]
    worst = max(error for _, error in completed)
    before = [row for row in rows if row["day"] < excursion]
    after = [row for row in rows if row["day"] > excursion]
    rule_before = _fraction_rule(sum(row["covered"] for row in before), len(before), 0.86, 0.94)
    rule_after = _fraction_rule(sum(row["covered"] for row in after), len(after), 0.86, 1.0)
    outside = [row for row in rows if row["history_outside_validated_range"]]
    flagged: dict[str, Any] = {
        "rule": "every history leaving the validated range carries outside_validated_range",
        "flagged": sum(row["outside_validated_range"] for row in outside),
        "n": len(outside),
        "flagged_before_excursion": sum(row["outside_validated_range"] for row in before),
        "passed": all(row["outside_validated_range"] for row in outside),
    }
    bias = _median_bias_rule(rows)
    write_report(
        "VS-3",
        {
            "replicates": n,
            "before_excursion": rule_before,
            "after_excursion": rule_after,
            "validated_range_flag": flagged,
            "median_bias": bias,
            "max_truth_halving_relative_error": worst,
            "rows": rows,
        },
    )
    assert worst < 1e-6, f"truth step-halving error {worst} not below 1e-6"
    rules = {"before": rule_before, "after": rule_after, "flag": flagged, "median_bias": bias}
    failed = {name: rule for name, rule in rules.items() if not rule["passed"]}
    assert not failed, failed


def _profile_scenario(profile_id: str, days: float, seed: int) -> ScenarioDoc:
    """A ``days``-day history of one profile's zone with its templates: the hot thermal
    failure at day 180 and an SPE at day 250 (the Mars surface event on Mars, the August 1972
    event behind 20 g/cm2 elsewhere; request B3-R24)."""
    scenario = builders.self_test_scenario(load_bundle(), days=days, seed=seed, profiles=(profile_id,))
    zone_id = builders.PROFILE_ZONES[profile_id]
    start = iso_to_seconds(scenario["mission"]["start_utc"])
    spe = "mars_surface_sep_2017" if profile_id == "mars" else "aug1972_behind_20_g_cm2"
    events: list[dict[str, Any]] = [
        {
            "event_id": f"vs4-tcs-{profile_id}",
            "kind": "thermal_control_failure",
            "zone_id": zone_id,
            "start_utc": seconds_to_iso(start + 180 * DAY),
            "duration_s": None,
            "template_id": "tcs_failure_hot",
            "sensor_ids": [],
            "shielding_scale": None,
            "note": "VS-4 template event",
        },
        {
            "event_id": f"vs4-spe-{profile_id}",
            "kind": "solar_particle_event",
            "zone_id": zone_id,
            "start_utc": seconds_to_iso(start + 250 * DAY),
            "duration_s": None,
            "template_id": spe,
            "sensor_ids": [],
            "shielding_scale": 1.0,
            "note": "VS-4 template event",
        },
    ]
    return builders.with_events(scenario, events)


def _profile_lot(profile_id: str, scenario: ScenarioDoc) -> LotDoc:
    """The closed-loop lot made at mission start and kept in the profile's zone."""
    lot = configured_lot(DESIGNS["closed_loop"]["lot_id"])
    start = scenario["mission"]["start_utc"]
    lot["lot_id"] = f"lot-vs4-{profile_id}"
    lot["manufactured_at_utc"] = start
    lot["custody"] = [{"zone_id": builders.PROFILE_ZONES[profile_id], "from_utc": start, "to_utc": None}]
    lot["package_history"] = [{"package_id": lot["package_history"][0]["package_id"], "from_utc": start}]
    return lot


def _sweep_replicate(directory: Path, task: tuple[str, int]) -> tuple[list[dict[str, Any]], float]:
    profile_id, r = task
    length = float(DESIGNS["closed_loop"]["environment_days"]["value"])
    days = _days("assessment_days")
    scenario = _profile_scenario(profile_id, length, replicate_seed(DESIGNS, r))
    rep = prepare_replicate(r, directory / profile_id, scenario, _profile_lot(profile_id, scenario), days)
    return [{**engine_row(rep, d), "profile": profile_id} for d in days], rep.halving_relative_error


def test_vs4_environment_sweep(tmp_path_factory: pytest.TempPathFactory) -> None:
    """VS-3 on 400-day histories of each profile with its templates: per-profile coverage
    reported; pooled coverage within [0.86, 0.94] without outside_validated_range and at
    least 0.86 with it. Empty subgroups are reported and cannot establish coverage."""
    directory = tmp_path_factory.mktemp("vs4")
    n = replicate_count(DESIGNS, "vs4")
    days = _days("assessment_days")
    tasks = [(profile_id, r) for r in range(n) for profile_id in PROFILES]
    completed = study_map(functools.partial(_sweep_replicate, directory), tasks, label="VS-4")
    rows = [row for result, _ in completed for row in result]
    worst = max(error for _, error in completed)
    per_profile = {}
    for p in PROFILES:
        mine = [x for x in rows if x["profile"] == p]
        per_profile[p] = {
            "covered": sum(x["covered"] for x in mine),
            "n": len(mine),
            "outside_validated_range": sum(x["outside_validated_range"] for x in mine),
        }
    inside = [x for x in rows if not x["outside_validated_range"]]
    pooled = _fraction_rule(sum(x["covered"] for x in inside), len(inside), 0.86, 0.94)
    outside = [x for x in rows if x["outside_validated_range"]]
    pooled_outside = _fraction_rule(sum(x["covered"] for x in outside), len(outside), 0.86, 1.0)
    write_report(
        "VS-4",
        {
            "replicates_per_profile": n,
            "days": days,
            "per_profile": per_profile,
            "pooled_inside_validated_range": pooled,
            "pooled_outside_validated_range": pooled_outside,
            "empty_subgroups": [
                name for name, group in (("inside", inside), ("outside", outside)) if not group
            ],
            "max_truth_halving_relative_error": worst,
            "rows": rows,
        },
    )
    assert worst < 1e-6, f"truth step-halving error {worst} not below 1e-6"
    assert pooled["passed"], pooled
    assert pooled_outside["passed"], pooled_outside


def _assay_replicate(directory: Path, r: int) -> dict[str, Any]:
    assay_day = float(DESIGNS["closed_loop"]["assay_day"]["value"])
    width_day = float(DESIGNS["closed_loop"]["width_day"]["value"])
    lot = configured_lot(DESIGNS["closed_loop"]["lot_id"])
    rep = prepare_replicate(r, directory, builders.load_demo(), lot, [assay_day, width_day])
    assay = simulated_assay(rep, assay_day)
    updated = engine_row(rep, width_day, [assay])
    plain = engine_row(rep, width_day)
    return {
        **updated,
        "width_without_assay": plain["width"],
        "assay_value_percent": assay["value_percent"],
        "assay_used": assay["assay_id"] in updated["assay_ids_used"],
    }


def test_vs5_assay_closes_the_gap(tmp_path_factory: pytest.TempPathFactory) -> None:
    """VS-3 plus one onboard assay of the truth at day 200 with the lot's configured assay sd
    (the initial assay sd, 1.5 %): coverage after the update within [0.86, 0.94]; the
    interval at day 250 narrower than without the assay in at least 95% of replicates."""
    directory = tmp_path_factory.mktemp("vs5")
    n = replicate_count(DESIGNS, "vs5")
    updated_rows = study_map(functools.partial(_assay_replicate, directory), list(range(n)), label="VS-5")
    narrower = sum(row["width"] < row["width_without_assay"] for row in updated_rows)
    unused = [row["replicate"] for row in updated_rows if not row["assay_used"]]
    coverage = _fraction_rule(sum(x["covered"] for x in updated_rows), n, 0.86, 0.94)
    width_rule = _fraction_rule(narrower, n, 0.95, 1.0)
    write_report(
        "VS-5",
        {
            "replicates": n,
            "coverage_after_update": coverage,
            "narrower_at_day_250": width_rule,
            "assay_not_used_in_replicates": unused,
            "rows": updated_rows,
        },
    )
    rules = {"coverage_after_update": coverage, "narrower_at_day_250": width_rule}
    failed = {name: rule for name, rule in rules.items() if not rule["passed"]}
    assert not failed, (failed, f"assay unused in {len(unused)} replicates")
