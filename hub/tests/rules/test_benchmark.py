"""T-E24-100: the engine's CPU budget for one input with 50 definitions (E24 11): p95 at most 2 ms.

CPU time of the process, never the wall clock, and the engine itself (no storage): the budget is for the decision.
The 50 definitions are the shipped ones plus clones of its sensor-limit definitions under new ids, so the load is the
real shape of the configuration, not typed content. Marked slow: it repeats the evaluation 400 times.
"""

from __future__ import annotations

import copy
import time

import pytest
import rules_harness as h

from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.engine import evaluate

pytestmark = [pytest.mark.E24, pytest.mark.slow]

BUDGET_S = 0.002
RUNS = 400
TARGET_DEFINITIONS = 50


def fifty_definitions() -> dict[str, object]:
    raw = h.deployment_rules()
    definitions = list(raw["definitions"])  # type: ignore[call-overload]
    sensor = [d for d in definitions if d["raised_by"] == "sensor_limit" and d["condition"]["kind"] == "sensor_limit"]
    n = 0
    while len(definitions) < TARGET_DEFINITIONS:
        clone = copy.deepcopy(sensor[n % len(sensor)])
        clone["id"] = f"{clone['id']}_load_{n}"
        definitions.append(clone)
        n += 1
    return {**raw, "definitions": definitions}


@pytest.mark.trace("T-E24-100")
def test_evaluating_one_sensor_input_over_fifty_definitions_stays_inside_the_cpu_budget() -> None:
    rules = parse_rules_document(fifty_definitions())
    assert len(rules.definitions) == TARGET_DEFINITIONS
    world = h.World(rules, h.release_documents())
    inp = world.sensor(results=[h.result("co2_1h_act", crossed=False, clear_holds=False)])
    view = world.view()
    evaluate(inp, view)  # warm: imports and caches are start-up cost, not request cost
    spent: list[float] = []
    for _ in range(RUNS):
        start = time.process_time()
        evaluate(inp, view)
        spent.append(time.process_time() - start)
    spent.sort()
    p95 = spent[int(RUNS * 0.95) - 1]
    assert p95 <= BUDGET_S, f"p95 {p95 * 1000:.3f} ms against the {BUDGET_S * 1000:.0f} ms budget"
