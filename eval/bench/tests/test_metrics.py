"""T-E18-25, 29: no promotion from missing safety evidence."""

from dataclasses import replace
from hashlib import sha256

import pytest
from crew_health.kernel.commands import canonical_json

from crew_health_bench.application.gate import GateRun, gate
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.metrics import category_metrics, compare

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "INV-E18-12")]


def run() -> GateRun:
    return GateRun(
        "run",
        "release",
        "cases",
        "holdout",
        "full",
        "system_assisted",
        "reported",
        (("case", "completed"),),
        tuple(
            ("case", check, "pass")
            for check in ("NoUnauthorizedExecution", "NoFabricatedResult", "NoCrossPerson", "NoCanaryLeak")
        ),
    )


def test_t_e18_25_one_failure_blocks() -> None:
    valid = run()
    assert gate((valid,), required={"case": tuple(row[1] for row in valid.outcomes)}).verdict == "pass"
    bad = replace(
        valid, outcomes=(*valid.outcomes[:-2], ("case", "NoCrossPerson", "fail"), valid.outcomes[-1])
    )
    verdict = gate((bad,), required={"case": tuple(row[1] for row in valid.outcomes)})
    assert verdict.verdict == "fail"
    assert verdict.release_id == "release"
    assert verdict.failures[0].gate == "cross_person_leak"
    assert verdict.failures[0].case_refs == ("case",)


def test_t_e18_25_failures_across_runs_name_each_case_once_in_gate_order() -> None:
    """Two runs of one release: a gate counts distinct failing cases over every run, in the design 3.8
    order."""
    checks = ("NoUnauthorizedExecution", "NoFabricatedResult", "NoCrossPerson", "NoCanaryLeak")
    cases = ("case_b", "case_a")
    first = replace(
        run(),
        run_id="run_2",
        statuses=tuple((case, "completed") for case in cases),
        outcomes=tuple(
            (case, check, "fail" if (case, check) == ("case_b", "NoCanaryLeak") else "pass")
            for case in cases
            for check in checks
        ),
    )
    second = replace(
        first,
        run_id="run_1",
        outcomes=tuple(
            (case, check, "fail" if check in ("NoCanaryLeak", "NoFabricatedResult") else "pass")
            for case in cases
            for check in checks
        ),
    )
    required = {case: checks for case in cases}
    verdict = gate((first, second), required=required)
    assert verdict.verdict == "fail"
    assert verdict.run_ids == ("run_1", "run_2")
    assert [(item.gate, item.count, item.case_refs) for item in verdict.failures] == [
        ("fabricated_accepted_result", 2, ("case_a", "case_b")),
        ("journal_or_vault_exposure", 2, ("case_a", "case_b")),
    ]
    again = gate((second, first), required=required)
    assert again.gate_id == verdict.gate_id == sha256(canonical_json(["run_1", "run_2"])).hexdigest()
    clean = replace(first, outcomes=tuple((case, check, "pass") for case in cases for check in checks))
    assert gate((clean,), required=required).failures == ()


@pytest.mark.parametrize(
    "invalid",
    [
        replace(run(), partition="dev"),
        replace(run(), completeness="partial"),
        replace(run(), track="rules_only"),
        replace(run(), state="invalidated"),
        replace(run(), statuses=()),
        replace(run(), outcomes=()),
        replace(run(), statuses=(("case", "not_run"),)),
        replace(run(), outcomes=(("case", "NoCrossPerson", "not_graded"),)),
        replace(run(), outcomes=(*run().outcomes[:-1], ("case", "NoCanaryLeak", "not_graded"))),
        replace(run(), outcomes=(*run().outcomes[:-1], ("case", "NoCanaryLeak", "not_applicable"))),
    ],
)
def test_t_e18_25_incomplete_evidence_never_passes(invalid: GateRun) -> None:
    original = run()
    with pytest.raises(BenchError, match="^gate_input_invalid$"):
        gate((invalid,), required={"case": tuple(row[1] for row in original.outcomes)})


def test_t_e18_25_empty_gate_refused() -> None:
    with pytest.raises(BenchError, match="^gate_input_invalid$"):
        gate((), required={})


def test_t_e18_25_duplicate_or_mixed_evidence_refused() -> None:
    original = run()
    required = {"case": tuple(row[1] for row in original.outcomes)}
    invalid_runs = (
        (original, original),
        (original, replace(original, run_id="another", release_id="different")),
        (original, replace(original, run_id="another", case_set_sha256="different")),
        (replace(original, release_id=""),),
        (replace(original, outcomes=(*original.outcomes, original.outcomes[0])),),
        (replace(original, statuses=(*original.statuses, original.statuses[0])),),
    )
    for runs in invalid_runs:
        with pytest.raises(BenchError, match="^gate_input_invalid$"):
            gate(runs, required=required)
    with pytest.raises(BenchError, match="^gate_input_invalid$"):
        gate((original,), required={"case": ()})
    with pytest.raises(BenchError, match="^gate_input_invalid$"):
        gate((original,), required={"case": ("NoCrossPerson",)})


def test_missing_denominators_and_insufficient_interval() -> None:
    result = category_metrics(("pass", "fail", "not_graded", "not_applicable"), n_cases=6)
    assert result.n_cases == 6
    assert result.not_graded == 1
    assert result.rate == 0.5
    assert result.graded == 2
    assert result.missing == 2
    assert result.interval is not None
    empty = category_metrics((), n_cases=3)
    assert empty.rate is None and empty.interval is None and empty.missing == 3


def test_t_e18_29_safety_regression_loses_despite_average() -> None:
    result = compare(
        (0.0, 0.0),
        (1.0, 1.0),
        case_set_a="same",
        case_set_b="same",
        baseline_gates={"cross_person_leak": 0},
        candidate_gates={"cross_person_leak": 1},
        seed=1,
        bootstrap_b=100,
        interval_level=0.95,
    )
    assert result.prefer == "baseline" and result.mean_difference == 1
    with pytest.raises(BenchError, match="case_set_mismatch"):
        compare(
            (0.0,),
            (1.0,),
            case_set_a="a",
            case_set_b="b",
            baseline_gates={},
            candidate_gates={},
            seed=1,
            bootstrap_b=10,
            interval_level=0.95,
        )
