import threading
from pathlib import Path

import pytest

from crew_health.groundlink.application.agent import AgentPass, ContactAgent

from .support import Harness


@pytest.mark.trace("R48", "SYSTEM-4")
def test_enabled_loop_counts_failures_and_keeps_running_then_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finished = threading.Event()
    calls = 0

    def run_once(self: ContactAgent) -> AgentPass:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("owner process failed")
        finished.set()
        return AgentPass()

    monkeypatch.setattr(ContactAgent, "run_once", run_once)
    h = Harness(tmp_path, enabled=True)
    loop = h.runtime.loop
    assert loop is not None
    try:
        loop.start()
        assert finished.wait(1)
        assert loop.failures == 1 and calls >= 2
    finally:
        loop.stop()
    assert loop._thread is not None and not loop._thread.is_alive()
