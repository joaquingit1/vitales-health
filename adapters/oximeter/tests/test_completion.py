"""When a reading is complete, when a sensor is stuck, and what the device's own clock leaves behind.

Review fixes B1 (a link lost mid reading is never a success), M1 (stuck from frame evidence, not from equal values)
and minors 2, 3 and 7 (start conflicts, device time kept, a twin is a connected SIM source).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.health import manifest
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.domain.acquisition import AttemptDraft, CaptureEnd, ReceivedFrame, build_attempt
from crew_health_device.domain.errors import FrameError, ProfileError
from crew_health_device.domain.ieee11073 import DeviceDateTime
from crew_health_device.domain.profile import DeviceProfile, parse_profile
from crew_health_device.domain.readings import Component, DecodedFrame

from .helpers import Clock, binding, profile, profile_document, settings

START = datetime(2026, 11, 14, tzinfo=UTC)


def _frame(
    value: str, *, at_s: int, stamp: int | None = None, clock_set: bool | None = None
) -> ReceivedFrame:
    decoded = PLXDecoder().decode("plx_continuous", bytes.fromhex("00 62 00 48 00"))
    if isinstance(decoded, FrameError):
        raise RuntimeError("invalid test frame")
    device_time = None if stamp is None else DeviceDateTime(2026, 11, 14, 10, 0, stamp)
    frame: DecodedFrame = replace(
        decoded,
        components=(Component("spo2", Decimal(value), None, "percent"),),
        device_time=device_time,
        device_clock_set=clock_set,
    )
    return ReceivedFrame(frame, START + timedelta(seconds=at_s), at_s * 1_000_000_000)


def _attempt(
    frames: tuple[ReceivedFrame, ...], end: CaptureEnd, document: DeviceProfile | None = None
) -> AttemptDraft:
    return build_attempt(
        binding(),
        document or profile(),
        frames,
        started_at=START,
        ended_at=START,
        source_origin="LIVE",
        end=end,
    )


@pytest.mark.trace("T-E07-OX-01", "E07-01", "V11")
@pytest.mark.parametrize("end", ["disconnected", "timeout"])
def test_a_continuous_reading_cut_short_is_unavailable_never_its_last_value(end: CaptureEnd) -> None:
    result = _attempt((_frame("97", at_s=0), _frame("98", at_s=1)), end)
    assert (result.outcome, result.failure_reason, result.observations) == ("failed", end, ())


@pytest.mark.trace("T-E07-OX-01", "E07-01", "V11")
@pytest.mark.parametrize("end", ["disconnected", "timeout"])
def test_a_spot_device_that_ends_after_its_measurement_records_it(end: CaptureEnd) -> None:
    spot = replace(profile(), completion="measurement")
    result = _attempt((_frame("97", at_s=0),), end, spot)
    assert result.outcome == "success" and result.observations[0].value == Decimal(97)
    nothing = _attempt((), end, spot)
    assert (nothing.outcome, nothing.failure_reason, nothing.observations) == ("failed", end, ())


@pytest.mark.trace("T-E07-OX-01", "E07-01", "V11")
@pytest.mark.parametrize("end", ["radio_absent", "device_absent"])
def test_a_source_that_never_opened_fails_for_every_completion_rule(end: CaptureEnd) -> None:
    for completion in ("window", "measurement"):
        result = _attempt((), end, replace(profile(), completion=completion))
        assert (result.outcome, result.failure_reason) == ("failed", end)


@pytest.mark.trace("T-E07-OX-02", "R26", "V15")
def test_a_repeated_timestamped_measurement_is_a_stuck_sensor() -> None:
    frozen = (_frame("98", at_s=0, stamp=5), _frame("98", at_s=1, stamp=5))
    result = _attempt(frozen, "window_complete")
    assert (result.outcome, result.failure_reason, result.observations) == ("rejected", "stuck", ())
    assert result.quality_notes == ("frame:repeated_measurement",)


@pytest.mark.trace("T-E07-OX-02", "R26", "V15")
def test_equal_values_with_advancing_or_absent_timestamps_are_a_good_reading() -> None:
    advancing = _attempt((_frame("98", at_s=0, stamp=5), _frame("98", at_s=1, stamp=6)), "window_complete")
    untimed = _attempt(tuple(_frame("98", at_s=second) for second in range(30)), "window_complete")
    assert advancing.outcome == "success" and untimed.outcome == "success"


@pytest.mark.trace("T-E07-OX-02", "R26", "V15")
def test_a_steady_profile_rejects_a_reading_that_never_settled() -> None:
    steady = replace(profile(), steady_reading=True)
    moving = _attempt(
        (_frame("95", at_s=0), _frame("96", at_s=1), _frame("97", at_s=2)), "window_complete", steady
    )
    assert (moving.outcome, moving.failure_reason) == ("rejected", "not_steady")
    settled = _attempt(
        (_frame("96", at_s=0), _frame("97", at_s=1), _frame("97", at_s=2)), "window_complete", steady
    )
    assert settled.outcome == "success" and settled.observations[0].value == Decimal(97)


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_a_rule_without_sourced_bounds_says_so_in_the_quality_notes() -> None:
    document = profile()
    rule = replace(document.components[0], range_min=None, range_max=None)
    result = _attempt((_frame("97", at_s=0),), "window_complete", replace(document, components=(rule,)))
    assert result.outcome == "success"
    assert result.quality_notes == ("unchecked:range:spo2", "unchecked:rate:spo2")


@pytest.mark.trace("T-E07-OX-01", "V11")
@pytest.mark.parametrize("clock_set,word", [(True, "true"), (False, "false")])
def test_the_device_clock_is_kept_as_context_and_arrival_stays_observed_at(
    clock_set: bool, word: str
) -> None:
    result = _attempt((_frame("97", at_s=3, stamp=7, clock_set=clock_set),), "window_complete")
    observation = result.observations[0]
    assert observation.observed_at == START + timedelta(seconds=3)
    assert ("device_time", "2026-11-14T10:00:07") in observation.context
    assert ("device_clock_set", word) in observation.context


@pytest.mark.trace("T-E07-OX-01", "V11")
def test_a_device_time_without_a_clock_flag_keeps_only_the_time() -> None:
    observation = _attempt((_frame("97", at_s=0, stamp=7),), "window_complete").observations[0]
    assert [key for key, _ in observation.context if key.startswith("device_")] == ["device_time"]


@pytest.mark.trace("T-E07-OX-01", "V11")
@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("completion", "spot", "invalid_completion"),
        ("steady_reading", "yes", "expected_boolean"),
    ],
)
def test_completion_and_steady_rules_are_validated(key: str, value: object, reason: str) -> None:
    document = profile_document()
    document[key] = value
    with pytest.raises(ProfileError, match=reason):
        parse_profile(document)


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize(
    "change",
    [
        {"request_id": "req-2"},
        {"patient_id": "crew-2"},
        {"capability_code": "pulse_rate"},
        {"device_id": "ox-2"},
    ],
)
def test_a_second_start_under_the_same_acquisition_for_another_binding_is_a_conflict(
    tmp_path: Path, change: dict[str, str]
) -> None:
    worker = AcquisitionService(
        PLXDecoder(),
        ProfileStore(profile),
        lambda: pytest.fail("no capture in this test"),
        FileJournal(tmp_path, max_entries=20, max_bytes=100000),
        Clock(),
        settings(),
        "ox-1",
    )
    assert worker.start(binding()) == "started"
    assert worker.start(binding()) == "duplicate"
    assert worker.start(replace(binding(), **change)) == "conflict"  # type: ignore[arg-type]


@pytest.mark.trace("T-E07-OX-06", "R26", "AGENTS 4")
@pytest.mark.parametrize("state,status", [("connected", "available"), ("quiet", "unavailable")])
def test_a_twin_is_reported_as_a_connected_sim_device_not_a_file_replay(state: str, status: str) -> None:
    operations = manifest(
        profile(),
        family="oximeter",
        service="0x1822",
        characteristics=["0x2A5F"],
        source_origin="SIM",
        connected_state=state,  # type: ignore[arg-type]
        simulated_device=True,
    )["operations"]
    assert operations[0:2] == [  # type: ignore[index]
        {
            "mode": "connected",
            "origin": "SIM",
            "status": status,
            "reason": "connected" if status == "available" else "source_not_connected",
        },
        {"mode": "recorded", "origin": "REPLAY", "status": "unavailable", "reason": "recording_not_selected"},
    ]
