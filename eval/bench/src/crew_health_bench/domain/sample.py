from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_FLOOR
from hashlib import sha256

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity

from crew_health_bench.domain.errors import BenchError


def eligible_order(
    prompt_ids: Sequence[str], *, seed: str, frame_exclusions: Sequence[str]
) -> tuple[str, ...]:
    if len(set(prompt_ids)) != len(prompt_ids):
        raise BenchError("case_invalid")
    excluded = frozenset(frame_exclusions)
    return tuple(
        sorted(
            (item for item in prompt_ids if item not in excluded),
            key=lambda item: (sha256((seed + item).encode()).digest(), item),
        )
    )


def sample_size(eligible_n: int, budget: Quantity, t_case: Quantity) -> int:
    if eligible_n < 0 or budget.unit.code != "s" or t_case.unit.code != "s":
        raise BenchError("warmup_failed")
    if budget.value <= 0 or t_case.value <= 0:
        raise BenchError("warmup_failed")
    return min(eligible_n, int((budget.value / t_case.value).to_integral_value(rounding=ROUND_FLOOR)))


def run_id(manifest: Mapping[str, object], ids: IdFactory) -> str:
    return ids.derived("benchrun", sha256(canonical_json(manifest)).hexdigest())


def validate_grader(candidate_model: str, grader_model: str) -> None:
    if not candidate_model or not grader_model or candidate_model == grader_model:
        raise BenchError("grader_unavailable")
