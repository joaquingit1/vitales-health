import errno
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.health import HealthTracker
from crew_health_device.application.ports import (
    FrameSource,
    JournalCorrupt,
    JournalUnavailable,
    RawFrame,
    SourceDisconnected,
    SourceUnavailable,
)
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.serialization import attempt_body
from crew_health_device.domain.errors import BindingError

from .helpers import Clock, binding, profile, settings, validate


def recording(frames: list[dict[str, object]]) -> RecordedFrameSource:
    document: dict[str, object] = {
        "origin": "SIM",
        "provenance": "Specification golden frames",
        "frames": frames,
    }
    validate(document, "frame-recording")
    return RecordedFrameSource(document, max_frames=8)


def store(path: Path) -> FileJournal:
    return FileJournal(path, max_entries=100, max_bytes=100000)


def service(
    path: Path,
    source: Callable[[], FrameSource],
    *,
    clock: Clock | None = None,
    health: HealthTracker | None = None,
    max_frames: int = 8,
) -> AcquisitionService:
    return AcquisitionService(
        PLXDecoder(),
        ProfileStore(profile),
        source,
        store(path),
        clock or Clock(),
        replace(settings(), max_frames=max_frames),
        "ox-1",
        health=health,
    )


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_start_then_capture_journals_one_attempt_and_never_recaptures_after_restart(tmp_path: Path) -> None:
    opened: list[bool] = []

    def source() -> FrameSource:
        opened.append(True)
        return recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}])

    worker = service(tmp_path, source, max_frames=1)
    request = replace(binding(), origin_mode="SIM")
    assert worker.start(request) == "started"
    assert worker.status(request.acquisition_id, 1) == "started"
    assert worker.start(request) == "duplicate"
    assert worker.start(replace(request, acquisition_id="acq-2")) == "busy"
    draft = worker.capture(request.acquisition_id)
    assert draft.outcome == "success"
    assert worker.status(request.acquisition_id, 1) == "completed"
    assert worker.cancel(request.acquisition_id, 1) == "cannot_stop"
    restarted = service(tmp_path, source, max_frames=1)
    assert restarted.start(request) == "duplicate"
    assert len(opened) == 1
    sent = store(tmp_path).get("e07:acq-1:1")
    assert sent is not None and sent.state == "pending"
    assert json.loads(sent.body)["observations"][0]["origin_mode"] == "SIM"
    with pytest.raises(BindingError):
        worker.capture(request.acquisition_id)


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_unknown_generation_and_unrecovered_physical_state_stay_unknown(tmp_path: Path) -> None:
    worker = service(tmp_path, lambda: recording([]))
    request = binding()
    assert worker.cancel("absent", 1) == "unknown"
    assert worker.status("absent", 1) == "unknown"
    assert worker.start(request) == "started"
    assert worker.cancel(request.acquisition_id, 2) == "unknown"
    assert worker.status(request.acquisition_id, 2) == "unknown"
    restarted = service(tmp_path, lambda: recording([]))
    assert restarted.start(request) == "duplicate"
    assert restarted.status(request.acquisition_id, 1) == "unknown"
    assert restarted.cancel(request.acquisition_id, 1) == "unknown"
    assert restarted.start(replace(request, acquisition_id="acq-2")) == "started"
    assert restarted.status(request.acquisition_id, 1) == "unknown"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_cancel_during_capture_stops_and_journals_failed_cancelled(tmp_path: Path) -> None:
    replies: list[str] = []

    class CancellingSource:
        origin: Literal["SIM"] = "SIM"

        def open(self, timeout_ms: int) -> None:
            pass

        def next_frame(self, timeout_ms: int) -> RawFrame:
            replies.append(worker.cancel("acq-1", 1))
            return RawFrame("plx_continuous", bytes.fromhex("0062004800"))

        def close(self) -> None:
            pass

    worker = service(tmp_path, lambda: CancellingSource())
    assert worker.start(replace(binding(), origin_mode="SIM")) == "started"
    draft = worker.capture("acq-1")
    assert replies == ["stopped"]
    assert draft.outcome == "failed" and draft.failure_reason == "cancelled" and not draft.observations
    assert worker.status("acq-1", 1) == "stopped"
    assert worker.cancel("acq-1", 1) == "cannot_stop"
    sent = store(tmp_path).get("e07:acq-1:1")
    assert sent is not None and json.loads(sent.body)["failure_reason"] == "cancelled"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_cancel_before_capture_does_not_open_source(tmp_path: Path) -> None:
    worker = service(tmp_path, lambda: recording([{"event": "device_absent"}]))
    assert worker.start(replace(binding(), origin_mode="SIM")) == "started"
    assert worker.cancel("acq-1", 1) == "stopped"
    assert worker.capture("acq-1").failure_reason == "cancelled"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize("event", ["disconnect", "timeout", "device_absent", "radio_absent"])
