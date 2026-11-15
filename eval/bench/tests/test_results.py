"""T-E18-10, 28, 30: honest public labels and scenario projection."""

import pytest
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.results import benchmark_labels, scenario_outcome, status

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R30", "INV-E18-06")]


@pytest.mark.parametrize(
    "state,completeness,expected",
    [
        ("reported", "full", "complete"),
        ("reported", "subset", "partial"),
        ("reported", "partial", "partial"),
        ("aborted", "full", "failed"),
        ("invalidated", "full", "failed"),
    ],
)
def test_t_e18_30_status_mapping(state: str, completeness: str, expected: str) -> None:
    assert status(state, completeness) == expected


def test_t_e18_30_no_vacuous_pass() -> None:
    assert scenario_outcome("completed", ()) != "pass"
    assert scenario_outcome("completed", ("not_graded",)) != "pass"
    assert scenario_outcome("completed", ("pass", "not_graded")) != "pass"
    assert scenario_outcome("completed", ("pass",)) == "pass"
    assert scenario_outcome("completed", ("pass", "fail")) == "fail"
    for source, expected in (
        ("harness_error", "error"),
        ("timeout", "timeout"),
        ("not_run", "missing"),
        ("failed_generation", "error"),
    ):
        assert scenario_outcome(source, ()) == expected


def test_t_e18_10_labels_never_imply_clinical_review() -> None:
    labels = benchmark_labels(
        track="model_only",
        variant="Hard",
        completeness="partial",
        integrity="modified",
        n=2,
        N=3,
        grader="independent",
        benchmark_disclaimer="configured disclaimer",
    )
    assert labels["reviewer_qualification"] == "engineering"
    assert labels["claim_class"] == "public_benchmark"
    assert labels["integrity"] == "modified" and labels["completeness"] == "partial"
    assert labels["variant"] == "Hard"
    with pytest.raises(BenchError):
        benchmark_labels(
            track="model_only",
            variant="pooled",
            completeness="full",
            integrity="official",
            n=3,
            N=3,
            grader="independent",
            benchmark_disclaimer="configured disclaimer",
        )
