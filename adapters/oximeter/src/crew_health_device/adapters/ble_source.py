"""BLE transport. A physical radio needs the `ble` extra; a simulated twin plugs in through `client_factory`.

The client is BleakClient or anything with its shape. A client whose device identity declares it simulated
(`simulated` is True) makes this source SIM, so the hub labels its readings through the normal source rule;
nothing else in the pipeline knows about simulation."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import TimeoutError as FutureTimeout
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Protocol

from crew_health_device.application.ports import RawFrame, SourceDisconnected, SourceUnavailable
from crew_health_device.domain.acquisition import OriginMode


class BleClient(Protocol):
    """The part of BleakClient this transport uses."""

    def connect(self) -> Awaitable[object]: ...

    def start_notify(
        self, char_specifier: str, callback: Callable[[object, bytearray], None]
    ) -> Awaitable[None]: ...

    def disconnect(self) -> Awaitable[object]: ...


type ClientFactory = Callable[[str, Callable[[object], None]], BleClient]


def _bleak_factory() -> ClientFactory:
    try:
        from bleak import BleakClient  # type: ignore[import-not-found, unused-ignore]
    except ImportError as exc:
        raise SourceUnavailable("radio_absent") from exc

    def create(address: str, on_disconnect: Callable[[object], None]) -> BleClient:
        client: BleClient = BleakClient(address, disconnected_callback=on_disconnect)
        return client

    return create


class BleakFrameSource:
    def __init__(
        self,
        address: str,
        characteristic_uuids: Mapping[str, str],
        *,
        max_queue: int,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not address or not 1 <= max_queue <= 4096 or not 1 <= len(characteristic_uuids) <= 64:
            raise ValueError("invalid_ble_configuration")
        self._factory = client_factory
        self._address = address
        self._characteristics = tuple(sorted(characteristic_uuids.items()))
        self._queue: Queue[RawFrame | None] = Queue(maxsize=max_queue)
        self._disconnected = Event()
        self._overflow = Event()
        self._loop = asyncio.new_event_loop()
        self._client: BleClient | None = None
        self._running: tuple[Thread, BleClient] | None = None
        self._timeout = 0.0

    @property
    def origin(self) -> OriginMode:
        # The device identity decides, never a configuration flag: a simulated twin is SIM.
        return "SIM" if getattr(self._client, "simulated", False) is True else "LIVE"

    def open(self, timeout_ms: int) -> None:
        factory = self._factory if self._factory is not None else _bleak_factory()
        self._timeout = timeout_ms / 1000
        client = self._client = factory(self._address, self._lost)
        thread = Thread(target=self._loop.run_forever, daemon=True)
        self._running = (thread, client)
        thread.start()
        future = asyncio.run_coroutine_threadsafe(self._connect(client), self._loop)
        try:
            future.result(timeout=self._timeout)
        except (FutureTimeout, TimeoutError) as exc:
            future.cancel()
            raise SourceUnavailable("connect_timeout") from exc
        except Exception as exc:
            raise SourceUnavailable("device_absent") from exc

    async def _connect(self, client: BleClient) -> None:
        await asyncio.wait_for(client.connect(), self._timeout)
        for name, uuid in self._characteristics:
            await asyncio.wait_for(client.start_notify(uuid, self._notification(name)), self._timeout)

    def _notification(self, name: str) -> Callable[[object, bytearray], None]:
        def receive(sender: object, payload: bytearray) -> None:
            try:
                self._queue.put_nowait(RawFrame(name, bytes(payload)))
            except Full:
                self._overflow.set()
                self._disconnected.set()

        return receive

    def _lost(self, _client: object) -> None:
        # The end marker queues behind the notifications already received, so those are read first. A full
        # queue drops the marker; the reader then drains without waiting and sees the flag.
        self._disconnected.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass

    def next_frame(self, timeout_ms: int) -> RawFrame | None:
        # An overflow already lost frames, so it ends the reading at once.
        if self._overflow.is_set():
            raise SourceDisconnected("queue_overflow")
        if self._disconnected.is_set() and self._queue.empty():
            raise SourceDisconnected()
        try:
            frame = self._queue.get(timeout=timeout_ms / 1000)
        except Empty:
            return None
        if frame is None:
            raise SourceDisconnected()
        return frame

    async def _disconnect(self, client: BleClient) -> None:
        await asyncio.wait_for(client.disconnect(), self._timeout)

    def close(self) -> None:
        if self._running is None:
            self._loop.close()
            return
        thread, client = self._running
        future = asyncio.run_coroutine_threadsafe(self._disconnect(client), self._loop)
        try:
            future.result(timeout=self._timeout)
        except Exception as exc:
            future.cancel()
            raise SourceDisconnected() from exc
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            thread.join(timeout=self._timeout)
            if not thread.is_alive():
                self._loop.close()
