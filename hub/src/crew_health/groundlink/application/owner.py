"""SYSTEM 3: at most one outstanding optional owner call, with a bounded wait."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from crew_health.kernel.errors import StorageUnavailable


@dataclass
class _Call:
    key: str
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None
    outcome: str | None = None


class OwnerCalls:
    def __init__(self, timeout_ms: int) -> None:
        self.timeout_s = timeout_ms / 1000
        self.pending: _Call | None = None

    def call(self, key: str, run: Callable[[], str]) -> str | None:
        pending = self.pending
        if pending is not None:
            if not pending.done.is_set():
                return "owner_busy"
            if isinstance(pending.error, StorageUnavailable):
                self.pending = None
                raise pending.error
            if pending.key == key:
                self.pending = None
                return self._result(pending)
            self.pending = None
        call = _Call(key)
        self.pending = call
        threading.Thread(target=self._run, args=(call, run), daemon=True, name="groundlink-owner").start()
        if not call.done.wait(self.timeout_s):
            return "owner_timeout"
        self.pending = None
        return self._result(call)

    @staticmethod
    def _run(call: _Call, run: Callable[[], str]) -> None:
        try:
            call.outcome = run()
        except BaseException as error:
            call.error = error
        finally:
            call.done.set()

    @staticmethod
    def _result(call: _Call) -> str | None:
        if isinstance(call.error, StorageUnavailable):
            raise call.error
        if call.error is not None:
            return "owner_failed"
        return None if call.outcome in ("applied", "duplicate", "buffered") else "owner_invalid_outcome"
