"""Authenticated worker API using recorded source and the real journal."""

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

import pytest

from crew_health.contracts_gen.schemas.health_schema import Adapter
from crew_health_adapter_oximeter.family import build
from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.api.app import build_app
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.health import HealthTracker, manifest
from crew_health_device.application.ports import Accepted, HubReply
from crew_health_device.application.profiles import ProfileStore

from .helpers import ASGIClient, Clock, binding, profile, settings


class Hub:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.bodies.append(body)
        return Accepted(200)


def recording() -> Mapping[str, object]:
    return {
        "origin": "SIM",
        "provenance": "hand checked SIG golden vector",
        "frames": [
            {"characteristic": "plx_continuous", "payload_hex": "0062004800"},
            {"event": "disconnect"},
        ],
    }


@pytest.mark.trace("T-E07-OX-06", "R26", "E07-01")
def test_health_manifest_auth_capture_and_control_endpoints(tmp_path: Path) -> None:
    clock = Clock(tuple(range(0, 100000000, 1000000)))
    health = HealthTracker(clock, 1000)
    journal = FileJournal(tmp_path, max_entries=20, max_bytes=100000)
    store = ProfileStore(profile)
    service = AcquisitionService(
        PLXDecoder(),
        store,
        lambda: RecordedFrameSource(recording(), max_frames=8),
        journal,
        clock,
        settings(),
        "adapter-1",
        health=health,
    )
    app = build_app(
        service=service,
        health=health,
        token="token",
        adapter_id="adapter-1",
        family="oximeter",
        manifest=lambda: manifest(
            store.current(),
            family="oximeter",
            service="0x1822",
            characteristics=["0x2A5E", "0x2A5F"],
            source_origin="SIM",
            connected_state=health.state(),
        ),
    )
    document = asdict(binding())
    document["origin_mode"] = "SIM"
    with ASGIClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        info = response.json()
        Adapter.model_validate({key: info[key] for key in ("adapter_id", "state")})
        assert info["manifest"]["hardware_compatibility"] == "unmeasured"
        assert info["manifest"]["profile"]["device_label"] == profile().device_label
        assert client.post("/acquisitions", json=document).status_code == 401
        assert client.post("/acquisitions", json=document).json() == {"code": "UNAUTHENTICATED"}
        auth = {"Authorization": "Bearer token"}
        assert client.post("/acquisitions", json={}, headers=auth).status_code == 422
        assert client.post("/acquisitions", json=document, headers=auth).json()["reply"] == "started"
        assert any(record.kind == "send" for record in journal.records())
        assert client.get("/health").json()["state"] == "disconnected"
        # One continuous frame, then the link drops before the window ends: unavailable, not a value (B1).
        assert client.get("/acquisitions/acq-1?generation=1", headers=auth).json() == {"state": "failed"}
        assert client.post("/acquisitions/acq-1/cancel", json={"generation": 1}, headers=auth).json() == {
            "reply": "cannot_stop"
        }
        assert client.post("/acquisitions/unknown/cancel", json={"generation": 1}, headers=auth).json() == {
            "reply": "unknown"
        }
        assert client.get("/acquisitions/acq-1?generation=2", headers=auth).json() == {"state": "unknown"}
        assert client.post("/acquisitions", json=document, headers=auth).json()["reply"] == "duplicate"
    with pytest.raises(ValueError, match="empty_token"):
        build_app(
            service=service,
            health=health,
            token="",
            adapter_id="adapter-1",
            family="oximeter",
            manifest=lambda: {},
        )


@pytest.mark.trace("T-E07-OX-06", "R26", "E07-01")
def test_family_build_wires_background_delivery_with_real_runtime(tmp_path: Path) -> None:
    hub = Hub()
    journal = FileJournal(tmp_path, max_entries=20, max_bytes=100000)
    app = build(
        profile_store=ProfileStore(profile),
        source_factory=lambda: RecordedFrameSource(recording(), max_frames=8),
        journal=journal,
        clock=Clock(tuple(range(0, 100000000, 1000000))),
        settings=settings(),
        adapter_id="adapter-1",
        token="token",
        hub=hub,
        source_origin="SIM",
        wait=lambda seconds: True,
    )
    document = asdict(binding())
    document["origin_mode"] = "SIM"
    with ASGIClient(app) as client:
        assert (
            client.post("/acquisitions", json=document, headers={"Authorization": "Bearer token"}).json()[
                "reply"
            ]
            == "started"
        )
    assert len(hub.bodies) == 1
    assert all(record.kind != "send" for record in journal.records())


@pytest.mark.trace("T-E07-OX-06", "V16", "V35")
def test_held_sends_are_listed_and_released_through_the_authenticated_api(tmp_path: Path) -> None:
    from crew_health_device.application.ports import JournalRecord

    journal = FileJournal(tmp_path, max_entries=20, max_bytes=100000)
    held = JournalRecord("e07:acq-9:1", "send", "held", "req-9", b"{}", "IDENTITY_MISMATCH")
    journal.put(held)
    app = build(
        profile_store=ProfileStore(profile),
        source_factory=lambda: RecordedFrameSource(recording(), max_frames=8),
        journal=journal,
        clock=Clock(),
        settings=settings(),
        adapter_id="adapter-1",
        token="token",
        hub=Hub(),
        source_origin="SIM",
        wait=lambda _seconds: False,
    )
    auth = {"Authorization": "Bearer token"}
    with ASGIClient(app) as client:
        assert client.get("/deliveries").status_code == 401
        assert client.get("/deliveries", headers=auth).json() == {
            "items": [
                {"key": held.key, "request_id": "req-9", "state": "held", "detail": "IDENTITY_MISMATCH"}
            ]
        }
        assert client.post("/deliveries/release", json={"key": 1}, headers=auth).status_code == 422
        assert client.post("/deliveries/release", json={"key": held.key}, headers=auth).json() == {
            "reply": "released"
        }
        assert client.post("/deliveries/release", json={"key": held.key}, headers=auth).json() == {
            "reply": "unknown"
        }
    pending = journal.get(held.key)
    assert pending is not None and (pending.state, pending.detail, pending.body) == ("pending", None, b"{}")
