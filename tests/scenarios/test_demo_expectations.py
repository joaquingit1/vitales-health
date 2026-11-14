"""Demo expectations (spec 15.4), evaluated on the engine output at each ``as_of_utc``, and
``true_potency_within_interval`` against the simulator truth (marker slow: one engine run of
the 400-day demo at its three assessment times, shared through ``builders.demo_run``)."""

import functools
from typing import Any, cast

import pytest

from pharmacy.types import Expectation, LotDoc, iso_to_seconds
from tests.oracle.draws import nominal_draw
from tests.oracle.lot_truth import LotTruth, TruthNotModelledError, lot_true_potency
from tests.scenarios import builders
from tests.scenarios.bundles import stability_model

pytestmark = pytest.mark.slow

DEMO = builders.load_demo()
EXPECTATION_IDS = [e["expectation_id"] for e in DEMO["expectations"]]


def _lot(lot_id: str) -> LotDoc:
    bundle, _, _ = builders.demo_simulation()
    for lot in bundle.lots["lots"]:
        if lot["lot_id"] == lot_id:
            return lot
    raise KeyError(lot_id)


@functools.cache
def nominal_truth(lot_id: str, ingredient_id: str) -> LotTruth:
    """True potency of the lot at every demo assessment time when the true chemistry is the
    engine's nominal parameter set (posterior means, multipliers at their medians), the
    environment is the simulator's truth and c0 is the lot's initial assay mean."""
    bundle, scenario, simulated = builders.demo_simulation()
    lot = _lot(lot_id)
    product = next(p for p in bundle.formulary["products"] if p["product_id"] == lot["product_id"])
    package_ids = sorted({p["package_id"] for p in lot["package_history"]})
    model = stability_model(bundle, product["product_id"], ingredient_id, package_ids)
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    draw = nominal_draw(model, zones, sorted({c["zone_id"] for c in lot["custody"]}))
    assay = next(a for a in lot["initial_assay"] if a["ingredient_id"] == ingredient_id)
    draw = type(draw)(
        assay["mean_percent"]["value"],
        draw.pathway_values,
        draw.zone_ln_multiplier,
        draw.temperature_offset_k,
        draw.rh_offset_percent,
    )
    zone_truth = {
        z["zone_id"]: (
            builders.truth_values(simulated, z["zone_id"], "temperature_k")[0],
            builders.truth_values(simulated, z["zone_id"], "temperature_k")[1],
            builders.truth_values(simulated, z["zone_id"], "relative_humidity")[1],
        )
        for z in scenario["zones"]
    }
    times = [iso_to_seconds(t) for t in scenario["assessment_times_utc"]]
    return lot_true_potency(model, draw, lot, zone_truth, zones, packages, bundle.settings, times)


def demo_true_potency(lot_id: str, ingredient_id: str, as_of_utc: str) -> float:
    truth = nominal_truth(lot_id, ingredient_id)
    assert truth.halving_relative_error < 1e-6, truth.halving_relative_error
    return truth.potency_total_percent[truth.times_s.index(iso_to_seconds(as_of_utc))]


@pytest.mark.parametrize("expectation_id", EXPECTATION_IDS)
def test_demo_expectation(expectation_id: str) -> None:
    run = builders.demo_run()
    expectation = next(e for e in run.scenario["expectations"] if e["expectation_id"] == expectation_id)
    outcome = builders.evaluate_expectation(
        expectation,
        run.results[expectation["as_of_utc"]],
        run.bundle,
        scenario=run.scenario,
        true_potency=demo_true_potency,
    )
    assert outcome.passed, f"{expectation_id} ({expectation['kind']}): {outcome.detail}"


def _lots_without_observations() -> list[str]:
    observed = {a["lot_id"] for a in DEMO["assays"]} | {i["lot_id"] for i in DEMO["inspections"]}
    return [lot_id for lot_id in DEMO["lot_ids"] if lot_id not in observed]


@pytest.mark.parametrize("lot_id", _lots_without_observations())
@pytest.mark.parametrize("as_of_utc", DEMO["assessment_times_utc"])
def test_true_potency_within_interval_at_nominal_truth(lot_id: str, as_of_utc: str) -> None:
    """``true_potency_within_interval`` for every lot without assays or inspections (an
    observation may legitimately move the posterior away from the nominal chemistry): the
    90% interval must contain the potency the nominal chemistry gives on the true
    environment. Lots whose chemistry the truth helper does not model are skipped with
    the reason."""
    run = builders.demo_run()
    bundle = run.bundle
    lot = _lot(lot_id)
    product = next(p for p in bundle.formulary["products"] if p["product_id"] == lot["product_id"])
    for ingredient in product["ingredients"]:
        try:
            nominal_truth(lot_id, ingredient["ingredient_id"])
        except TruthNotModelledError as reason:
            pytest.skip(f"{lot_id}:{ingredient['ingredient_id']}: truth not modelled ({reason})")
        expectation = cast(
            Expectation,
            cast(
                Any,
                {
                    "expectation_id": f"x-truth-{lot_id}-{ingredient['ingredient_id']}",
                    "kind": "true_potency_within_interval",
                    "lot_id": lot_id,
                    "ingredient_id": ingredient["ingredient_id"],
                    "as_of_utc": as_of_utc,
                    "values": [],
                    "lower": None,
                    "upper": None,
                    "other_lot_id": None,
                    "rationale": "B3 synthetic expectation at the nominal truth",
                },
            ),
        )
        outcome = builders.evaluate_expectation(
            expectation, run.results[as_of_utc], bundle, scenario=run.scenario, true_potency=demo_true_potency
        )
        assert outcome.passed, outcome.detail
