# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Scenario helpers shared by tests/scenarios, tests/e2e and tests/virtual (B3).

* Scenario documents built in code (30-day simulator self-test missions).
* Engine inputs at an ``as_of`` from a simulated scenario (readings and observations at or
  before ``as_of`` only, spec 12.1).
* The expectation evaluator of spec 15.4.
* A cached run of the demo scenario through ``pipeline.assess_inventory`` at every
  assessment time, so scenario and end-to-end tests share one engine run per session.

Engine modules are imported inside functions so that collection never depends on the state
of B1's and B2's code.
"""

import copy
import dataclasses
import functools
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.envsim import SimulatedScenario, simulate_scenario
from pharmacy.types import (
    EngineInputsDoc,
    Expectation,
    InventoryAssessment,
    LotAssessment,
    PotencyEstimate,
    Quantity,
    ScenarioDoc,
    ScenarioSensor,
    ScenarioZone,
    iso_to_seconds,
    seconds_to_iso,
    to_json,
)
from tests.oracle.exact import sort_key

if TYPE_CHECKING:
    from pharmacy.store import ResultStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_PATH = REPO_ROOT / "fixtures" / "pharmacy" / "demo-moon-base.json"
INPUTS_SCHEMA = "pharmacy/inputs.schema.json"
SCENARIO_SCHEMA = "pharmacy/scenario.schema.json"

SELF_TEST_SEED = 20280110
"""The demo mission seed, chosen before any run as the seed of the 30-day self-test mission."""
SELF_TEST_DAYS = 30
SELF_TEST_START_UTC = "2028-01-10T00:00:00Z"
PROFILE_ZONES: Mapping[str, str] = {
    "station": "station_kit",
    "transit": "transit_kit",
    "moon": "moon_base_pharmacy",
    "mars": "mars_base_pharmacy",
}
"""One configured storage zone per environment profile; the seed label is the profile id."""


def load_demo() -> ScenarioDoc:
    return cast(ScenarioDoc, json.loads(DEMO_PATH.read_text(encoding="utf-8")))


def _zone_sensors(bundle: ConfigBundle, zone_id: str, interval_s: float) -> list[ScenarioSensor]:
    zone = next(z for z in bundle.zones["zones"] if z["zone_id"] == zone_id)
    return [
        {"sensor_id": s["sensor_id"], "quantity": s["quantity"], "sampling_interval_s": interval_s}
        for s in zone["sensors"]
    ]


def self_test_scenario(
    bundle: ConfigBundle,
    *,
    days: float = SELF_TEST_DAYS,
    seed: int = SELF_TEST_SEED,
    profiles: Sequence[str] = ("station", "transit", "moon", "mars"),
    interval_s: float = 1800.0,
) -> ScenarioDoc:
    """A mission of ``days`` days with one zone per environment profile at the profile's
    sampling interval, no events and no observations."""
    start = iso_to_seconds(SELF_TEST_START_UTC)
    zones: list[ScenarioZone] = [
        {
            "zone_id": PROFILE_ZONES[p],
            "profile_id": p,
            "sensors": _zone_sensors(bundle, PROFILE_ZONES[p], interval_s),
            "seed_label": p,
        }
        for p in profiles
    ]
    return cast(
        ScenarioDoc,
        {
            "document_id": "envsim-self-test",
            "schema": SCENARIO_SCHEMA,
            "version": "0.1.0",
            "review_state": "draft",
            "notice": "Built in tests/scenarios for the simulator self-tests (spec 15.4).",
            "mission": {
                "mission_id": "envsim-self-test",
                "name": "Simulator self-test",
                "start_utc": SELF_TEST_START_UTC,
                "end_utc": seconds_to_iso(start + days * 86400.0),
                "seed": seed,
                "description": "One zone per environment profile.",
            },
            "zones": zones,
            "events": [],
            "lot_ids": [],
            "assays": [],
            "inspections": [],
            "assessment_times_utc": [],
            "expectations": [],
        },
    )


def with_events(scenario: ScenarioDoc, events: Sequence[Mapping[str, Any]]) -> ScenarioDoc:
    out = copy.deepcopy(scenario)
    out["events"] = [cast(Any, dict(e)) for e in events]
    return out


def truth_values(sim: SimulatedScenario, zone_id: str, quantity: Quantity) -> tuple[list[float], list[float]]:
    for t in sim.truth:
        if t.zone_id == zone_id and t.quantity == quantity:
            return list(t.t_s), list(t.values)
    raise KeyError((zone_id, quantity))


# =========================================================================================
# Engine inputs at as_of (spec 12.1)
# =========================================================================================


def engine_inputs(sim: SimulatedScenario, as_of_utc: str) -> EngineInputsDoc:
    """Sensor series, assays and inspections at or before ``as_of`` as an inputs document."""
    as_of = iso_to_seconds(as_of_utc)
    series = []
    for s in sim.series:
        points = [{"t_utc": p.t_utc, "t_s": p.t_s, "value": p.value} for p in s.points if p.t_s <= as_of]
        series.append(
            {
                "series_id": s.series_id,
                "sensor_id": s.sensor_id,
                "zone_id": s.zone_id,
                "quantity": s.quantity,
                "unit": s.unit,
                "points": points,
            }
        )
    assays = [cast(dict[str, Any], to_json(a)) for a in sim.assays if a.measured_at_s <= as_of]
    inspections = [cast(dict[str, Any], to_json(i)) for i in sim.inspections if i.inspected_at_s <= as_of]
    return cast(
        EngineInputsDoc,
        {
            "schema": INPUTS_SCHEMA,
            "as_of_utc": as_of_utc,
            "sensor_series": series,
            "assays": assays,
            "inspections": inspections,
        },
    )


# =========================================================================================
# Expectations (spec 15.4)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class ExpectationOutcome:
    expectation_id: str
    passed: bool
    detail: str


def _assessment(result: InventoryAssessment, lot_id: str) -> LotAssessment:
    for a in result.assessments:
        if a.lot_id == lot_id:
            return a
    raise AssertionError(f"lot {lot_id} not assessed at {result.as_of_utc}")


def _estimates(assessment: LotAssessment, ingredient_id: str | None) -> list[PotencyEstimate]:
    found = [e for e in assessment.estimates if ingredient_id is None or e.ingredient_id == ingredient_id]
    if not found:
        raise AssertionError(f"no estimate for {assessment.lot_id}:{ingredient_id}")
    return found


def lower_quantile(estimate: PotencyEstimate, probability: float) -> float:
    for q in estimate.quantiles:
        if q.probability == probability:
            return q.value
    raise AssertionError(f"{estimate.estimate_id} has no quantile at {probability}")


def evaluate_expectation(
    expectation: Expectation,
    result: InventoryAssessment,
    bundle: ConfigBundle,
    *,
    scenario: ScenarioDoc | None = None,
    true_potency: Callable[[str, str, str], float] | None = None,
) -> ExpectationOutcome:
    """Evaluate one expectation on the engine output at its ``as_of_utc`` (spec 15.4).
    ``true_potency(lot_id, ingredient_id, as_of_utc)`` serves true_potency_within_interval."""
    kind = expectation["kind"]
    eid = expectation["expectation_id"]
    assessment = _assessment(result, expectation["lot_id"])
    values = expectation["values"]
    if kind == "state_in":
        state = assessment.decision.state
        return ExpectationOutcome(eid, state in values, f"state {state}, expected one of {values}")
    if kind == "ordered_before":
        ordered = sorted((a.decision for a in result.assessments), key=lambda d: sort_key(d.ordering_key))
        ids = [d.lot_id for d in ordered]
        other = expectation["other_lot_id"]
        assert other is not None
        ok = (
            expectation["lot_id"] in ids
            and other in ids
            and ids.index(expectation["lot_id"]) < ids.index(other)
        )
        return ExpectationOutcome(eid, ok, f"order {ids}")
    estimates = _estimates(assessment, expectation["ingredient_id"])
    if kind in ("flag_present", "flag_absent"):
        flags = sorted({f for e in estimates for f in e.flags})
        present = any(v in flags for v in values)
        ok = present if kind == "flag_present" else not present
        return ExpectationOutcome(eid, ok, f"flags {flags}, {kind} of {values}")
    if kind == "estimate_basis_in":
        bases = [e.estimate_basis for e in estimates]
        ok = all(b in values for b in bases)
        detail = f"bases {bases}, expected in {values}"
        if eid == "x-amc-002-assay-id" and scenario is not None:
            wanted = [a["assay_id"] for a in scenario["assays"] if a["lot_id"] == expectation["lot_id"]]
            used = sorted({i for e in estimates for i in e.assay_ids_used})
            ok = ok and all(w in used for w in wanted)
            detail += f"; assay_ids_used {used}, required {wanted}"
        return ExpectationOutcome(eid, ok, detail)
    if kind == "forecast_status_in":
        statuses = [
            f.status
            for f in assessment.forecasts
            if expectation["ingredient_id"] is None or f.ingredient_id == expectation["ingredient_id"]
        ]
        ok = bool(statuses) and all(s in values for s in statuses)
        return ExpectationOutcome(eid, ok, f"forecast statuses {statuses}, expected in {values}")
    if kind == "lower_quantile_between":
        p = bundle.settings["decision"]["lower_quantile"]["value"]
        lows = [lower_quantile(e, p) for e in estimates]
        lo, hi = expectation["lower"], expectation["upper"]
        assert lo is not None
        assert hi is not None
        return ExpectationOutcome(
            eid, all(lo <= q <= hi for q in lows), f"q_lo {lows}, expected in [{lo}, {hi}]"
        )
    if kind == "coverage_low_is":
        flagged = any("coverage_low" in e.flags for e in estimates)
        flag_wanted = values == ["true"]
        return ExpectationOutcome(eid, flagged == flag_wanted, f"coverage_low {flagged}, expected {values}")
    if kind == "true_potency_within_interval":
        if true_potency is None:
            raise AssertionError(f"{eid} needs simulator truth (true_potency callback)")
        outcomes = []
        for e in estimates:
            truth = true_potency(e.lot_id, e.ingredient_id, expectation["as_of_utc"])
            inside = e.coverage_interval.low <= truth <= e.coverage_interval.high
            outcomes.append(
                (
                    inside,
                    f"{e.ingredient_id}: truth {truth} in [{e.coverage_interval.low}, "
                    f"{e.coverage_interval.high}]",
                )
            )
        return ExpectationOutcome(eid, all(o for o, _ in outcomes), "; ".join(d for _, d in outcomes))
    raise AssertionError(f"unknown expectation kind {kind}")


# =========================================================================================
# The demo run (cached per session)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class DemoRun:
    bundle: ConfigBundle
    scenario: ScenarioDoc
    simulated: SimulatedScenario
    inputs: Mapping[str, EngineInputsDoc]
    results: Mapping[str, InventoryAssessment]
    seconds: Mapping[str, float]


@functools.cache
def demo_simulation() -> tuple[ConfigBundle, ScenarioDoc, SimulatedScenario]:
    bundle = load_bundle()
    scenario = load_demo()
    simulated = simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    return bundle, scenario, simulated


def _demo_assessment(task: tuple[ConfigBundle, EngineInputsDoc]) -> tuple[InventoryAssessment, float]:
    """One independent assessment time; only elapsed timing depends on scheduling."""
    from pharmacy import pipeline

    bundle, inputs = task
    started = time.perf_counter()
    result = pipeline.assess_inventory(bundle, inputs)
    return result, time.perf_counter() - started


@functools.cache
def demo_run() -> DemoRun:
    """Run ``pipeline.assess_inventory`` on the demo at every assessment time (spec 15.6)."""
    from tests.scenarios.parallel import ordered_map

    bundle, scenario, simulated = demo_simulation()
    times = sorted(scenario["assessment_times_utc"])
    inputs = {as_of: engine_inputs(simulated, as_of) for as_of in times}
    tasks = [(bundle, inputs[as_of]) for as_of in times]
    completed = ordered_map(_demo_assessment, tasks, label="demo assessment times")
    results = {as_of: result for as_of, (result, _) in zip(times, completed, strict=True)}
    seconds = {as_of: elapsed for as_of, (_, elapsed) in zip(times, completed, strict=True)}
    return DemoRun(bundle, scenario, simulated, inputs, results, seconds)


def new_store(path: Path) -> ResultStore:
    """A ResultStore at ``path`` (the constructor takes the JSON Lines path)."""
    from pharmacy.store import ResultStore

    return ResultStore(path)
