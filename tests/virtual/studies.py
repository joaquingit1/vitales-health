# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Virtual stability studies (spec 15.8, B3): chambers, truth, observations, fits and the
closed loop through a simulated mission.

Designs come from the fixture (``FIXTURE_PATH``; its spec path is
tests/virtual/virtual-studies.json). Each chamber condition is a simulator zone (13.1.2) whose profile is
the condition's setpoint with the design's fluctuation, no diurnal cycle. The truth is one
draw from the engine's prior for the product (``build_stability_model`` then
``draw_parameters`` with the replicate's seed); the true potency is computed by the oracle
(tests/oracle/truth.py, lot_truth.py) from the simulator's truth series, never from readings.

Interpretations where the spec is silent (docs/requests/B3.md): the truth chemistry is the
drawn ln k*, Ea and B of the destructive thermal pathways with the extrapolation z set to 0
(R22: a widened truth is outside every fitted model family); a chamber has rate multiplier 1;
pull times are exact (the truth is interpolated between grid nodes, R11); fits receive the
chamber sensor logs and the sample package (decision 6).
"""

import copy
import dataclasses
import hashlib
import itertools
import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import numpy as np

from pharmacy.config import ConfigBundle
from pharmacy.types import (
    EnvironmentProfilesDoc,
    PackageDoc,
    RateLawKind,
    ScenarioDoc,
    Segment,
    StabilityModel,
    StorageZonesDoc,
    iso_to_seconds,
    seconds_to_iso,
)
from tests.oracle.draws import Z_RH, Z_T, OracleDraw
from tests.oracle.kinetics import conversion_from_theta, theta_from_conversion
from tests.oracle.lot_truth import oracle_draw_row, truth_model
from tests.oracle.precision import R_FLOAT
from tests.oracle.truth import PackageModel, TruthModel, integrate_truth
from tests.scenarios.bundles import stability_model
from tests.scenarios.parallel import ordered_map

if TYPE_CHECKING:
    from pharmacy.fit import StudyFit, StudyObservation

HERE = Path(__file__).resolve().parent
FIXTURE_PATH: Final[Path] = HERE / "virtual-studies.json"
SCHEMA_PATH: Final[Path] = HERE / "virtual-studies.schema.json"
REPORT_PATH: Final[Path] = HERE / "report.json"
CHAMBER_START_UTC: Final[str] = "2030-01-01T00:00:00Z"
SECONDS_PER_MONTH: Final[float] = 365.25 * 86400.0 / 12.0
Z_95: Final[float] = 1.959963984540054
FITTABLE_KINDS: Final[tuple[RateLawKind, ...]] = (
    "F0",
    "F1",
    "F2",
    "R2",
    "R3",
    "A2",
    "A3",
    "A4",
    "D1",
    "D2",
    "D3",
    "D4",
)
"""Kinds select_model compares in VS-2 and VS-3; Fn, An and B1 need kind parameters the
spec does not fix for a study fit (request B3-R14)."""


def load_designs() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data


def design(designs: Mapping[str, Any], design_id: str) -> dict[str, Any]:
    for d in designs["designs"]:
        if d["design_id"] == design_id:
            result: dict[str, Any] = d
            return result
    raise KeyError(design_id)


def replicate_count(designs: Mapping[str, Any], test_id: str) -> int:
    """The fixture's count, overridden by the environment variable B3_<TEST>_REPLICATES
    (for local runs; the report records the count used)."""
    override = os.environ.get(f"B3_{test_id.upper()}_REPLICATES")
    return int(override) if override else int(designs["replicates"][test_id]["value"])


def replicate_seed(designs: Mapping[str, Any], r: int) -> int:
    return int(designs["mission_seed_base"]["value"]) + r


def generator(seed: int, stream: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(stream,))))


STREAM_TRUTH, STREAM_ASSAY = 0, 1


# =========================================================================================
# Truth draw
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class Truth:
    model: StabilityModel
    draw: OracleDraw
    kind: RateLawKind

    def thermal(self, bundle: ConfigBundle) -> TruthModel:
        """The true thermal chemistry with z = 0 (request B3-R22)."""
        values = {pid: {**v, Z_T: 0.0, Z_RH: 0.0} for pid, v in self.draw.pathway_values.items()}
        flat = dataclasses.replace(self.draw, pathway_values=values)
        return truth_model(self.model, flat, bundle.settings)

    def single_pathway(self, bundle: ConfigBundle) -> tuple[float, float, float]:
        """(ln k*, Ea, B) of the only destructive thermal pathway (VS-1 recovery target)."""
        pathways = self.thermal(bundle).pathways
        if len(pathways) != 1:
            raise AssertionError(
                f"{self.model.product_id}: {len(pathways)} destructive thermal pathways; parameter "
                "recovery of one (ln k*, Ea, B) is undefined (request B3-R23)"
            )
        p = pathways[0]
        return p.ln_k_ref_per_s, p.ea_j_per_mol, p.b_per_percent_rh


def draw_truth(
    bundle: ConfigBundle,
    product_id: str,
    ingredient_id: str,
    package_ids: Sequence[str],
    zone_ids: Sequence[str],
    seed: int,
    *,
    kind: RateLawKind | None = None,
    initial: tuple[float, float] = (100.0, 1.5),
) -> Truth:
    from pharmacy.uncertainty import draw_parameters

    model = stability_model(bundle, product_id, ingredient_id, package_ids)
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    block = draw_parameters(
        model, initial[0], initial[1], zones, list(zone_ids), generator(seed, STREAM_TRUTH), 1
    )
    draw = oracle_draw_row(model, block, 0)
    true_kind = model.rate_law_kind if kind is None else kind
    if kind is not None:
        model = dataclasses.replace(model, rate_law_kind=kind, order_n=None, alpha0=None, avrami_n=None)
    return Truth(model, draw, true_kind)


def true_rate(tm: TruthModel, temperature_k: float, rh_percent: float) -> float:
    total = 0.0
    for p in tm.pathways:
        ln_k = p.ln_k_ref_per_s - p.ea_j_per_mol / R_FLOAT * (
            1 / temperature_k - 1 / tm.reference_temperature_k
        )
        if p.humidity_term:
            ln_k += p.b_per_percent_rh * (rh_percent - tm.reference_rh_percent)
        total += math.exp(ln_k)
    return total


def potency(kind: RateLawKind, c0: float, theta: float) -> float:
    return c0 * (1.0 - float(conversion_from_theta(kind, theta)))


# =========================================================================================
# Chambers
# =========================================================================================


def chamber_docs(
    bundle: ConfigBundle, design_doc: Mapping[str, Any], condition: Mapping[str, Any]
) -> tuple[EnvironmentProfilesDoc, StorageZonesDoc, str, str]:
    """A profile and a zone for one chamber condition (setpoint, design fluctuation, no
    diurnal cycle), added to copies of the configured documents."""
    profiles = cast(dict[str, Any], copy.deepcopy(bundle.environment_profiles))
    zones = cast(dict[str, Any], copy.deepcopy(bundle.zones))
    chamber = design_doc["chamber"]
    profile_id = f"vs_{design_doc['design_id']}_{condition['condition_id']}"
    profile = copy.deepcopy(next(p for p in profiles["profiles"] if p["profile_id"] == "station"))
    profile["profile_id"] = profile_id
    t, rh = profile["temperature"], profile["relative_humidity"]
    t["setpoint_k"]["value"] = condition["temperature_k"]["value"]
    t["diurnal_amplitude_k"]["value"] = 0.0
    t["ou_sd_k"]["value"] = chamber["temperature_sd_k"]["value"]
    t["ou_time_constant_s"]["value"] = chamber["ou_time_constant_s"]["value"]
    rh["setpoint_percent"]["value"] = condition["rh_percent"]["value"]
    rh["diurnal_amplitude_percent"]["value"] = 0.0
    rh["ou_sd_percent"]["value"] = chamber["rh_sd_percent"]["value"]
    rh["ou_time_constant_s"]["value"] = chamber["ou_time_constant_s"]["value"]
    profiles["profiles"].append(profile)
    zone = copy.deepcopy(next(z for z in zones["zones"] if z["zone_id"] == "station_kit"))
    zone["zone_id"] = profile_id
    zone["environment_profile_id"] = profile_id
    zones["zones"].append(zone)
    return cast(EnvironmentProfilesDoc, profiles), cast(StorageZonesDoc, zones), profile_id, profile_id


def chamber_run(
    bundle: ConfigBundle,
    design_doc: Mapping[str, Any],
    condition: Mapping[str, Any],
    duration_s: float,
    seed: int,
) -> tuple[list[float], list[float], list[float], tuple[Segment, ...]]:
    """Truth (t from 0, T, RH) of one chamber condition over ``duration_s`` on the design's grid."""
    from pharmacy.envsim import measure, simulate_truth

    profiles, zones, profile_id, zone_id = chamber_docs(bundle, design_doc, condition)
    grid = float(design_doc["chamber"]["sampling_interval_s"]["value"])
    steps = max(1, math.ceil(duration_s / grid))
    start = iso_to_seconds(CHAMBER_START_UTC)
    zone = next(z for z in zones["zones"] if z["zone_id"] == zone_id)
    sensors = [
        {"sensor_id": x["sensor_id"], "quantity": x["quantity"], "sampling_interval_s": grid}
        for x in zone["sensors"]
        if x["quantity"] in ("temperature_k", "relative_humidity")
    ]
    scenario = cast(
        ScenarioDoc,
        {
            "document_id": "virtual-chamber",
            "schema": "pharmacy/scenario.schema.json",
            "version": "0.1.0",
            "review_state": "draft",
            "notice": "B3 virtual chamber (spec 15.8).",
            "mission": {
                "mission_id": profile_id,
                "name": profile_id,
                "start_utc": CHAMBER_START_UTC,
                "end_utc": seconds_to_iso(start + steps * grid),
                "seed": seed,
                "description": "chamber",
            },
            "zones": [
                {"zone_id": zone_id, "profile_id": profile_id, "sensors": sensors, "seed_label": profile_id}
            ],
            "events": [],
            "lot_ids": [],
            "assays": [],
            "inspections": [],
            "assessment_times_utc": [],
            "expectations": [],
        },
    )
    truth = simulate_truth(scenario, profiles, zones, grid)
    series = {s.quantity: s for s in truth if s.zone_id == zone_id}
    readings = {s.quantity: s for s in measure(truth, scenario, profiles, zones)}
    t = [x - start for x in series["temperature_k"].t_s]
    points = {q: list(s.points) for q, s in readings.items()}
    if any(not points.get(q) or points[q][0].t_s != start for q in ("temperature_k", "relative_humidity")):
        raise AssertionError("chamber log has no initial T/RH reading; cannot silently use a setpoint")
    edges = sorted(
        {
            start,
            start + duration_s,
            *(p.t_s for ps in points.values() for p in ps if p.t_s < start + duration_s),
        }
    )
    cursors = dict.fromkeys(points, 0)
    segments = []
    for left, right in itertools.pairwise(edges):
        values = {}
        for q, ps in points.items():
            while cursors[q] + 1 < len(ps) and ps[cursors[q] + 1].t_s <= left:
                cursors[q] += 1
            values[q] = ps[cursors[q]].value
        rh = values["relative_humidity"]
        segments.append(
            Segment(
                left - start,
                duration_s if right == start + duration_s else right - start,
                zone_id,
                None,
                values["temperature_k"],
                rh,
                rh,
                0.0,
                0.0,
                0.0,
                (),
                (),
            )
        )
    return t, list(series["temperature_k"].values), list(series["relative_humidity"].values), tuple(segments)


