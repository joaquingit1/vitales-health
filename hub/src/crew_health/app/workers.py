"""The WorkerLifecycle create_app provides (SYSTEM 5.12): module workers report that they started, and
Runtime.shutdown stops them in reverse start order within one bounded budget."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.platform.domain.errors import fail

_LOGGER = "crew_health.app.workers"


class ModuleWorkers:
    def __init__(self, *, maximum: int, monotonic_ns: Callable[[], int]) -> None:
        self._maximum, self._monotonic_ns = maximum, monotonic_ns
        self._lock = threading.Lock()
        self._started: list[tuple[str, Callable[[float], bool]]] = []
        self._closed = False

    def started(self, name: str, stop: Callable[[float], bool]) -> None:
        """Record a started worker. A duplicate name, a full table or a start after shutdown is refused."""
        if not name or not name.isascii() or len(name) > 128:
            raise fail(ErrorCode.CONFIG_INVALID)
        with self._lock:
            if (
                self._closed
                or len(self._started) >= self._maximum
                or any(existing == name for existing, _ in self._started)
            ):
                raise fail(ErrorCode.INVALID_TRANSITION)
            self._started.append((name, stop))

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(name for name, _ in self._started)

    def shutdown(self, timeout_ms: int) -> tuple[str, ...]:
        """Stop every started worker, last started first, sharing one deadline; later starts are refused. Returns
        the workers that did not confirm a stop. A failing stop is logged by type and never skips the others."""
        with self._lock:
            self._closed = True
            workers, self._started = list(reversed(self._started)), []
        deadline = self._monotonic_ns() + timeout_ms * 1_000_000
        stuck: list[str] = []
        for name, stop in workers:
            remaining = max(0, deadline - self._monotonic_ns()) / 1_000_000_000
            try:
                stopped = stop(remaining)
            except Exception as error:  # noqa: BLE001  # shutdown continues; the worker is reported as stuck
                logging.getLogger(_LOGGER).error("worker %s failed to stop: %s", name, type(error).__name__)
                stopped = False
            if not stopped:
                stuck.append(name)
        return tuple(stuck)
