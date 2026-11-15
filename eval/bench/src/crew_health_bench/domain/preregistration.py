from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.sample import eligible_order, sample_size


@dataclass(frozen=True)
class VariantSample:
    N: int
    n: int
    case_ids: tuple[str, ...]
    warmup_ids: tuple[str, ...]
    t_case: Quantity


@dataclass(frozen=True)
class PreRegistration:
    plan_sha256: str
    frozen_by: str
    variants: Mapping[str, VariantSample]

    def record(self) -> dict[str, object]:
        return {"plan_sha256": self.plan_sha256, "frozen_by": self.frozen_by,
                "variants": {name: {"N": value.N, "n": value.n, "case_ids": value.case_ids,
                                    "warmup_ids": value.warmup_ids,
                                    "t_case": {"value": float(value.t_case.value), "unit": value.t_case.unit.code}}
                             for name, value in sorted(self.variants.items())}}


def _median(values: Sequence[Quantity], expected: int) -> Quantity:
    if expected < 1 or len(values) != expected:
        raise BenchError("warmup_failed")
    if any(q.unit.code != "s" or not q.value.is_finite() or q.value <= 0 for q in values):
        raise BenchError("warmup_failed")
    ordered = sorted(q.value for q in values)
    middle = len(ordered) // 2
    value = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
    return Quantity(value, Unit("s"))


def preregistration(
    plan_sha256: str, variants: Mapping[str, Sequence[str]], timings: Mapping[str, Sequence[Quantity]], *,
    seed: str, frame_exclusions: Sequence[str], warmup_n: int, budget: Quantity, frozen_by: str,
) -> PreRegistration:
    if not variants or set(variants) != set(timings) or not frozen_by:
        raise BenchError("warmup_failed")
    if len(plan_sha256) != 64 or any(c not in "0123456789abcdef" for c in plan_sha256):
        raise BenchError("warmup_failed")
    samples: dict[str, VariantSample] = {}
    for name, ids in sorted(variants.items()):
        order = eligible_order(ids, seed=seed, frame_exclusions=frame_exclusions)
        if warmup_n < 1 or len(order) <= warmup_n:
            raise BenchError("warmup_failed")
        duration = _median(timings[name], warmup_n)
        eligible = order[:-warmup_n]
        count = sample_size(len(eligible), budget, duration)
        samples[name] = VariantSample(len(eligible), count, eligible[:count], order[-warmup_n:], duration)
    return PreRegistration(plan_sha256, frozen_by, samples)
