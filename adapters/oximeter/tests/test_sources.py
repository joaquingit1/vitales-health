"""Source and clock boundaries remain labeled and bounded."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health_device.adapters.recorded_source import RecordedFrameSource, load_recording_file
from crew_health_device.adapters.system_time import SystemTimeSource
from crew_health_device.application.ports import SourceDisconnected, SourceUnavailable
from crew_health_device.domain.errors import ProfileError

from .helpers import validate


def recording(frames: list[dict[str, str]], origin: str = "REPLAY") -> Mapping[str, object]:
    return {"origin": origin, "provenance": "SIG specification golden case", "frames": frames}


@pytest.mark.trace("R08", "R26", "V15")
def test_recorded_frames_keep_origin_and_timeout_without_sample_fallback(tmp_path: Path) -> None:
    doc = recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}, {"event": "timeout"}])
    validate(doc, "frame-recording")
    path = tmp_path / "recording.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    source = RecordedFrameSource(load_recording_file(path, 4096), max_frames=2)
    assert source.origin == "REPLAY"
    with pytest.raises(SourceDisconnected):
        source.next_frame(1)
    source.open(1)
    frame = source.next_frame(1)
    assert frame is not None and frame.payload == bytes.fromhex("0062004800")
    assert source.next_frame(1) is None
    assert source.next_frame(1) is None
    source.close()
    with pytest.raises(SourceDisconnected):
        source.next_frame(1)
    with pytest.raises(ProfileError):
        RecordedFrameSource(doc, max_frames=1)
    with pytest.raises(ProfileError):
        RecordedFrameSource(doc, max_frames=0)


@pytest.mark.trace("E07-01", "R08")
@pytest.mark.parametrize("event", ["radio_absent", "device_absent"])
def test_recorded_absent_source_reports_specific_open_failure(event: str) -> None:
    source = RecordedFrameSource(recording([{"event": event}]), max_frames=1)
    with pytest.raises(SourceUnavailable) as failure:
        source.open(1)
    assert failure.value.reason == event


@pytest.mark.trace("R26", "V15")
@pytest.mark.parametrize(
    "doc",
    [
        recording([], "LIVE"),
        recording([{"characteristic": "x", "payload_hex": "f"}]),
        recording([{"event": "made_up"}]),
    ],
)
def test_recording_cannot_claim_live_or_contain_invalid_frames(doc: Mapping[str, object]) -> None:
    with pytest.raises(ProfileError):
        RecordedFrameSource(doc, max_frames=1)


@pytest.mark.trace("AGENTS 3.9")
def test_system_clock_delegates_only_to_injected_boundary_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    from crew_health_device.adapters import system_time

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> FrozenDateTime:
            assert tz == UTC
            return cls(2026, 11, 14, tzinfo=UTC)

    monkeypatch.setattr(system_time, "datetime", FrozenDateTime)
    monkeypatch.setattr("time.monotonic_ns", lambda: 123456)
    clock = SystemTimeSource()
    assert clock.now() == datetime(2026, 11, 14, tzinfo=UTC)
    assert clock.monotonic_ns() == 123456
