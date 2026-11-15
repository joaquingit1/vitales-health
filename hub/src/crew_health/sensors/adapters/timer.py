"""The independent sweep timer (E23 3.4): a bounded background loop, apart from the scheduler and from the HTTP
path. It runs one unit of work per tick; a failed tick is a visible SCHEDULER_LAG fault, never silence, and the
loop keeps running (restart policy, DEPLOYMENT 7.2)."""

from __future__ import annotations

import threading
from collections.abc import Callable


class SweepTimer:
    def __init__(self, tick: Callable[[], None], on_failure: Callable[[], None], *, period_s: int) -> None:
        if not 1 <= period_s <= 3600:
            raise ValueError("sweep period outside its bound")
        self._tick, self._on_failure, self._period = tick, on_failure, period_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sensors-sweep", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._period + 1)

    def _run(self) -> None:
        while not self._stop.wait(self._period):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - a failed tick is announced, then the loop goes on
                self._on_failure()
