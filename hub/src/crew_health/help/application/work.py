"""After-commit work handed from commands to the help worker (E26 4.5): bounded, thread safe, never blocks."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkBatch:
    cancels: tuple[str, ...]
    dirty: tuple[str, ...]
    sweep_all: bool


class HelpWork:
    """After-commit work handed from commands to the worker; bounded, thread safe, never blocks a command."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancels: deque[str] = deque()
        self._dirty: dict[str, None] = {}
        self._sweep_all = False

    def cancel(self, help_turn_id: str) -> None:
        with self._lock:
            if len(self._cancels) < self._capacity:
                self._cancels.append(help_turn_id)
        self._wake.set()

    def session_changed(self, help_session_id: str) -> None:
        with self._lock:
            if len(self._dirty) < self._capacity:
                self._dirty[help_session_id] = None
            else:
                self._sweep_all = True
        self._wake.set()

    def sweep_all(self) -> None:
        with self._lock:
            self._sweep_all = True
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def wait(self, timeout_s: float) -> None:
        self._wake.wait(timeout_s)
        self._wake.clear()

    def take(self, limit: int) -> WorkBatch:
        with self._lock:
            cancels = tuple(self._cancels.popleft() for _ in range(min(limit, len(self._cancels))))
            dirty = tuple(list(self._dirty)[:limit])
            for key in dirty:
                del self._dirty[key]
            sweep, self._sweep_all = self._sweep_all, False
            if self._dirty or self._cancels:
                self._wake.set()
        return WorkBatch(cancels, dirty, sweep)
