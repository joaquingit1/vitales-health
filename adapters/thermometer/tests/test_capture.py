"""Thermometer captures through the shared production runtime and durable journal."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.contracts_gen.schemas.requests_schema import AttemptCreate
from crew_health_adapter_thermometer.htm import HTMDecoder
from crew_health_device.application.delivery import deliver
from crew_health_device.domain.acquisition import ReceivedFrame, build_attempt
from crew_health_device.domain.ieee11073 import Special

from .helpers import (
    Clock,
    Hub,
    binding,
    component,
    journal,
    profile,
    profile_document,
    raw,
    service,
    settings,
)


@pytest.mark.trace("T-E07-TEMP-01", "T-E07-TEMP-03", "R08", "E07-01", "V11", "V16")
def test_capture_journals_exact_attributed_value_and_delivers_after_restart(tmp_path: Path) -> None:
    clock = Clock()
    worker = service(tmp_path, [raw()], clock=clock)
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "success" and draft.failure_reason is None
    assert draft.sequence == binding().reserved_sequence
    assert len(draft.observations) == 1
    observation = draft.observations[0]
    assert observation.value == Decimal("36.6")
    assert observation.unit == profile().components[0].units["degree_celsius"] == "Cel"
    assert observation.patient_id == "crew-1" and observation.device_id == "temp-1"
    assert observation.acquisition_method == "device" and observation.origin_mode == "SIM"
    assert observation.measurement_request_id == "req-1"
    assert observation.observed_at == clock.receptions[1]
    persisted = journal(tmp_path).get("e07:acq-1:1")
    assert persisted is not None and persisted.state == "pending"
    AttemptCreate.model_validate_json(persisted.body)
    assert b'"value":36.6' in persisted.body
    parsed = json.loads(persisted.body, parse_float=Decimal)
    assert parsed["observations"][0]["value"] == Decimal("36.6")
    restarted = service(tmp_path, [{"event": "device_absent"}])
    assert restarted.start(binding()) == "duplicate"
    hub = Hub()
    report = deliver(journal(tmp_path), hub, settings())
    assert report.sent == 1 and report.held == report.retry == report.failed_transport == 0
    assert hub.calls == [("req-1", "e07:acq-1:1", persisted.body)]
    assert journal(tmp_path).records() == ()
    assert deliver(journal(tmp_path), hub, settings()).sent == 0
    assert len(hub.calls) == 1


@pytest.mark.trace("T-E07-TEMP-03", "R08", "E07-01", "V11", "V16")
@pytest.mark.parametrize(
    "event,reason",
    [
        ("disconnect", "disconnected"),
        ("device_absent", "device_absent"),
        ("radio_absent", "radio_absent"),
        ("timeout", "timeout"),
    ],
)
def test_unavailable_capture_has_no_value_and_is_durable(tmp_path: Path, event: str, reason: str) -> None:
    worker = service(tmp_path, [{"event": event}])
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "failed" and draft.failure_reason == reason
    assert not draft.observations
    persisted = journal(tmp_path).get("e07:acq-1:1")
    assert persisted is not None
    assert json.loads(persisted.body)["observations"] == []
    assert worker.status("acq-1", 1) == "failed"


@pytest.mark.trace("T-E07-TEMP-03", "E07-03", "V16")
def test_start_reserved_before_capture_prevents_recapture_after_restart(tmp_path: Path) -> None:
    worker = service(tmp_path, [raw()])
    assert worker.start(binding()) == "started"
    restarted = service(tmp_path, [raw()])
    assert restarted.start(binding()) == "duplicate"
    assert restarted.status("acq-1", 1) == "unknown"
    assert all(record.kind == "acquisition" for record in journal(tmp_path).records())


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
def test_fahrenheit_without_profile_mapping_is_rejected_without_affine_conversion(tmp_path: Path) -> None:
    # Bit 0 = Fahrenheit; raw 0xFF0003DA: 986 * 10^-1 = 98.6, never converted to Celsius.
    worker = service(tmp_path, [raw("01da0300ff")])
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "rejected" and draft.failure_reason == "unsupported_unit:degree_fahrenheit"
    assert draft.observations == ()


@pytest.mark.trace("T-E07-TEMP-02", "T-E07-TEMP-03", "E07-03", "V15")
@pytest.mark.parametrize("site", range(1, 10))
def test_assigned_sites_reach_device_site_context(tmp_path: Path, site: int) -> None:
    worker = service(tmp_path, [raw("046e0100ff" + bytes([site]).hex())])
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "success"
    assert draft.observations[0].context == (
        ("adapter_profile", "htm-test@1"),
        ("device_site_code", str(site)),
    )


@pytest.mark.trace("T-E07-TEMP-02", "T-E07-TEMP-03", "V15")
@pytest.mark.parametrize("timestamp", ["00000000000000", "d0070101000000"])
def test_unknown_device_clock_never_replaces_reception_time(tmp_path: Path, timestamp: str) -> None:
    # Year 0 means unknown; year 0x07D0 = 2000 is valid but still untrusted.
    clock = Clock()
    worker = service(tmp_path, [raw("026e0100ff" + timestamp)], clock=clock)
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "success"
    assert draft.observations[0].observed_at == clock.receptions[1]
    assert draft.observations[0].observed_at.year == 2026


@pytest.mark.trace("T-E07-TEMP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "raw_float,missing",
    [
        (0x007FFFFF, Special.NOT_A_NUMBER),
        (0x00800000, Special.NOT_AT_THIS_RESOLUTION),
        (0x007FFFFE, Special.POSITIVE_INFINITY),
        (0x00800002, Special.NEGATIVE_INFINITY),
        (0x00800001, Special.RESERVED),
    ],
)
def test_special_float_never_becomes_an_observation(tmp_path: Path, raw_float: int, missing: Special) -> None:
    worker = service(tmp_path, [raw("00" + raw_float.to_bytes(4, "little").hex())])
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "rejected" and draft.failure_reason == "insufficient_samples"
    assert not draft.observations
    assert draft.quality_notes == ("component_missing:temperature:" + missing, "unchecked:rate:temperature")


@pytest.mark.trace("T-E07-TEMP-04", "V75")
def test_scratch_range_edit_changes_verdict_without_code_change(tmp_path: Path) -> None:
    document = profile_document()
    first = service(tmp_path / "first", [raw()], document=document)
    assert first.start(binding()) == "started"
    assert first.capture("acq-1").outcome == "success"
    component(document)["range_max"] = "36"
    second = service(tmp_path / "second", [raw()], document=document)
    assert second.start(binding()) == "started"
    draft = second.capture("acq-1")
    assert draft.outcome == "rejected" and draft.failure_reason == "out_of_device_range"
    assert not draft.observations


@pytest.mark.trace("T-E07-TEMP-03", "E07-03", "V11")
def test_htm_has_no_subject_index_and_binding_patient_is_preserved(tmp_path: Path) -> None:
    worker = service(tmp_path, [raw()])
    subject = replace(binding(), patient_id="crew-2", expected_user_index=7)
    assert worker.start(subject) == "started"
    draft = worker.capture("acq-1")
    assert draft.observations[0].patient_id == "crew-2"
    decoded = HTMDecoder().decode("temperature_measurement", bytes.fromhex("006e0100ff"))
    assert decoded.user_index is None
    # HTM cannot verify a subject. Shared runtime rejects a mismatched index if another protocol supplies it.
    clock = Clock()
    refused = build_attempt(
        subject,
        profile(),
        [ReceivedFrame(replace(decoded, user_index=8), clock.now(), 1)],
        started_at=clock.now(),
        ended_at=clock.now(),
        source_origin="SIM",
        end="window_complete",
    )
    assert refused.outcome == "rejected" and refused.failure_reason == "wrong_subject"
    assert not refused.observations


@pytest.mark.trace("T-E07-TEMP-03", "V15", "V16")
def test_bad_frame_cannot_fabricate_value_and_following_real_frame_survives(tmp_path: Path) -> None:
    worker = service(tmp_path, [raw("00"), raw()], max_frames=2)
    assert worker.start(binding()) == "started"
    draft = worker.capture("acq-1")
    assert draft.outcome == "success" and draft.observations[0].value == Decimal("36.6")
    assert draft.quality_notes == ("frame:truncated", "unchecked:rate:temperature")
