"""Shared-runtime capture, restart and journal delivery for cuff recordings."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health_adapter_cuff.bpm import BPMDecoder
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.delivery import deliver
from crew_health_device.application.health import HealthTracker
from crew_health_device.application.profiles import ProfileStore

from .helpers import Clock, Hub, binding, journal, profile, recording, settings


@pytest.mark.trace("T-E07-BP-01", "T-E07-BP-03", "R08", "E07-01", "V11", "V16")
def test_recorded_capture_durable_restart_duplicate_and_exact_delivery(tmp_path: Path) -> None:
    clock = Clock()
    health = HealthTracker(clock, 1000)
    source = recording([{"characteristic": "blood_pressure_measurement", "payload_hex": "00780050005d00"}])
    worker = AcquisitionService(
        BPMDecoder(),
        ProfileStore(profile),
        lambda: source,
        journal(tmp_path),
        clock,
        settings(),
        "cuff-1",
        health=health,
    )
    request = binding()
    assert worker.start(request) == "started"
    draft = worker.capture(request.acquisition_id)
    assert draft.outcome == "success" and draft.failure_reason is None
    assert len(draft.observations) == 1
    observation = draft.observations[0]
    assert (
        observation.value == Decimal(120)
        and observation.unit == profile().components[0].units["millimetre_of_mercury"]
    )
    assert observation.device_id == request.device_id and observation.acquisition_method == "device"
    assert observation.origin_mode == "SIM" and observation.patient_id == request.patient_id
    assert observation.measurement_request_id == request.request_id
    assert observation.context == (("adapter_profile", "bp-test@1"),)
    assert observation.observed_at.tzinfo is not None
    assert health.state() == "connected"
    persisted = journal(tmp_path).get("e07:acq-bp-1:1")
    assert persisted is not None and persisted.state == "pending"
    body = json.loads(persisted.body, parse_float=Decimal)
    assert body["observations"][0]["value"] == 120 and body["observations"][0]["unit"] == "mm[Hg]"
    restarted = AcquisitionService(
        BPMDecoder(), ProfileStore(profile), lambda: source, journal(tmp_path), Clock(), settings(), "cuff-1"
    )
    assert restarted.start(request) == "duplicate"
    hub = Hub()
    result = deliver(journal(tmp_path), hub, settings())
    assert result.sent == 1 and result.held == 0 and result.failed_transport == 0
    assert hub.calls == [(request.request_id, persisted.key, persisted.body)]
    assert not journal(tmp_path).records()


@pytest.mark.trace("T-E07-BP-03", "R08", "E07-01", "V11", "V16")
@pytest.mark.parametrize("event,reason", [("disconnect", "disconnected"), ("device_absent", "device_absent")])
def test_unavailable_source_fails_without_a_value(tmp_path: Path, event: str, reason: str) -> None:
    clock = Clock()
    health = HealthTracker(clock, 1000)
    source = recording([{"event": event}])
    worker = AcquisitionService(
        BPMDecoder(),
        ProfileStore(profile),
        lambda: source,
        journal(tmp_path),
        clock,
        settings(),
        "cuff-1",
        health=health,
    )
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-bp-1")
    assert draft.outcome == "failed" and draft.failure_reason == reason and not draft.observations
    assert health.state() == "disconnected"
    persisted = journal(tmp_path).get("e07:acq-bp-1:1")
    assert persisted is not None
    assert json.loads(persisted.body)["observations"] == []


@pytest.mark.trace("T-E07-BP-03", "E07-03", "V16")
def test_restart_of_reserved_but_uncaptured_request_never_recaptures(tmp_path: Path) -> None:
    source = recording([{"event": "device_absent"}])
    first = AcquisitionService(
        BPMDecoder(), ProfileStore(profile), lambda: source, journal(tmp_path), Clock(), settings(), "cuff-1"
    )
    assert first.start(binding()) == "started"
    second = AcquisitionService(
        BPMDecoder(), ProfileStore(profile), lambda: source, journal(tmp_path), Clock(), settings(), "cuff-1"
    )
    assert second.start(binding()) == "duplicate"
    assert second.status("acq-bp-1", 1) == "unknown"
    assert second.cancel("acq-bp-1", 1) == "unknown"
    assert journal(tmp_path).get("e07:acq-bp-1:1") is None


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_unit_change_in_window_never_combines_or_converts_samples(tmp_path: Path) -> None:
    from .helpers import component_document, profile_document

    document = profile_document()
    component_document(document)["units"] = {"millimetre_of_mercury": "mm[Hg]", "kilopascal": "kPa"}
    source = recording(
        [
            {"characteristic": "blood_pressure_measurement", "payload_hex": "00780050005d00"},
            {"characteristic": "blood_pressure_measurement", "payload_hex": "01a0f06bf07cf0"},
        ]
    )
    worker = AcquisitionService(
        BPMDecoder(),
        ProfileStore(lambda: profile(document)),
        lambda: source,
        journal(tmp_path),
        Clock(),
        replace(settings(), max_frames=2),
        "cuff-1",
    )
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-bp-1")
    assert draft.outcome == "rejected" and draft.failure_reason == "unit_changed"
    assert not draft.observations
