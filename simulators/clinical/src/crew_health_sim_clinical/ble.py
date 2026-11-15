"""A bounded in-process BLE twin transport without a radio or bleak dependency."""

import asyncio
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from .twin import MAX_EVENTS, Disconnect, Twin

type NotifyCallback = Callable[[object, bytearray], None]


class ClientFactory(Protocol):
    def __call__(
        self,
        address: str,
        disconnected_callback: Callable[[SimulatedBleClient], None] | None = None,
    ) -> SimulatedBleClient: ...


class SimulatedBleClient:
    simulated = True

    def __init__(
        self,
        address: str,
        disconnected_callback: Callable[[SimulatedBleClient], None] | None = None,
        *,
        twin: Twin,
        pace_s: Decimal | None = None,
    ) -> None:
        if not address.strip():
            raise ValueError("address required")
        if pace_s is not None and (not pace_s.is_finite() or pace_s < 0):
            raise ValueError("pace must be finite and nonnegative")
        self.identity = twin.identity
        self._twin = twin
        # Wall time between deliveries. The twin's own clock (its timestamps) always steps by interval_s; a test
        # may deliver faster than the simulated clock runs, a running adapter keeps the device's real pace.
        self._pace = float(twin.interval_s if pace_s is None else pace_s)
        self._disconnected_callback = disconnected_callback
        self._connected = False
        self._callbacks: dict[str, NotifyCallback] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        self._connected = True
        return True

    def _uuid(self, specifier: str) -> str:
        specifier = specifier.lower()
        uuid = self._twin.characteristics.get(specifier, specifier)
        if uuid not in self._twin.characteristics.values():
            raise ValueError("unknown characteristic")
        return uuid

    async def start_notify(self, char_specifier: str, callback: NotifyCallback) -> None:
        uuid = self._uuid(char_specifier)
        if not self._connected:
            raise ValueError("device is disconnected")
        self._callbacks[uuid] = callback
        if self._task is None:
            self._task = asyncio.create_task(self._deliver())

    async def stop_notify(self, char_specifier: str) -> None:
        self._callbacks.pop(self._uuid(char_specifier), None)

    async def _deliver(self) -> None:
        try:
            for event in self._twin.iter_events(MAX_EVENTS):
                if isinstance(event, Disconnect):
                    self._connected = False
                    self._callbacks.clear()
                    if self._disconnected_callback is not None:
                        self._disconnected_callback(self)
                    return
                callback = self._callbacks.get(event.uuid)
                if callback is not None:
                    callback(event.uuid, bytearray(event.payload))
                await asyncio.sleep(self._pace)
        finally:
            self._task = None

    async def disconnect(self) -> bool:
        self._connected = False
        self._callbacks.clear()
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return True


def client_factory(twin: Twin, *, pace_s: Decimal | None = None) -> ClientFactory:
    def factory(
        address: str,
        disconnected_callback: Callable[[SimulatedBleClient], None] | None = None,
    ) -> SimulatedBleClient:
        return SimulatedBleClient(address, disconnected_callback, twin=twin, pace_s=pace_s)

    return factory
