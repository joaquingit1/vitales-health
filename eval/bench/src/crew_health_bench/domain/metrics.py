from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite, sqrt
from random import Random
from statistics import NormalDist, mean

from crew_health_bench.domain.errors import BenchError


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    passed: int
    failed: int
    not_applicable: int
    not_graded: int
    n_cases: int
    graded: int
    missing: int
    rate: float | None
    interval: tuple[float, float] | None


def category_metrics(outcomes: tuple[str, ...], *, n_cases: int) -> CategoryMetrics:
    if n_cases < len(outcomes) or any(
        item not in ("pass", "fail", "not_applicable", "not_graded") for item in outcomes
    ):
        raise BenchError("case_invalid")
    passed, failed = outcomes.count("pass"), outcomes.count("fail")
    graded = passed + failed
    rate = passed / graded if graded else None
    interval: tuple[float, float] | None = None
    if rate is not None:
        z = NormalDist().inv_cdf(0.975)
        center = (rate + z * z / (2 * graded)) / (1 + z * z / graded)
        radius = z * sqrt(rate * (1 - rate) / graded + z * z / (4 * graded * graded)) / (1 + z * z / graded)
        interval = (max(0.0, center - radius), min(1.0, center + radius))
    return CategoryMetrics(
        passed,
        failed,
        outcomes.count("not_applicable"),
        outcomes.count("not_graded"),
        n_cases,
        graded,
        n_cases - len(outcomes),
        rate,
        interval,
    )


@dataclass(frozen=True, slots=True)
class Comparison:
    mean_difference: float
    interval: tuple[float, float]
    prefer: str


def compare(
    baseline: tuple[float, ...],
    candidate: tuple[float, ...],
    *,
    case_set_a: str,
    case_set_b: str,
    baseline_gates: Mapping[str, int],
    candidate_gates: Mapping[str, int],
    seed: int,
    bootstrap_b: int,
    interval_level: float,
) -> Comparison:
    if case_set_a != case_set_b or not baseline or len(baseline) != len(candidate):
        raise BenchError("case_set_mismatch")
    if (
        bootstrap_b < 1
        or not 0 < interval_level < 1
        or any(not isfinite(item) for item in (*baseline, *candidate))
        or baseline_gates.keys() != candidate_gates.keys()
    ):
        raise BenchError("case_invalid")
    differences = tuple(b - a for a, b in zip(baseline, candidate, strict=True))
    rng = Random(seed)
    boot = sorted(mean(rng.choices(differences, k=len(differences))) for _ in range(bootstrap_b))
    lower = int((1 - interval_level) / 2 * bootstrap_b)
    upper = min(bootstrap_b - 1, int((1 + interval_level) / 2 * bootstrap_b))
    regression = any(candidate_gates[key] > baseline_gates[key] for key in sorted(baseline_gates))
    difference = mean(differences)
    return Comparison(
        difference, (boot[lower], boot[upper]), "baseline" if regression or difference <= 0 else "candidate"
    )


def bootstrap_interval(values: tuple[float, ...], *, seed: int, bootstrap_b: int,
                       interval_level: float) -> tuple[float, float]:
    if (not values or any(not isfinite(value) for value in values)
            or bootstrap_b < 1 or not 0 < interval_level < 1):
        raise BenchError("case_invalid")
    rng = Random(seed)
    samples = sorted(max(0.0, min(1.0, mean(rng.choices(values, k=len(values)))))
                     for _ in range(bootstrap_b))
    lower = int((1 - interval_level) / 2 * bootstrap_b)
    upper = min(bootstrap_b - 1, int((1 + interval_level) / 2 * bootstrap_b))
    return samples[lower], samples[upper]