def test_source_failure_journals_failure_and_disconnects_health(tmp_path: Path, event: str) -> None:
    clock = Clock()
    health = HealthTracker(clock, 1000)
    worker = service(tmp_path, lambda: recording([{"event": event}]), clock=clock, health=health)
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    assert draft.outcome == "failed"
    assert draft.failure_reason == ("disconnected" if event == "disconnect" else event)
    assert worker.status("acq-1", 1) == "failed"
    assert health.state() == "disconnected"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_journal_disk_full_propagates_and_nothing_is_claimed_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = service(
        tmp_path, lambda: recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}])
    )
    worker.start(replace(binding(), origin_mode="SIM"))

    def disk_full(source: object, target: object) -> None:
        raise OSError(errno.ENOSPC, "test full disk")

    monkeypatch.setattr("os.replace", disk_full)
    with pytest.raises(JournalUnavailable) as caught:
        worker.capture("acq-1")
    assert caught.value.cause == "disk_full"
    assert store(tmp_path).get("e07:acq-1:1") is None
    assert worker.status("acq-1", 1) == "started"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_nondevice_method_is_rejected_before_durable_reservation(tmp_path: Path) -> None:
    worker = service(tmp_path, lambda: recording([]))
    assert worker.start(replace(binding(), method="manual")) == "rejected"
    assert not store(tmp_path).records()


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_connect_timeout_becomes_failed_timeout(tmp_path: Path) -> None:
    class UnavailableSource:
        origin: Literal["LIVE"] = "LIVE"

        def open(self, timeout_ms: int) -> None:
            raise SourceUnavailable("connect_timeout")

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            raise RuntimeError("not reached")

        def close(self) -> None:
            pass

    worker = service(tmp_path, lambda: UnavailableSource())
    worker.start(binding())
    assert worker.capture("acq-1").failure_reason == "timeout"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_cancel_after_frames_keeps_the_real_evidence_as_late_and_labeled(tmp_path: Path) -> None:
    """A cancel never erases frames already read: the value goes to the hub (which quarantines a late result)
    labeled as cancelled, and the acquisition is recorded as stopped."""
    delegate = recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}])

    class ClosingSource:
        origin: Literal["SIM"] = "SIM"

        def open(self, timeout_ms: int) -> None:
            delegate.open(timeout_ms)

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            return delegate.next_frame(timeout_ms)

        def close(self) -> None:
            assert worker.cancel("acq-1", 1) == "stopped"
            delegate.close()

    worker = service(tmp_path, lambda: ClosingSource())
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    assert draft.outcome == "success" and draft.failure_reason is None
    assert [o.value for o in draft.observations] == [Decimal(98)]
    assert "acquisition_cancelled" in draft.quality_notes
    assert worker.status("acq-1", 1) == "stopped"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize(
    "payload,max_bytes,note", [("0062004800", 4, "frame:frame_too_large"), ("0062", 512, "frame:truncated")]
)
def test_malformed_or_oversized_frames_are_not_observations(
    tmp_path: Path, payload: str, max_bytes: int, note: str
) -> None:
    options = replace(settings(), max_frames=1, max_frame_bytes=max_bytes)
    worker = AcquisitionService(
        PLXDecoder(),
        ProfileStore(profile),
        lambda: recording([{"characteristic": "plx_continuous", "payload_hex": payload}]),
        store(tmp_path),
        Clock(),
        options,
        "ox-1",
    )
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    assert draft.outcome == "rejected" and not draft.observations
    assert note in draft.quality_notes
    assert worker.status("acq-1", 1) == "rejected"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_elapsed_window_prevents_another_frame_read(tmp_path: Path) -> None:
    clock = Clock((0, 1000000000))
    source = recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}])
    worker = AcquisitionService(
        PLXDecoder(), ProfileStore(profile), lambda: source, store(tmp_path), clock, settings(), "ox-1"
    )
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    assert draft.outcome == "rejected" and draft.failure_reason == "insufficient_samples"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_frame_bound_terminates_capture_and_keeps_last_value(tmp_path: Path) -> None:
    clock = Clock()
    health = HealthTracker(clock, 1000)
    options = replace(settings(), max_frames=1)
    worker = AcquisitionService(
        PLXDecoder(),
        ProfileStore(profile),
        lambda: recording(
            [
                {"characteristic": "plx_continuous", "payload_hex": "0062004800"},
                {"characteristic": "plx_continuous", "payload_hex": "0061004800"},
            ]
        ),
        store(tmp_path),
        clock,
        options,
        "ox-1",
        health=health,
    )
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    assert draft.observations[0].value == 98
    # A normal end leaves a connect-per-acquisition device reachable: connected, then quiet when idle.
    assert health.state() == "connected"


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
def test_canonical_body_omits_absent_fields_and_rejects_nonfinite_or_naive_values(tmp_path: Path) -> None:
    worker = service(
        tmp_path,
        lambda: recording([{"characteristic": "plx_continuous", "payload_hex": "0062004800"}]),
        max_frames=1,
    )
    worker.start(replace(binding(), origin_mode="SIM"))
    draft = worker.capture("acq-1")
    for value, expected in [(Decimal("97.500"), b'"value":97.5'), (Decimal("-0.000"), b'"value":0')]:
        observation = replace(draft.observations[0], value=value, episode_id="episode-1")
        body = attempt_body(replace(draft, observations=(observation,)))
        assert expected in body and b'"episode_id":"episode-1"' in body
        assert b'"failure_reason"' not in body
    with pytest.raises(ValueError, match="finite_decimal_required"):
        attempt_body(
            replace(draft, observations=(replace(draft.observations[0], value=Decimal("Infinity")),))
        )
    with pytest.raises(ValueError, match="utc_aware_instant_required"):
        attempt_body(replace(draft, started_at=datetime(2026, 11, 14)))


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_health_becomes_quiet_without_frames_and_recovers_only_after_a_frame() -> None:
    clock = Clock((0, 999000000, 1000000000, 1001000000, 1002000000))
    health = HealthTracker(clock, 1000)
    assert health.state() == "quiet"
    health.connected()
    assert health.state() == "connected"
    assert health.state() == "quiet"
    health.frame_received()
    assert health.state() == "connected"
    health.disconnected()
    assert health.state() == "disconnected"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize("body", [b"{invalid", b"[" * 20000 + b"]" * 20000])
