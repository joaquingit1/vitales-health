from __future__ import annotations

import math
from collections.abc import Mapping

from crew_health.model.domain.release import ModelRefused, object_value, sha


def check_gate(release_id: str, gate: Mapping[str, object], policy: Mapping[str, object]) -> None:
    if (
        gate.get("release_id") != release_id
        or gate.get("verdict") != "pass"
        or gate.get("partition") != "holdout"
        or gate.get("completeness") != "full"
        or not gate.get("run_ids")
        or not gate.get("case_set_sha256")
        or gate.get("case_set_sha256") != gate.get("paired_case_set_sha256")
    ):
        raise ModelRefused("gate_not_passed")
    run_ids = gate.get("run_ids")
    if not isinstance(run_ids, list) or any(
        not isinstance(identifier, str) or not identifier for identifier in run_ids
    ):
        raise ModelRefused("gate_not_passed")
    sha(gate.get("case_set_sha256"))
    failures = gate.get("failures")
    if not isinstance(failures, list) or any(object_value(x).get("count") != 0 for x in failures):
        raise ModelRefused("gate_not_passed")
    regressions = object_value(gate.get("regressions"))
    bounds = object_value(policy.get("max_regression"))
    for category, bound in bounds.items():
        value = regressions.get(category)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not isinstance(bound, int | float)
            or not math.isfinite(value)
            or not math.isfinite(bound)
            or value < -bound
        ):
            raise ModelRefused("regression")
    latency = gate.get("latency_regression_fraction")
    ceiling = policy.get("max_latency_regression_fraction")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, int | float)
        or not isinstance(ceiling, int | float)
        or not math.isfinite(latency)
        or not math.isfinite(ceiling)
        or latency > ceiling
    ):
        raise ModelRefused("regression")
