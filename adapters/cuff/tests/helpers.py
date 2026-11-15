"""Schema-validated scratch documents and a deterministic clock, never clinical defaults."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.profile_file import validate_document
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.application.ports import Accepted, HubReply
from crew_health_device.application.settings import AdapterSettings, descriptors, load_settings
from crew_health_device.domain.acquisition import Binding, parse_binding
from crew_health_device.domain.profile import DeviceProfile, parse_profile


def profile_document() -> dict[str, object]:
    return {
        "profile_id": "bp-test",
        "version": "1",
        "family": "cuff",
        "device_label": "Scratch cuff",
        "model": None,
        "calibration": "unknown",
        "hardware_evidence": "unmeasured",
        "evidence_ref": None,
        "provenance": [
            {
                "path": "E07-SPEC section 3 Blood pressure",
                "note": "Nonclinical test limits only; no hardware evidence",
            }
        ],
        "components": [
            {
                "field": field,
                "indicator_code": indicator,
                "units": {"millimetre_of_mercury": "mm[Hg]"},
                "range_min": "0",
                "range_max": "300",
                "saturation_low": None,
                "saturation_high": None,
                "max_rate_per_second": None,
            }
            for field, indicator in (("systolic", "systolic_bp"), ("diastolic", "diastolic_bp"))
        ],
        "min_samples": 1,
        "rejecting_status": [],
        "noted_status": ["reserved_bits"],
    }


def profile(document: Mapping[str, object] | None = None) -> DeviceProfile:
    document = profile_document() if document is None else document
    validate_document(document, "device-profile.schema.json")
    return parse_profile(document)


def component_document(document: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], document["components"])[0]


def binding() -> Binding:
    return parse_binding(
        {
            "request_id": "req-bp-1",
            "acquisition_id": "acq-bp-1",
            "generation": 1,
            "patient_id": "crew-1",
            "episode_id": None,
            "capability_code": "systolic_bp",
            "method": "device",
            "device_id": "cuff-1",
            "origin_mode": "SIM",
            "reserved_sequence": 1,
            "expected_user_index": 2,
        }
    )


def settings() -> AdapterSettings:
    values = {
        "max_frames": 1,
        "max_frame_bytes": 512,
        "journal_max_entries": 100,
        "journal_max_bytes": 1048576,
        "profile_max_bytes": 1048576,
    }
    return load_settings(
        {
            descriptor.key: {
                "value": values.get(descriptor.key.removeprefix("adapter."), 1000),
                "unit": descriptor.unit,
                "basis": "E07 bounded test capture",
                "to_be_set_by": "test operator",
            }
            for descriptor in descriptors()
        }
    )


class Clock:
    def __init__(self) -> None:
        self.ticks = 0

    def now(self) -> datetime:
        return datetime(2026, 11, 14, tzinfo=UTC) + timedelta(microseconds=self.ticks // 1000)

    def monotonic_ns(self) -> int:
        self.ticks += 1000000
        return self.ticks


def journal(path: Path) -> FileJournal:
    options = settings()
    return FileJournal(path, max_entries=options.journal_max_entries, max_bytes=options.journal_max_bytes)


def recording(frames: list[dict[str, str]]) -> RecordedFrameSource:
    document: dict[str, object] = {
        "origin": "SIM",
        "provenance": "E07 hand-checked specification vectors, generated SIM",
        "frames": frames,
    }
    validate_document(document, "frame-recording.schema.json")
    return RecordedFrameSource(document, max_frames=8)


class Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls.append((request_id, idempotency_key, body))
        return Accepted(200)