def test_corrupt_persisted_binding_fails_loudly_without_recapturing(tmp_path: Path, body: bytes) -> None:
    worker = service(tmp_path, lambda: recording([]))
    worker.start(binding())
    journal = store(tmp_path)
    record = journal.get("acquisition:acq-1")
    assert record is not None
    journal.put(replace(record, body=body))
    with pytest.raises(JournalCorrupt):
        worker.status("acq-1", 1)
    with pytest.raises(JournalCorrupt):
        worker.start(binding())


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_parser_recursion_failure_is_a_typed_corrupt_journal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = service(tmp_path, lambda: recording([]))
    worker.start(binding())

    def nested_parser(data: bytes) -> object:
        raise RecursionError("parser_depth_limit")

    monkeypatch.setattr("crew_health_device.application.capture.json", SimpleNamespace(loads=nested_parser))
    with pytest.raises(JournalCorrupt) as caught:
        worker.status("acq-1", 1)
    assert caught.value.key == "acquisition:acq-1"


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_queue_overflow_disconnect_is_journaled_with_explicit_quality_note(tmp_path: Path) -> None:
    closed: list[bool] = []

    class OverflowSource:
        origin: Literal["LIVE"] = "LIVE"

        def open(self, timeout_ms: int) -> None:
            pass

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            raise SourceDisconnected("queue_overflow")

        def close(self) -> None:
            closed.append(True)

    worker = service(tmp_path, lambda: OverflowSource())
    worker.start(binding())
    draft = worker.capture("acq-1")
    assert draft.outcome == "failed" and draft.failure_reason == "disconnected"
    assert draft.quality_notes == ("source:queue_overflow",)
    assert not draft.observations and closed == [True]
    record = store(tmp_path).get("e07:acq-1:1")
    assert record is not None and b'"source:queue_overflow"' in record.body


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
def test_disconnect_during_close_preserves_frame_timeout_reason(tmp_path: Path) -> None:
    class DisconnectingCloseSource:
        origin: Literal["LIVE"] = "LIVE"

        def open(self, timeout_ms: int) -> None:
            pass

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            return None

        def close(self) -> None:
            raise SourceDisconnected()

    worker = service(tmp_path, lambda: DisconnectingCloseSource())
    worker.start(binding())
    draft = worker.capture("acq-1")
    assert draft.outcome == "failed" and draft.failure_reason == "timeout"
    assert not draft.observations
    assert store(tmp_path).get("e07:acq-1:1") is not None


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize("reason", ["device_absent", "radio_absent", "connect_timeout"])
def test_disconnect_during_close_preserves_original_open_failure(
    tmp_path: Path, reason: Literal["device_absent", "radio_absent", "connect_timeout"]
) -> None:
    class FailedOpenSource:
        origin: Literal["LIVE"] = "LIVE"

        def open(self, timeout_ms: int) -> None:
            raise SourceUnavailable(reason)

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            raise RuntimeError("not_opened")

        def close(self) -> None:
            raise SourceDisconnected()

    worker = service(tmp_path, lambda: FailedOpenSource())
    worker.start(binding())
    draft = worker.capture("acq-1")
    assert draft.outcome == "failed"
    assert draft.failure_reason == ("timeout" if reason == "connect_timeout" else reason)
    assert not draft.observations
    assert store(tmp_path).get("e07:acq-1:1") is not None


