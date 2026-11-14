"""Measurement-predictive intervals from the public adaptive result, spec 15.5.

The context manager observes run_adaptive without changing its inputs or result. It
avoids reconstructing a distribution from an estimate's handful of quantiles. Use it
within one replicate process; engine evaluation threads do not enter run_adaptive.
"""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import numpy as np

from pharmacy import uncertainty
from pharmacy.errors import InputError
from pharmacy.types import CoverageInterval, MonteCarloSettings


@contextmanager
def capture_draws() -> Iterator[list[uncertainty.AdaptiveResult]]:
    """Collect completed public adaptive results; the last is the final posterior."""
    results: list[uncertainty.AdaptiveResult] = []
    original = uncertainty.run_adaptive

    def observe(
        evaluate_batch: Callable[[int, np.random.Generator], uncertainty.BatchOutcome],
        seed: int,
        settings: MonteCarloSettings,
        *,
        ess_floor: float | None = None,
        **options: Any,
    ) -> uncertainty.AdaptiveResult:
        result = original(evaluate_batch, seed, settings, ess_floor=ess_floor, **options)
        results.append(result)
        return result

    with patch.object(uncertainty, "run_adaptive", observe):
        yield results


def measurement_interval(
    result: uncertainty.AdaptiveResult, assay_sd: float, probability: float, seed_material: str
) -> CoverageInterval:
    """Add one independent normal assay error per draw, then take weighted quantiles.

    The separate PCG64 seed is SHA-256 of the estimate and measurement identity. Engine
    draw streams and ordering are unchanged. No clipping of simulated assay errors.
    """
    if not np.isfinite(assay_sd) or assay_sd < 0:
        raise InputError("measurement assay sd must be finite and nonnegative")
    seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:16], "big")
    rng = np.random.Generator(np.random.PCG64(seed))
    values = np.asarray(result.values, dtype=np.float64)
    noisy = values + assay_sd * rng.standard_normal(values.size)
    weights = None
    if result.log_weights is not None:
        logs = np.asarray(result.log_weights, dtype=np.float64)
        weights = np.exp(logs - np.max(logs))
    return uncertainty.coverage_interval(noisy, weights, probability)
