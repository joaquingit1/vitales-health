# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""End to end (spec 15.6, marker slow): the demo scenario through ``pipeline.assess_inventory``
at every assessment time. Every output validates against its schema, every expectation
holds, ``replay`` returns true, the result store verifies after appending every result, and
the runtime budget of 12.5 is reported (never asserted)."""

import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from pharmacy.config import canonical_json_bytes, schema_violations
from pharmacy.types import JSONObject, JSONValue, iso_to_seconds, to_json
from tests.oracle.exact import sort_key
from tests.scenarios import builders

pytestmark = pytest.mark.slow

ASSESSMENT_TIMES = builders.load_demo()["assessment_times_utc"]
DAY_100_BUDGET_S = 120.0
DAY_400_BUDGET_S = 300.0
SUITE_BUDGET_S = 900.0
ACTOR = "b3-e2e"


def _validate(instance: JSONValue, schema_id: str, label: str) -> list[str]:
    return [f"{label}: {v}" for v in schema_violations(instance, schema_id)]


@pytest.mark.parametrize("as_of_utc", ASSESSMENT_TIMES)
def test_outputs_validate_against_their_schemas(as_of_utc: str) -> None:
    run = builders.demo_run()
    result = run.results[as_of_utc]
    document = to_json(result)
    problems = _validate(document, "pharmacy/inventory-assessment.schema.json", "inventory assessment")
    for assessment in result.assessments:
        for estimate in assessment.estimates:
            problems += _validate(
                to_json(estimate), "pharmacy/potency-estimate.schema.json", estimate.estimate_id
            )
        for forecast in assessment.forecasts:
            problems += _validate(to_json(forecast), "pharmacy/forecast.schema.json", forecast.forecast_id)
        problems += _validate(
            to_json(assessment.decision), "pharmacy/lot-decision.schema.json", assessment.decision.decision_id
        )
    assert not problems, "\n".join(problems)


def test_inputs_validate_against_their_schema() -> None:
    run = builders.demo_run()
    problems: list[str] = []
    for as_of, inputs in run.inputs.items():
        problems += _validate(cast(JSONValue, inputs), "pharmacy/inputs.schema.json", as_of)
    assert not problems, "\n".join(problems)


def test_every_lot_with_status_active_is_assessed_in_order() -> None:
    run = builders.demo_run()
    active = {lot["lot_id"] for lot in run.bundle.lots["lots"] if lot["status"] == "active"}
    for as_of, result in run.results.items():
        assert {a.lot_id for a in result.assessments} == active, as_of
        keys = [sort_key(a.decision.ordering_key) for a in result.assessments]
        assert keys == sorted(keys), f"{as_of}: assessments are not in spec 11.5 order"


def test_every_expectation_holds() -> None:
    run = builders.demo_run()
    failures = []
    for expectation in run.scenario["expectations"]:
        outcome = builders.evaluate_expectation(
            expectation, run.results[expectation["as_of_utc"]], run.bundle, scenario=run.scenario
        )
        if not outcome.passed:
            failures.append(f"{outcome.expectation_id} ({expectation['kind']}): {outcome.detail}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("as_of_utc", ASSESSMENT_TIMES)
def test_replay_returns_true(as_of_utc: str) -> None:
    from pharmacy import pipeline

    run = builders.demo_run()
    recorded = json.loads(canonical_json_bytes(to_json(run.results[as_of_utc])))
    assert pipeline.replay(run.bundle, run.inputs[as_of_utc], recorded) is True


def test_result_store_verifies_after_appending_every_result(tmp_path: Path) -> None:
    run = builders.demo_run()
    store = builders.new_store(tmp_path / "results.jsonl")
    appended = 0
    for as_of, result in run.results.items():
        store.append(
            "inventory_assessment", cast(JSONObject, to_json(result)), ACTOR, "demo end to end", as_of
        )
        appended += 1
        for assessment in result.assessments:
            for estimate in assessment.estimates:
                store.append("potency_estimate", cast(JSONObject, to_json(estimate)), ACTOR, "demo", as_of)
                appended += 1
            store.append("lot_decision", cast(JSONObject, to_json(assessment.decision)), ACTOR, "demo", as_of)
            appended += 1
    store.verify()
    records = store.records()
    assert len(records) == appended
    sequences = [r.sequence for r in records]
    assert all(b == a + 1 for a, b in itertools.pairwise(sequences)), sequences[:5]


def test_a_tampered_store_fails_verification(tmp_path: Path) -> None:
    from pharmacy.errors import StoreError

    run = builders.demo_run()
    path = tmp_path / "results.jsonl"
    store = builders.new_store(path)
    for as_of, result in run.results.items():
        store.append("inventory_assessment", cast(JSONObject, to_json(result)), ACTOR, "demo", as_of)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["reason"] = "edited"
    lines[0] = json.dumps(first, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(StoreError):
        builders.new_store(path).verify()


def test_runtime_budget_is_reported(record_property: Callable[[str, object], None]) -> None:
    """Spec 12.5: reported, not asserted."""
    run = builders.demo_run()
    for as_of, seconds in run.seconds.items():
        record_property(f"assess_inventory_seconds[{as_of}]", round(seconds, 3))
        day = (iso_to_seconds(as_of) - iso_to_seconds(run.scenario["mission"]["start_utc"])) / 86400.0
        budget = DAY_100_BUDGET_S if day == 100 else (DAY_400_BUDGET_S if day == 400 else None)
        print(f"assess_inventory day {day:g}: {seconds:.2f} s (target {budget}, reported only)")
    print(f"Full 15.6 suite target: {SUITE_BUDGET_S:.0f} s; use pytest session wall time including replay.")