def chamber_truth(
    bundle: ConfigBundle,
    design_doc: Mapping[str, Any],
    condition: Mapping[str, Any],
    duration_s: float,
    seed: int,
) -> tuple[list[float], list[float], list[float]]:
    """Independent simulator truth for the integral spot checks."""
    t, temperatures, rhs, _ = chamber_run(bundle, design_doc, condition, duration_s, seed)
    return t, temperatures, rhs


def pull_log(log: Sequence[Segment], pull_s: float, package: PackageDoc | None) -> tuple[Segment, ...]:
    """Clip the left-held chamber readings at the observation time."""
    return tuple(
        dataclasses.replace(
            segment,
            t1_s=min(segment.t1_s, pull_s),
            package_id=None if package is None else package["package_id"],
        )
        for segment in log
        if segment.t0_s < pull_s
    )


# =========================================================================================
# A study on one truth
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class StudyRun:
    observations: tuple[StudyObservation, ...]
    true_potency: dict[tuple[str, float], float]
    halving_relative_error: float


def pull_times_s(
    design_doc: Mapping[str, Any],
    condition: Mapping[str, Any],
    truth: Truth,
    tm: TruthModel,
    spec_lower: float,
) -> list[float]:
    """Pull times (s from chamber start): the ICH months, or for isoconversion the design's
    fractions of the time at which the truth reaches the specification edge (capped by
    the design's maximum). Pulls are exact times, not rounded to the chamber grid: the
    truth is interpolated linearly between grid nodes (request B3-R11), so fast kinetics at
    80 C do not collapse every pull onto the first grid node."""
    if condition["pull_months"] is not None:
        raw = [m * SECONDS_PER_MONTH for m in condition["pull_months"]["value"]]
    else:
        k = true_rate(tm, condition["temperature_k"]["value"], condition["rh_percent"]["value"])
        alpha_edge = 1.0 - spec_lower / truth.draw.c0_percent
        theta_edge = float(theta_from_conversion(truth.kind, max(alpha_edge, 1e-6)))
        cap = float(design_doc["isoconversion_max_days"]["value"]) * 86400.0
        t_edge = min(theta_edge / k, cap / max(design_doc["isoconversion_pull_fractions"]["value"]))
        raw = [f * t_edge for f in design_doc["isoconversion_pull_fractions"]["value"]]
    return sorted(set(raw))


