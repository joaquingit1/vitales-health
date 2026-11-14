# ruff: noqa: PLC0415
"""Spec 15.7 posterior coverage through public engine interfaces, 2000 replicates.

PHARMACY_COVERAGE_REPLICATES permits a tiny execution smoke run. Such a run keeps the
full acceptance rules and is never evidence of calibrated coverage. PHARMACY_TEST_WORKERS
controls deterministic process parallelism, with replicate r mapped to seed 20261114+r.
"""

import dataclasses
import functools
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pharmacy.config import ConfigBundle
from pharmacy.types import AssayObservation, iso_to_seconds, seconds_to_iso
from tests.oracle.draws import evaluate_draw
from tests.oracle.lot_truth import oracle_draw_row
from tests.scenarios import builders
from tests.scenarios.bundles import stability_model, write_test_bundle
from tests.scenarios.parallel import ordered_map
from tests.virtual.studies import write_report

pytestmark = pytest.mark.slow


@dataclasses.dataclass(frozen=True)
class Replicate:
    r: int
    truth: float
    lower_limit: float
    plain: tuple[float, float, float]
    assayed: tuple[float, float, float]
    reanchored: bool


@functools.cache
def _one_batch_bundle() -> ConfigBundle:
    return write_test_bundle(Path(tempfile.mkdtemp(prefix="cxd-coverage-")), one_batch=True)


def coverage_replicate(r: int) -> Replicate:
    """Draw truth on the sensed history, simulate one assay, estimate both posteriors."""
    from pharmacy import envsim, exposure, pipeline, uncertainty, update

    bundle = _one_batch_bundle()
    seed = 20261114 + r  # Spec 15.7 seed mapping.
    scenario = builders.self_test_scenario(bundle, days=1, seed=seed, profiles=("transit",))
    # Use the fixture's declared assay product, lot, site and assay uncertainty.
    template = builders.load_demo()["assays"][0]
    lot = next(lot for lot in bundle.lots["lots"] if lot["lot_id"] == template["lot_id"])
    ingredient_id = template["ingredient_id"]
    initial = next(a for a in lot["initial_assay"] if a["ingredient_id"] == ingredient_id)
    end = scenario["mission"]["end_utc"]
    start_s = iso_to_seconds(scenario["mission"]["start_utc"])
    end_s = iso_to_seconds(end)
    assay_s = (start_s + end_s) / 2
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    model = stability_model(
        bundle, lot["product_id"], ingredient_id, sorted({p["package_id"] for p in lot["package_history"]})
    )
    sim = envsim.simulate_scenario(scenario, bundle.environment_profiles, bundle.zones)
    series_by_zone = {z: [s for s in sim.series if s.zone_id == z] for z in zones}
    history = exposure.build_lot_exposure(lot, zones, packages, series_by_zone, end_s)
    generator = uncertainty.batch_generator(seed, 0)
    drawn = uncertainty.draw_parameters(
        model,
        initial["mean_percent"]["value"],
        initial["sd_percent"]["value"],
        zones,
        sorted({s.zone_id for s in history.segments}),
        generator,
        1,
    )
    truth_draw = oracle_draw_row(model, drawn, 0)
    truth = evaluate_draw(model, history.segments, packages, zones, bundle.settings, truth_draw)
    prefix = exposure.build_lot_exposure(lot, zones, packages, series_by_zone, assay_s)
    assay_truth = evaluate_draw(model, prefix.segments, packages, zones, bundle.settings, truth_draw)
    assay = AssayObservation(
        f"coverage-{r}",
        lot["lot_id"],
        ingredient_id,
        seconds_to_iso(assay_s),
        assay_s,
        template["measurand"],
        float(assay_truth.potency_total_percent),
        template["sd_percent"],
        template["site"],
        template["method"],
        template["laboratory"],
        "Spec 15.7 synthetic assay",
    )
    sd = update.total_sd_percent(assay, bundle.settings["update"])
    assay = dataclasses.replace(
        assay, value_percent=assay.value_percent + sd * float(generator.standard_normal())
    )
    plain = pipeline.estimate_potency(
        bundle, lot["lot_id"], ingredient_id, end, sim.series, (), (), model=model
    )
    assayed = pipeline.estimate_potency(
        bundle, lot["lot_id"], ingredient_id, end, sim.series, (assay,), (), model=model
    )
    return Replicate(
        r,
        float(truth.potency_active_percent),
        plain.specification.lower_percent,
        (plain.coverage_interval.low, plain.coverage_interval.high, plain.p_below_lower),
        (assayed.coverage_interval.low, assayed.coverage_interval.high, assayed.p_below_lower),
        "assay_reanchored" in assayed.flags,
    )


def coverage_summary(rows: list[Replicate], arm: str) -> dict[str, Any]:
    """Coverage, Brier score and all nonempty decile reliability bins under spec 15.7."""
    truth = np.array([row.truth for row in rows])
    lower = np.array([row.lower_limit for row in rows])
    values = np.array([getattr(row, arm) for row in rows])
    covered = (values[:, 0] <= truth) & (truth <= values[:, 1])
    below = truth < lower
    predicted = values[:, 2]
    bins = np.minimum(np.floor(predicted * 10).astype(np.intp), 9)
    reliability = []
    for index in range(10):
        selected = bins == index
        count = int(selected.sum())
        if not count:
            continue
        mean = float(predicted[selected].mean())
        observed = float(below[selected].mean())
        se = math.sqrt(mean * (1 - mean) / count)
        reliability.append(
            {
                "bin": index,
                "count": count,
                "predicted": mean,
                "observed": observed,
                "se": se,
                "evaluated": count >= 30,
                "passed": abs(observed - mean) <= 3 * se if count >= 30 else None,
            }
        )
    fraction = float(covered.mean())
    return {
        "n": len(rows),
        "coverage": fraction,
        "brier": float(np.mean((predicted - below) ** 2)),
        "reliability": reliability,
        "coverage_passed": 0.88 <= fraction <= 0.92,
        "reliability_passed": all(row["passed"] for row in reliability if row["evaluated"]),
    }


def test_posterior_coverage() -> None:
    n = int(os.environ.get("PHARMACY_COVERAGE_REPLICATES", "2000"))
    if n <= 0:
        raise ValueError("PHARMACY_COVERAGE_REPLICATES must be positive")
    rows = ordered_map(coverage_replicate, list(range(n)), label="15.7 posterior coverage")
    summaries = {arm: coverage_summary(rows, arm) for arm in ("plain", "assayed")}
    write_report(
        "15.7", {"replicates": n, "summaries": summaries, "rows": [dataclasses.asdict(row) for row in rows]}
    )
    assert all(s["coverage_passed"] and s["reliability_passed"] for s in summaries.values()), summaries
