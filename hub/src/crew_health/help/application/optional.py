"""Bounded calls into optional providers (E26 4.5, AGENTS rule 3): help never waits on an optional part.

Each call runs on its own daemon thread and the caller waits at most the configured timeout; a failure or a
timeout yields the designed fallback. A provider whose earlier call is still running past its timeout is treated
as stuck: later calls to it return the fallback at once instead of queueing. At most `slots` calls run at the
same time; with every slot taken, the fallback is returned at once, so nothing queues without bound.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _Outcome[T]:
    done: threading.Event = field(default_factory=threading.Event)
    value: T | None = None
    failed: bool = False


class OptionalCalls:
    def __init__(self, timeout_s: float = 0.1, slots: int = 8) -> None:
        self._timeout = timeout_s
        self._slots = threading.BoundedSemaphore(slots)
        self._lock = threading.Lock()
        self._stuck: set[str] = set()

    @property
    def timeout_s(self) -> float:
        return self._timeout

    def call[T](self, name: str, call: Callable[[], T], fallback: T) -> T:
        with self._lock:
            if name in self._stuck:
                return fallback
        if not self._slots.acquire(blocking=False):
            return fallback
        outcome: _Outcome[T] = _Outcome()

        def run() -> None:
            try:
                outcome.value = call()
            except Exception:  # noqa: BLE001  # an optional provider's failure degrades only its own feature
                outcome.failed = True
            finally:
                outcome.done.set()
                with self._lock:
                    self._stuck.discard(name)
                self._slots.release()

        try:
            threading.Thread(target=run, name=f"help-optional-{name}", daemon=True).start()
        except RuntimeError:
            self._slots.release()
            return fallback
        if not outcome.done.wait(self._timeout):
            with self._lock:
                if not outcome.done.is_set():
                    self._stuck.add(name)
            return fallback
        if outcome.failed:
            return fallback
        return outcome.value  # type: ignore[return-value]  # set by `run` before `done`