def with_nodes(
    t: Sequence[float], temps: Sequence[float], rhs: Sequence[float], extra: Sequence[float]
) -> tuple[list[float], list[float], list[float], list[int]]:
    """The truth series with ``extra`` times inserted (linear interpolation) and the index of
    each extra time in the merged nodes."""
    nodes = sorted({*t, *extra})
    merged_t = [float(x) for x in np.interp(nodes, t, temps)]
    merged_rh = [float(x) for x in np.interp(nodes, t, rhs)]
    position = {x: i for i, x in enumerate(nodes)}
    return nodes, merged_t, merged_rh, [position[x] for x in extra]


def run_study(
    bundle: ConfigBundle,
    design_doc: Mapping[str, Any],
    truth: Truth,
    package_id: str | None,
    seed: int,
    spec_lower: float,
) -> StudyRun:
    from pharmacy.fit import StudyObservation

    tm = truth.thermal(bundle)
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    package = PackageModel.open_dish() if package_id is None else PackageModel.from_doc(packages[package_id])
    rng = generator(seed, STREAM_ASSAY)
    sd = float(design_doc["assay"]["sd_percent"]["value"])
    replicates = int(design_doc["assay"]["replicates"]["value"])
    observations: list[StudyObservation] = []
    truth_at: dict[tuple[str, float], float] = {}
    worst = 0.0
    grid = float(design_doc["chamber"]["sampling_interval_s"]["value"])
    for condition in design_doc["conditions"]:
        pulls = pull_times_s(design_doc, condition, truth, tm, spec_lower)
        t, temps, rhs, log = chamber_run(bundle, design_doc, condition, max(pulls[-1], grid), seed)
        package_doc = None if package_id is None else packages[package_id]
        nodes, node_t, node_rh, indices = with_nodes(t, temps, rhs, pulls)
        integral = integrate_truth(tm, nodes, node_t, node_rh, package, report_indices=indices)
        worst = max(worst, integral.halving_relative_error)
        for pull, theta in zip(pulls, integral.theta_at, strict=True):
            value = potency(truth.kind, truth.draw.c0_percent, theta)
            truth_at[(condition["condition_id"], pull)] = value
            for _ in range(replicates):
                observations.append(
                    StudyObservation(
                        pull,
                        condition["temperature_k"]["value"],
                        condition["rh_percent"]["value"],
                        value + sd * float(rng.standard_normal()),
                        sd,
                        condition["condition_id"],
                        chamber_log=pull_log(log, pull, package_doc),
                        package=package_doc,
                    )
                )
    return StudyRun(tuple(observations), truth_at, worst)


