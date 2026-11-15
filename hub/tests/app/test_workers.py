"""Module workers stop with the hub (SYSTEM 5.12): reverse start order, one bounded budget, nothing skipped."""

import threading
from collections.abc import Callable

import pytest

from crew_health.app.workers import ModuleWorkers
from crew_health.kernel.errors import ProblemError

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.5.12")]


class Ticks:
    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        return self.ns


def recorder(
    log: list[tuple[str, float]], ticks: Ticks, name: str, *, cost_ms: int = 0
) -> Callable[[float], bool]:
    def stop(timeout_s: float) -> bool:
        log.append((name, timeout_s))
        ticks.ns += cost_ms * 1_000_000
        return True

    return stop


def test_workers_stop_in_reverse_start_order_within_one_shared_budget() -> None:
    ticks, log = Ticks(), list[tuple[str, float]]()
    workers = ModuleWorkers(maximum=4, monotonic_ns=ticks)
    for name in ("first", "second", "third"):
        workers.started(name, recorder(log, ticks, name, cost_ms=400))
    assert workers.shutdown(1000) == ()
    assert [name for name, _ in log] == ["third", "second", "first"]
    assert [round(budget, 3) for _, budget in log] == [1.0, 0.6, 0.2]


def test_a_stuck_or_failing_worker_is_reported_and_never_skips_the_rest() -> None:
    ticks, log = Ticks(), list[tuple[str, float]]()
    workers = ModuleWorkers(maximum=4, monotonic_ns=ticks)

    def broken(timeout_s: float) -> bool:
        raise RuntimeError("stop failed")

    workers.started("first", recorder(log, ticks, "first", cost_ms=5000))
    workers.started("stuck", lambda timeout_s: False)
    workers.started("broken", broken)
    workers.started("last", recorder(log, ticks, "last", cost_ms=5000))
    assert workers.shutdown(1000) == ("broken", "stuck")
    assert log == [("last", 1.0), ("first", 0.0)]


def test_duplicate_overflowing_or_late_starts_are_refused() -> None:
    workers = ModuleWorkers(maximum=1, monotonic_ns=Ticks())
    workers.started("help.worker", lambda timeout_s: True)
    for name in ("help.worker", "roles.recompute"):
        with pytest.raises(ProblemError):
            workers.started(name, lambda timeout_s: True)
    assert workers.shutdown(10) == ()
    with pytest.raises(ProblemError):
        ModuleWorkers(maximum=1, monotonic_ns=Ticks()).started("", lambda timeout_s: True)
    with pytest.raises(ProblemError):
        workers.started("late", lambda timeout_s: True)


def test_a_real_thread_is_joined_by_its_stop() -> None:
    halt = threading.Event()
    thread = threading.Thread(target=halt.wait, name="probe-worker", daemon=True)
    thread.start()

    def stop(timeout_s: float) -> bool:
        halt.set()
        thread.join(timeout_s)
        return not thread.is_alive()

    workers = ModuleWorkers(maximum=2, monotonic_ns=Ticks())
    workers.started("probe.worker", stop)
    assert workers.shutdown(2000) == ()
    assert not thread.is_alive()
