"""Deterministic process-level execution for independent simulation replicates."""

import multiprocessing
import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor

from pharmacy.errors import InputError


def worker_count() -> int:
    """PHARMACY_TEST_WORKERS controls processes; default 1 avoids oversubscription."""
    try:
        count = int(os.environ.get("PHARMACY_TEST_WORKERS", "1"))
    except ValueError as error:
        raise InputError("PHARMACY_TEST_WORKERS must be a positive integer") from error
    if count < 1:
        raise InputError("PHARMACY_TEST_WORKERS must be a positive integer")
    return count


def ordered_map[T, U](function: Callable[[T], U], items: Sequence[T], *, label: str) -> list[U]:
    """Return results in input replicate order for every worker count; report wall cost.

    Spawn avoids inheriting thread-pool state. Seeds belong to each task's replicate,
    never the worker. Only the parent writes aggregate reports.
    """
    workers = worker_count()
    start = time.perf_counter()
    if workers == 1:
        result = [function(item) for item in items]
    else:
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            result = list(pool.map(function, items))
    elapsed = time.perf_counter() - start
    cost = elapsed / len(items) if items else 0.0
    print(f"{label}: {len(items)} tasks, {workers} workers, {elapsed:.3f} s, {cost:.3f} wall s/task")
    return result