def fit_kind(
    bundle: ConfigBundle, observations: Sequence[StudyObservation], kind: RateLawKind, truth: Truth
) -> StudyFit:
    """fit_stability_study at the model's reference conditions, humidity and C0 free,
    absolute weights (the assay sd is known)."""
    from pharmacy.fit import fit_stability_study

    return fit_stability_study(
        list(observations),
        kind,
        truth.model.reference_temperature_k,
        truth.model.reference_rh_percent,
        fit_humidity=True,
        fit_initial=True,
        absolute_weights=True,
        numerics=bundle.settings["numerics"],
    )


def interval_contains(fit: StudyFit, name: str, truth_value: float) -> tuple[bool, float]:
    i = fit.names.index(name)
    se = math.sqrt(fit.covariance[i][i])
    z = (fit.mean[i] - truth_value) / se
    return abs(z) <= Z_95, z


def predicted_potency_interval(
    fit: StudyFit, temperature_k: float, rh_percent: float, time_s: float, seed: int, n: int = 4000
) -> tuple[float, float]:
    """Spec 14.4 interval from the public fitter, including its input-derived seed.

    This API remains a wrapper for callers supplying a replicate seed; the fit inputs
    determine prediction draws, so process scheduling cannot affect the interval.
    """
    from pharmacy.fit import StudyObservation, prediction_interval

    if n != 4000:
        raise ValueError("spec 14.4 requires 4000 prediction draws")
    observation = StudyObservation(time_s, temperature_k, rh_percent, 0.0, None, "prediction")
    return prediction_interval(fit, observation)


