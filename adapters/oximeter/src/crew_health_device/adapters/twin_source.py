"""Simulated twin input (AGENTS.md section 4): the twin's BleakClient-shaped client behind the normal BLE
transport, so the same notification queue, decoder, checks and sender run. The twin's device identity says
it is simulated, which makes the source SIM; nothing downstream has a simulation branch."""

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health_device.adapters.ble_source import BleakFrameSource
from crew_health_device.application.ports import FrameSource

type Family = Literal["oximeter", "cuff", "thermometer"]


def parse_twin_spec(spec: str) -> tuple[str, int]:
    """`<scenario>:<seed>`, for example `normal:7`."""
    scenario, _, seed = spec.partition(":")
    if not scenario or not seed.isdigit() or len(seed) > 9:
        raise ValueError("invalid_source")
    return scenario, int(seed)


def twin_source(
    family: Family,
    spec: str,
    characteristic_uuids: Mapping[str, str],
    *,
    max_queue: int,
    start: Callable[[], datetime],
    interval_s: Decimal,
    pace_s: Decimal | None = None,
) -> Callable[[], FrameSource]:
    scenario, seed = parse_twin_spec(spec)
    try:
        from crew_health_sim_clinical.ble import client_factory
        from crew_health_sim_clinical.twin import Twin
    except ImportError as exc:  # pragma: no cover: the `sim` extra is a declared test dependency
        raise ValueError("simulator_not_installed") from exc
    Twin(family, scenario, seed, start=start(), interval_s=interval_s)  # unknown scenario fails at startup
    acquisitions = 0

    def create() -> FrameSource:
        # Each acquisition is a new reading from the same simulated device: a fresh, still deterministic seed.
        nonlocal acquisitions
        twin = Twin(family, scenario, seed + acquisitions, start=start(), interval_s=interval_s)
        acquisitions += 1
        return BleakFrameSource(
            twin.identity.name,
            characteristic_uuids,
            max_queue=max_queue,
            client_factory=client_factory(twin, pace_s=pace_s),
        )

    return create
