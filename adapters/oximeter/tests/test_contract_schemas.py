"""The adapter's published documents match the schemas it ships (P-E07-1 manifest, P-E07-3 settings)."""

from pathlib import Path

import pytest
from jsonschema import ValidationError

from crew_health_adapter_oximeter.family import build
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.application.ports import Accepted, HubReply
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.settings import descriptors

from .helpers import ASGIClient, Clock, profile, settings, settings_document, validate


class Hub:
    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        return Accepted(200)


@pytest.mark.trace("T-E07-OX-06", "R26")
def test_health_response_validates_against_the_published_health_schema(tmp_path: Path) -> None:
    app = build(
        profile_store=ProfileStore(profile),
        source_factory=lambda: RecordedFrameSource(
            {"origin": "SIM", "provenance": "schema check", "frames": []}, max_frames=1
        ),
        journal=FileJournal(tmp_path, max_entries=10, max_bytes=100000),
        clock=Clock(),
        settings=settings(),
        adapter_id="adapter-1",
        token="t",
        hub=Hub(),
        source_origin="SIM",
        wait=lambda seconds: False,
    )
    with ASGIClient(app) as client:
        body = client.get("/health").json()
    validate(body, "adapter-health")
    with pytest.raises(ValidationError):
        validate({**body, "manifest": {**body["manifest"], "decoder_evidence": "hardware"}}, "adapter-health")


@pytest.mark.trace("T-E07-OX-06", "V35")
def test_settings_schema_and_descriptors_agree() -> None:
    document = settings_document()
    validate(document, "adapter-settings")
    for descriptor in descriptors():
        too_big = {**document, descriptor.key: {**document[descriptor.key], "value": descriptor.maximum + 1}}  # type: ignore[dict-item]
        with pytest.raises(ValidationError):
            validate(too_big, "adapter-settings")
    with pytest.raises(ValidationError):
        validate({k: v for k, v in document.items() if k != "adapter.retry_max"}, "adapter-settings")