def observation_digest(observations: Sequence[StudyObservation]) -> str:
    """Hash all fit inputs, including chamber histories, assay sd and sample packages."""
    encoded = json.dumps(
        [dataclasses.asdict(o) for o in observations], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_report(section: str, content: Mapping[str, Any]) -> None:
    """Merge one VS section into tests/virtual/report.json."""
    current: dict[str, Any] = {}
    if REPORT_PATH.exists():
        current = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    current.setdefault("document_id", "virtual-studies-report")
    current.setdefault("sections", {})[section] = content
    REPORT_PATH.write_text(
        json.dumps(current, indent=1, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def study_map[T, U](function: Callable[[T], U], items: Sequence[T], *, label: str) -> list[U]:
    """Record execution failures and elapsed cost, so stale study results never survive a failed run."""
    started = time.perf_counter()
    write_report(label, {"status": "running", "tasks": len(items)})
    try:
        return ordered_map(function, items, label=label)
    except (Exception, KeyboardInterrupt) as error:
        elapsed = time.perf_counter() - started
        write_report(
            label,
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "execution_failed",
                "tasks": len(items),
                "elapsed_seconds": elapsed,
                "error": f"{type(error).__name__}: {error}",
            },
        )
        print(f"{label}: execution failed after {elapsed:.3f} wall s for {len(items)} requested tasks")
        raise
