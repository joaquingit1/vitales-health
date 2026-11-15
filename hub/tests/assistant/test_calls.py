from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from crew_health.assistant.adapters.calls import BoundedCalls
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.testing import MemoryClock


class Cancel:
    def __init__(self) -> None:
        self.flag = threading.Event()

    def is_cancelled(self) -> bool:
        return self.flag.is_set()


def clock() -> MemoryClock:
    return MemoryClock(datetime(2026, 11, 15, tzinfo=UTC), 0, "epoch_one")


@pytest.mark.trace("T-E13-021", "T-E13-024", "V27")
def test_cancelled_dependency_is_never_started() -> None:
    calls = BoundedCalls(clock(), max_inflight=1, max_duration_ms=1000, cancel_poll_ms=5)
    cancel = Cancel()
    cancel.flag.set()
    seen: list[str] = []
    with pytest.raises(ProblemError) as error:
        calls.invoke(lambda: seen.append("forbidden"), budget_ns=1_000_000_000, cancel=cancel)
    assert error.value.code == ErrorCode.INVALID_TRANSITION and seen == []


@pytest.mark.trace("T-E13-021", "T-E13-024", "INV-E13-11", "V27")
def test_cancel_returns_before_blocked_provider_and_inflight_slot_remains_bounded() -> None:
    calls = BoundedCalls(clock(), max_inflight=1, max_duration_ms=1000, cancel_poll_ms=5)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    cancel = Cancel()

    def blocked() -> str:
        started.set()
        release.wait(2)
        finished.set()
        return "late private result"

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(calls.invoke, blocked, budget_ns=1_000_000_000, cancel=cancel)
        try:
            assert started.wait(1)
            cancel.flag.set()
            with pytest.raises(ProblemError) as error:
                pending.result(timeout=0.5)
            assert error.value.code == ErrorCode.INVALID_TRANSITION and not finished.is_set()
            with pytest.raises(ProblemError) as full:
                calls.invoke(lambda: "must not start", budget_ns=1_000_000_000, cancel=Cancel())
            assert full.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
        finally:
            release.set()
            assert finished.wait(1)


@pytest.mark.trace("T-E13-021", "T-E13-023", "V27")
@pytest.mark.parametrize("budget_ns,advance_ns", [(20_000_000, 20_000_000), (2_000_000_000, 1_000_000_000)])
def test_late_success_never_passes_own_budget_or_stricter_hub_cap(budget_ns: int, advance_ns: int) -> None:
    source = clock()
    calls = BoundedCalls(source, max_inflight=1, max_duration_ms=1000, cancel_poll_ms=5)

    def late() -> str:
        source.monotonic += advance_ns
        return "late private result"

    with pytest.raises(ProblemError) as error:
        calls.invoke(late, budget_ns=budget_ns, cancel=Cancel())
    assert error.value.code == ErrorCode.MODEL_TIMEOUT


@pytest.mark.trace("T-E13-021", "T-E13-023", "V27", "SYSTEM.5.2")
def test_epoch_change_and_monotonic_regression_cannot_admit_result() -> None:
    source = clock()
    calls = BoundedCalls(source, max_inflight=1, max_duration_ms=1000, cancel_poll_ms=5)

    def restart() -> str:
        source.epoch = "epoch_two"
        return "private result"

    with pytest.raises(ProblemError):
        calls.invoke(restart, budget_ns=1_000_000_000, cancel=Cancel())

    def regress() -> str:
        source.monotonic -= 1
        return "private result"

    with pytest.raises(ProblemError):
        calls.invoke(regress, budget_ns=1_000_000_000, cancel=Cancel())


@pytest.mark.trace("T-E13-021", "T-E13-051", "V26")
def test_result_and_none_are_preserved_but_unexpected_private_exception_is_sanitized() -> None:
    calls = BoundedCalls(clock(), max_inflight=1, max_duration_ms=1000, cancel_poll_ms=5)
    assert calls.invoke(lambda: None, budget_ns=1_000_000_000, cancel=Cancel()) is None
    assert calls.invoke(lambda: 0, budget_ns=1_000_000_000, cancel=Cancel()) == 0

    def broken() -> str:
        raise RuntimeError("PRIVATE_PROVIDER_CANARY")

    with pytest.raises(ProblemError) as error:
        calls.invoke(broken, budget_ns=1_000_000_000, cancel=Cancel())
    assert error.value.code == ErrorCode.INTERNAL_ERROR and "PRIVATE_PROVIDER_CANARY" not in str(error.value)
