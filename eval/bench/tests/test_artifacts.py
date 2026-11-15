"""T-E18-28: implemented gate and comparison outputs satisfy their schemas."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from crew_health.kernel.commands import canonical_json
from jsonschema import Draft202012Validator

from crew_health_bench.application.gate import CRITICAL_CHECKS, GateRun, gate
from crew_health_bench.domain.metrics import compare

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "INV-E18-12")]
SCHEMAS = Path(__file__).parents[1] / "schemas"


def test_t_e18_28_gate_and_comparison_schema() -> None:
    checks = tuple(check for _, check in CRITICAL_CHECKS)
    run = GateRun(
        "run",
        "release",
        "cases",
        "holdout",
        "full",
        "system_assisted",
        "reported",
        (("case", "completed"),),
        tuple(("case", check, "pass") for check in checks),
    )
    verdict = gate((run,), required={"case": checks})
    comparison = compare(
        (0.0,),
        (1.0,),
        case_set_a="cases",
        case_set_b="cases",
        baseline_gates={},
        candidate_gates={},
        seed=1,
        bootstrap_b=10,
        interval_level=0.95,
    )
    document = {
        **asdict(comparison),
        "baseline_run_id": "run_a",
        "candidate_run_id": "run_b",
        "case_set_sha256": "0" * 64,
        "paired_n": 1,
        "unpaired": 0,
        "interval_label": "our interval",
        "gate_counts": {
            side: dict.fromkeys((name for name, _ in CRITICAL_CHECKS), 0)
            for side in ("baseline", "candidate")
        },
        "labels": {
            "run_a": {
                "track": "system_assisted",
                "partition": "holdout",
                "completeness": "full",
                "integrity": "official",
                "release_id": "release",
            }
        },
    }
    for name, result in (("gate", asdict(verdict)), ("comparison", document)):
        schema = json.loads((SCHEMAS / (name + ".schema.json")).read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(json.loads(canonical_json(result)))
