"""Hardware path (T-E23-062): the same Bluetooth Environmental Sensing reader, first against the simulated twin in
the default run, then against a real device in an explicit, separate run.

Default run: `pytest -c adapters/habitat/pyproject.toml adapters/habitat/tests/test_hardware.py` runs the twin test
and deselects the `hardware` one. Hardware run: `... -m hardware` with CREW_HEALTH_BLE_ADDRESS set to a device
that exposes 0x181A; without a device it reports not measured. LIVE stays refused until R-E23-2 criteria exist, so
the real device run checks the reader and the parser, not an evaluated reading.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from support import make_rig

from crew_health_adapter_habitat.ble_ess import ESS_CHARACTERISTICS, BleakLink, EssSource, origin_of
from crew_health_adapter_habitat.errors import SourceUnavailable
from crew_health_adapter_habitat.feeds import Columns, Feed, Pace
from crew_health_adapter_habitat.http_client import HubClient, Sender
from crew_health_adapter_habitat.pipeline import Pipeline
from crew_health_adapter_habitat.ports import Settings
from crew_health_adapter_habitat.sim import TwinBleLink
from crew_health_sim_habitat import EssTwin


def ess_feed() -> Feed:
    return Feed(
        feed_id="ess", kind="sim", adapter_id="adp_replay_osdr", channel_id="ch_co2_cabin_sim", unit="ppm",
        pace=Pace("burst"), replay=None, file=None, columns=Columns("t", "v", "u"), row_filter=None, carry_naive=False,
        window_from=None, window_to=None, sim={"type": "twin"}, cabin_pressure=None, criteria=None, basis={},
    )  # fmt: skip


@pytest.mark.trace("T-E23-062")
def test_the_ble_reader_runs_end_to_end_against_the_simulated_device_by_default(
    tmp_path: Path, twin_data: Path
) -> None:
    rig = make_rig(tmp_path)
    link = TwinBleLink(EssTwin(0x2B8C, "nominal", twin_data, seed=1))
    origin = origin_of(link.identity(), None)  # the twin declares itself simulated: SIM
    source = EssSource(link, "ch_co2_cabin_sim", 0x2B8C, rig.clock)
    count = 0

    def stop() -> bool:
        nonlocal count
        count += 1
        return count > 25

    sender = Sender(
        HubClient(rig.hub.client(), "adp_replay_osdr", "tok", rig.settings), rig.queue, rig.sleeper
    )
    summary = Pipeline(
        ess_feed(), source, rig.queue, sender, rig.health, rig.clock, rig.sleeper, rig.settings, origin
    ).run(stop)
    assert origin == "SIM" and summary.enqueued == 25 and rig.queue.depth() == 0
    rig.queue.close()


@pytest.mark.hardware
@pytest.mark.trace("T-E23-062")
def test_a_real_environmental_sensing_device_streams_a_parseable_notification() -> None:
    address = os.environ.get("CREW_HEALTH_BLE_ADDRESS")
    if not address:
        pytest.skip("not measured: no device address in CREW_HEALTH_BLE_ADDRESS")
    settings = Settings()
    link = BleakLink(address, tuple(sorted(ESS_CHARACTERISTICS)), settings)
    assert link.identity().simulated is False  # a real device never declares itself simulated
    try:
        first = next(link.notifications())
    except SourceUnavailable as error:
        pytest.fail("device gave no notification: " + str(error))
    finally:
        link.close()
    assert first.uuid16 in ESS_CHARACTERISTICS and len(first.payload) == len(struct.pack("<H", 0))
