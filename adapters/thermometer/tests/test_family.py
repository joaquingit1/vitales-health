"""A fresh HTTP consumer exercises family composition with recorded input."""

import asyncio
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from crew_health.contracts_gen.schemas.health_schema import Adapter
from crew_health_adapter_thermometer.family import (
    SERVICE,
    TEMPERATURE_MEASUREMENT,
    TEMPERATURE_MEASUREMENT_UUID,
    build,
)
from crew_health_device.application.profiles import ProfileStore

from .helpers import Clock, Hub, journal, profile, profile_document, raw, recording, settings
from .helpers import binding as binding


def request(
    app: FastAPI, method: str, path: str, *, body: object = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(perform())


def app_for(path: Path, store: ProfileStore, hub: Hub) -> FastAPI:
    return build(
        profile_store=store,
        source_factory=lambda: recording([raw()]),
        journal=journal(path),
        clock=Clock(),
        settings=settings(),
        adapter_id="temp-1",
        token="token",
        hub=hub,
        source_origin="SIM",
        wait=lambda seconds: False,
    )


@pytest.mark.trace("T-E07-TEMP-05", "R26")
def test_health_manifest_hardware_remains_unmeasured(tmp_path: Path) -> None:
    app = app_for(tmp_path, ProfileStore(profile), Hub())
    response = request(app, "GET", "/health")
    assert response.status_code == 200
    info = response.json()
    Adapter.model_validate({key: info[key] for key in ("adapter_id", "state")})
    assert info["state"] == "quiet"  # Connect-per-acquisition runtime has no data before its first open.
    manifest = info["manifest"]
    assert manifest["family"] == "thermometer"
    assert manifest["hardware_compatibility"] == "unmeasured" and manifest["evidence_ref"] is None
    assert manifest["profile"]["calibration"] == "unknown" and manifest["profile"]["model"] is None
    assert manifest["decoder_evidence"] == "specification_golden_cases"
    assert manifest["protocol"] == {"service": "0x1809", "characteristics": ["0x2A1C"]}
    assert SERVICE == "0x1809" and TEMPERATURE_MEASUREMENT == "temperature_measurement"
    assert TEMPERATURE_MEASUREMENT_UUID == "00002a1c-0000-1000-8000-00805f9b34fb"
    assert manifest["operations"] == [
        {"mode": "connected", "origin": "LIVE", "status": "unavailable", "reason": "source_not_connected"},
        {"mode": "recorded", "origin": "SIM", "status": "available", "reason": "recording_selected"},
        {"mode": "manual", "origin": "LIVE", "status": "hub_owned"},
    ]


@pytest.mark.trace("T-E07-TEMP-04", "V75")
def test_profile_label_reload_changes_health_manifest(tmp_path: Path) -> None:
    document = profile_document()
    store = ProfileStore(lambda: profile(document))
    app = app_for(tmp_path, store, Hub())
    assert (
        request(app, "GET", "/health").json()["manifest"]["profile"]["device_label"] == "Scratch thermometer"
    )
    document["device_label"] = "Renamed scratch thermometer"
    assert store.reload()
    assert (
        request(app, "GET", "/health").json()["manifest"]["profile"]["device_label"]
        == "Renamed scratch thermometer"
    )


@pytest.mark.trace("T-E07-TEMP-01", "T-E07-TEMP-03", "R08", "E07-01", "V11", "V16")
def test_authenticated_api_capture_delivers_journaled_attempt(tmp_path: Path) -> None:
    hub = Hub()
    app = app_for(tmp_path, ProfileStore(profile), hub)
    document = asdict(binding())
    assert request(app, "POST", "/acquisitions", body=document).status_code == 401
    assert not journal(tmp_path).records()
    response = request(app, "POST", "/acquisitions", body=document, headers={"Authorization": "Bearer token"})
    assert response.status_code == 200 and response.json()["reply"] == "started"
    assert len(hub.calls) == 1
    assert hub.calls[0][:2] == ("req-1", "e07:acq-1:1")
    assert b'"value":36.6' in hub.calls[0][2] and b'"device_id":"temp-1"' in hub.calls[0][2]
    assert journal(tmp_path).records() == ()
    assert request(app, "GET", "/health").json()["state"] == "connected"