@pytest.mark.trace("T-E07-OX-03", "V13", "V16", "V35")
@pytest.mark.parametrize("payload,expected", [("0062004800", "success"), ("0065004800", "rejected")])
def test_disconnect_during_close_after_complete_window_still_journals_attempt(
    tmp_path: Path, payload: str, expected: str
) -> None:
    class DisconnectingCloseSource:
        origin: Literal["LIVE"] = "LIVE"

        def open(self, timeout_ms: int) -> None:
            pass

        def next_frame(self, timeout_ms: int) -> RawFrame | None:
            return RawFrame("plx_continuous", bytes.fromhex(payload))

        def close(self) -> None:
            raise SourceDisconnected()

    worker = AcquisitionService(
        PLXDecoder(),
        ProfileStore(profile),
        lambda: DisconnectingCloseSource(),
        store(tmp_path),
        Clock(),
        replace(settings(), max_frames=1),
        "ox-1",
    )
    worker.start(binding())
    draft = worker.capture("acq-1")
    # The frame budget was used before the link dropped on close: the reading is judged on what arrived.
    assert draft.outcome == expected
    if expected == "rejected":
        assert draft.failure_reason == "out_of_device_range" and not draft.observations
    else:
        assert draft.observations[0].value == Decimal(98)
    assert store(tmp_path).get("e07:acq-1:1") is not None
