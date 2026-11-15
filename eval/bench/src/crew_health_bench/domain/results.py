from __future__ import annotations

from crew_health_bench.domain.errors import BenchError


def status(state: str, completeness: str) -> str:
    if state in ("aborted", "invalidated"):
        return "failed"
    if state != "reported" or completeness not in ("full", "subset", "partial"):
        raise BenchError("invalid_transition")
    return "complete" if completeness == "full" else "partial"


def scenario_outcome(case_status: str, outcomes: tuple[str, ...]) -> str:
    if case_status in ("harness_error", "failed_generation"):
        return "error"
    if case_status == "timeout":
        return "timeout"
    if case_status == "not_run":
        return "missing"
    if case_status != "completed" or any(
        item not in ("pass", "fail", "not_graded", "not_applicable") for item in outcomes
    ):
        raise BenchError("case_invalid")
    if "fail" in outcomes:
        return "fail"
    if not outcomes or "not_graded" in outcomes or "pass" not in outcomes:
        return "missing"
    return "pass"


def benchmark_labels(
    *,
    track: str,
    variant: str,
    completeness: str,
    integrity: str,
    n: int,
    N: int,
    grader: str,
    benchmark_disclaimer: str,
) -> dict[str, object]:
    if track not in ("model_only", "system_assisted") or variant not in ("main", "Hard", "Consensus"):
        raise BenchError("case_invalid")
    if completeness not in ("full", "subset", "partial") or integrity not in ("official", "modified"):
        raise BenchError("case_invalid")
    if not 0 <= n <= N or (completeness == "full" and n != N) or not grader or not benchmark_disclaimer:
        raise BenchError("case_invalid")
    return {
        "claim_class": "public_benchmark",
        "track": track,
        "variant": variant,
        "completeness": completeness,
        "integrity": integrity,
        "n": n,
        "N": N,
        "grader": grader,
        "reviewer_qualification": "engineering",
        "benchmark_disclaimer": benchmark_disclaimer,
    }
