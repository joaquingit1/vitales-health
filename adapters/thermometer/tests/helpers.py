"""Schema-validated scratch inputs and injected time, never clinical configuration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from crew_health_adapter_thermometer.htm import HTMDecoder
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.profile_file import validate_document
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.ports import Accepted, HubReply
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.settings import AdapterSettings, descriptors, load_settings
from crew_health_device.domain.acquisition import Binding
from crew_health_device.domain.profile import DeviceProfile, parse_profile


def profile_document() -> dict[str, object]:
    return {
        "profile_id": "htm-test",
        "version": "1",
        "family": "thermometer",
        "device_label": "Scratch thermometer",
        "model": None,
        "calibration": "unknown",
        "hardware_evidence": "unmeasured",
        "evidence_ref": None,
        "provenance": [
            {"path": "E07-SPEC section 3", "note": "Nonclinical test bounds; no hardware evidence"}
        ],
        "components": [
            {
                "field": "temperature",
                "indicator_code": "temperature",
                "units": {"degree_celsius": "Cel"},
                "range_min": "0",
                "range_max": "100",
                "saturation_low": None,
                "saturation_high": None,
                "max_rate_per_second": None,
            }
        ],
        "min_samples": 1,
        "rejecting_status": [],
        "noted_status": ["reserved_bits"],
    }


def component(document: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], document["components"])[0]


def profile(document: dict[str, object] | None = None) -> DeviceProfile:
    candidate = profile_document() if document is None else document
    validate_document(candidate, "device-profile.schema.json")
    return parse_profile(candidate)


def settings(*, max_frames: int = 1) -> AdapterSettings:
    values = {
        "max_frames": max_frames,
        "max_frame_bytes": 512,
        "journal_max_entries": 100,
        "journal_max_bytes": 1048576,
        "profile_max_bytes": 1048576,
    }
    return load_settings(
        {
            d.key: {
                "value": values.get(d.key.removeprefix("adapter."), min(1000, d.maximum)),
                "unit": d.unit,
                "basis": "E07 deterministic test bounds",
                "to_be_set_by": "test operator",
            }
            for d in descriptors()
        }
    )


def binding() -> Binding:
    return Binding("req-1", "acq-1", 1, "crew-1", None, "temperature", "device", "temp-1", "SIM", 1, None)


class Clock:
    def __init__(self) -> None:
        self.ticks = 0
        self.receptions: list[datetime] = []

    def now(self) -> datetime:
        instant = datetime(2026, 11, 14, tzinfo=UTC) + timedelta(microseconds=self.ticks // 1000)
        self.receptions.append(instant)
        return instant

    def monotonic_ns(self) -> int:
        self.ticks += 1000000
        return self.ticks


class Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls.append((request_id, idempotency_key, body))
        return Accepted(200)


def recording(frames: list[dict[str, str]]) -> RecordedFrameSource:
    document: dict[str, object] = {
        "origin": "SIM",
        "provenance": "Specification golden vectors, not measured hardware",
        "frames": frames,
    }
    validate_document(document, "frame-recording.schema.json")
    return RecordedFrameSource(document, max_frames=8)


def journal(path: Path) -> FileJournal:
    return FileJournal(path, max_entries=100, max_bytes=1048576)


def service(
    path: Path,
    frames: list[dict[str, str]],
    *,
    document: dict[str, object] | None = None,
    clock: Clock | None = None,
    max_frames: int = 1,
) -> AcquisitionService:
    return AcquisitionService(
        HTMDecoder(),
        ProfileStore(lambda: profile(document)),
        lambda: recording(frames),
        journal(path),
        Clock() if clock is None else clock,
        settings(max_frames=max_frames),
        "temp-1",
    )


def raw(payload: str = "006e0100ff") -> dict[str, str]:
    return {"characteristic": "temperature_measurement", "payload_hex": payload}
