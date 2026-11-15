from datetime import UTC, datetime

import pytest

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.process_clock import ProcessClock


def test_process_clock_utc_monotonic_and_epoch_contract() -> None:
    at = datetime(2026, 11, 14, tzinfo=UTC)
    clock = ProcessClock("epoch_process", utc_now=lambda: at, monotonic=lambda: 42)
    assert clock.now() == at
    assert clock.monotonic_ns() == 42
    assert clock.process_epoch() == "epoch_process"
    with pytest.raises(ProblemError):
        ProcessClock("invalid epoch")
