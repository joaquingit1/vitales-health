"""SYSTEM 3: one outstanding optional call per boundary, bounded waits and no queue."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from crew_health.catchup.domain.errors import CatchUpUnavailable
from crew_health.kernel.errors import StorageUnavailable


@dataclass
class _Call[T]:
    done: threading.Event = field(default_factory=threading.Event)
    result: T | None = None
    error: BaseException | None = None


class BoundedCall:
    def __init__(self, timeout_ms: int) -> None:
        self._timeout_s = timeout_ms / 1000
        self._lock = threading.Lock()
        self._pending: _Call[object] | None = None

    def call[T](self, run: Callable[[], T]) -> T:
        with self._lock:
            previous = self._pending
            if previous is not None:
                if not previous.done.is_set():
                    raise CatchUpUnavailable()
                self._pending = None
                if isinstance(previous.error, StorageUnavailable):
                    raise previous.error
            call = _Call[T]()
            self._pending = cast(_Call[object], call)

        def invoke() -> None:
            try:
                call.result = run()
            except BaseException as error:
                call.error = error
            finally:
                call.done.set()

        threading.Thread(target=invoke, daemon=True, name="catchup-provider").start()
        if not call.done.wait(self._timeout_s):
            raise CatchUpUnavailable()
        with self._lock:
            if self._pending is call:
                self._pending = None
        if call.error is not None:
            raise call.error
        # T may itself include None, which is a valid provider answer.
        return cast(T, call.result)
