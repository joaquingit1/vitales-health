from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.domain.errors import failure
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings


class WakeSubscription:
    def __init__(self, queue: asyncio.Queue[None], close: Callable[[], None]) -> None:
        self.queue, self._close = queue, close

    def close(self) -> None:
        self._close()


class BoundedNotifier:
    """Wake hints contain no data. Dropped hints are recovered from the outbox."""

    def __init__(self, *, settings: HubSettings, ids: IdFactory) -> None:
        self.settings, self.ids = settings, ids
        self.streams: dict[str, asyncio.Queue[None]] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop | None] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> WakeSubscription:
        with self._lock:
            if len(self.streams) >= self.settings.integer("events.max_streams"):
                raise failure(ErrorCode.INTERNAL_ERROR)
            key = self.ids.new("stream")
            queue: asyncio.Queue[None] = asyncio.Queue(
                maxsize=self.settings.integer("events.stream_queue_size")
            )
            self.streams[key] = queue
            try:
                self._loops[key] = asyncio.get_running_loop()
            except RuntimeError:
                self._loops[key] = None

        def close() -> None:
            with self._lock:
                self.streams.pop(key, None)
                self._loops.pop(key, None)
                self._pending.discard(key)

        return WakeSubscription(queue, close)

    def _wake(self, key: str) -> None:
        with self._lock:
            self._pending.discard(key)
            queue = self.streams.get(key)
            if queue is not None and not queue.full():
                queue.put_nowait(None)

    def notify(self) -> None:
        with self._lock:
            ready = [(key, self._loops[key]) for key in sorted(self.streams) if key not in self._pending]
            self._pending.update(key for key, _ in ready)
        for key, loop in ready:
            if loop is None:
                self._wake(key)
            elif not loop.is_closed():
                loop.call_soon_threadsafe(self._wake, key)
