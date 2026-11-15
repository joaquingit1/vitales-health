"""Thermometer over BLE: the simulated twin by default (AGENTS.md section 4), a paired device as the extra run."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crew_health_sim_clinical.ble import client_factory
from crew_health_sim_clinical.twin import Twin

from crew_health_adapter_thermometer.__main__ import _source
from crew_health_adapter_thermometer.family import TEMPERATURE_MEASUREMENT, TEMPERATURE_MEASUREMENT_UUID
from crew_health_adapter_thermometer.htm import HTMDecoder
from crew_health_device.adapters.ble_source import BleakFrameSource

from .helpers import settings

UUIDS = {TEMPERATURE_MEASUREMENT: TEMPERATURE_MEASUREMENT_UUID}


def _twin_source(scenario: str) -> BleakFrameSource:
    twin = Twin(
        "thermometer", scenario, 5, start=datetime(2026, 11, 14, 9, tzinfo=UTC), interval_s=Decimal("0.005")
    )
    return BleakFrameSource(
        twin.identity.name, UUIDS, max_queue=settings().max_frames, client_factory=client_factory(twin)
    )


def _first_temperature(source: BleakFrameSource) -> Decimal | None:
    try:
        source.open(2000)
        raw = source.next_frame(2000)
    finally:
        source.close()
    assert raw is not None
    return HTMDecoder().decode(raw.characteristic, raw.payload).components[0].value


@pytest.mark.trace("T-E07-TEMP-05", "R26", "E07-03", "AGENTS 4")
@pytest.mark.hardware
def test_thermometer_notification_decodes() -> None:
    """CHC_E07_THERMOMETER_ADDRESS runs the same check against a paired thermometer instead of the twin."""
    bounds = settings()
    address = os.environ.get("CHC_E07_THERMOMETER_ADDRESS")
    source = (
        BleakFrameSource(address, UUIDS, max_queue=bounds.max_frames) if address else _twin_source("normal")
    )
    try:
        source.open(bounds.connect_timeout_ms)
        assert source.origin == ("LIVE" if address else "SIM")
        raw = source.next_frame(bounds.frame_timeout_ms)
        assert raw is not None, "No HTM notification received within the configured timeout"
        decoded = HTMDecoder().decode(raw.characteristic, raw.payload)
        assert decoded.components[0].value is not None and decoded.components[0].value.is_finite()
    finally:
        source.close()


@pytest.mark.trace("T-E07-TEMP-05", "AGENTS 4")
def test_twin_fever_reads_higher_than_the_normal_twin_through_the_decoder() -> None:
    normal = _first_temperature(_twin_source("normal"))
    fever = _first_temperature(_twin_source("fever"))
    assert normal is not None and fever is not None and fever > normal


@pytest.mark.trace("E07-03", "AGENTS 4")
def test_sim_source_option_selects_the_twin_and_labels_it_sim() -> None:
    factory, origin = _source("sim:fever:5", settings())
    assert origin == "SIM"
    assert isinstance(factory(), BleakFrameSource)
    with pytest.raises(ValueError):
        _source("sim:finger_out:5", settings())
