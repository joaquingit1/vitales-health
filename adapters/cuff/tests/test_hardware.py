"""Cuff over BLE: the simulated twin by default (AGENTS.md section 4), a paired cuff as the extra run."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crew_health_sim_clinical.ble import client_factory
from crew_health_sim_clinical.twin import Twin

from crew_health_adapter_cuff.__main__ import _source
from crew_health_adapter_cuff.bpm import BPMDecoder
from crew_health_adapter_cuff.family import MEASUREMENT, MEASUREMENT_UUID
from crew_health_device.adapters.ble_source import BleakFrameSource

from .helpers import settings


def _twin_source(scenario: str) -> BleakFrameSource:
    twin = Twin("cuff", scenario, 3, start=datetime(2026, 11, 14, 9, tzinfo=UTC), interval_s=Decimal("0.005"))
    return BleakFrameSource(
        twin.identity.name,
        {MEASUREMENT: MEASUREMENT_UUID},
        max_queue=settings().max_frames,
        client_factory=client_factory(twin),
    )


@pytest.mark.trace("T-E07-BP-05", "R26", "E07-03", "AGENTS 4")
@pytest.mark.hardware
def test_cuff_notification_decodes() -> None:
    """CHC_E07_CUFF_ADDRESS runs the same check against a paired cuff instead of the twin."""
    options = settings()
    address = os.environ.get("CHC_E07_CUFF_ADDRESS")
    source = (
        BleakFrameSource(address, {MEASUREMENT: MEASUREMENT_UUID}, max_queue=options.max_frames)
        if address
        else _twin_source("normal")
    )
    try:
        source.open(options.connect_timeout_ms)
        assert source.origin == ("LIVE" if address else "SIM")
        raw = source.next_frame(options.frame_timeout_ms)
        assert raw is not None, "No blood pressure notification received within the configured timeout"
        frame = BPMDecoder().decode(raw.characteristic, raw.payload)
        assert frame.characteristic == MEASUREMENT
        assert all(component.value is None or component.value.is_finite() for component in frame.components)
    finally:
        source.close()


@pytest.mark.trace("T-E07-BP-05", "AGENTS 4")
def test_twin_hypertension_reads_higher_than_the_normal_twin_through_the_decoder() -> None:
    systolic: dict[str, Decimal | None] = {}
    for scenario in ("normal", "hypertension"):
        source = _twin_source(scenario)
        try:
            source.open(2000)
            raw = source.next_frame(2000)
        finally:
            source.close()
        assert raw is not None
        decoded = BPMDecoder().decode(raw.characteristic, raw.payload)
        systolic[scenario] = next(c.value for c in decoded.components if c.field == "systolic")
    normal, high = systolic["normal"], systolic["hypertension"]
    assert normal is not None and high is not None and high > normal


@pytest.mark.trace("E07-03", "AGENTS 4")
def test_sim_source_option_selects_the_twin_and_labels_it_sim() -> None:
    factory, origin = _source("sim:normal:3", settings())
    assert origin == "SIM"
    assert isinstance(factory(), BleakFrameSource)
    with pytest.raises(ValueError, match="invalid_source"):
        _source("sim:normal", settings())
