# ruff: noqa: PLC0415
"""One-lot integration smoke for active development; full 15.6 remains unchanged."""

import time
from pathlib import Path
from typing import cast

import pytest

from pharmacy.config import canonical_json_bytes, schema_violations
from pharmacy.types import JSONObject, to_json
from tests.scenarios import builders
from tests.scenarios.bundles import configured_lot, write_test_bundle

pytestmark = pytest.mark.slow


def test_one_lot_assessment_replay_store_and_expectations(tmp_path: Path) -> None:
    from pharmacy import pipeline

    lot = configured_lot("lot-asa-001")
    bundle = write_test_bundle(tmp_path / "bundle", lots=[lot])
    _, scenario, simulated = builders.demo_simulation()
    as_of = scenario["assessment_times_utc"][0]
    inputs = builders.engine_inputs(simulated, as_of)
    inputs["assays"] = [a for a in inputs["assays"] if a["lot_id"] == lot["lot_id"]]
    inputs["inspections"] = [i for i in inputs["inspections"] if i["lot_id"] == lot["lot_id"]]
    start = time.perf_counter()
    result = pipeline.assess_inventory(bundle, inputs)
    elapsed = time.perf_counter() - start
    print(f"one-lot demo at {as_of}: {elapsed:.3f} wall s/replicate")
    assert not schema_violations(to_json(result), "pharmacy/inventory-assessment.schema.json")
    assert pipeline.replay(bundle, inputs, to_json(result))
    store = builders.new_store(tmp_path / "results.jsonl")
    store.append("inventory_assessment", cast(JSONObject, to_json(result)), "CX-D", "smoke", as_of)
    store.verify()
    assert canonical_json_bytes(store.records()[0].payload) == canonical_json_bytes(to_json(result))
    outcomes = [
        builders.evaluate_expectation(e, result, bundle, scenario=scenario)
        for e in scenario["expectations"]
        if e["lot_id"] == lot["lot_id"] and e["as_of_utc"] == as_of
    ]
    assert outcomes
    assert all(o.passed for o in outcomes), outcomes
