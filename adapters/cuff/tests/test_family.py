"""Exercise the composed worker API with real shared adapters and fake boundaries."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from crew_health_adapter_cuff.family import build
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.domain.acquisition import OriginMode

from .helpers import Clock, Hub, binding, journal, profile, profile_document, recording, settings


def request(
    app: FastAPI, method: str, path: str, *, body: object = None, token: str | None = None
) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            return await client.request(
                method, path, json=body, headers={} if token is None else {"Authorization": "Bearer " + token}
            )

    return asyncio.run(perform())


def application(path: Path, store: ProfileStore, hub: Hub, origin: OriginMode = "SIM") -> FastAPI:
    return build(
        profile_store=store,
        source_factory=lambda: recording(
            [{"characteristic": "blood_pressure_measurement", "payload_hex": "00780050005d00"}]
        ),
        journal=journal(path),
        clock=Clock(),
        settings=settings(),
        adapter_id="cuff-1",
        token="local-test-token",
        hub=hub,
        source_origin=origin,
        wait=lambda _: False,
    )


@pytest.mark.trace("T-E07-BP-04", "V75")
def test_profile_label_edit_changes_health_manifest_without_code_change(tmp_path: Path) -> None:
    document = profile_document()
    store = ProfileStore(lambda: profile(document))
    app = application(tmp_path, store, Hub())
    before = request(app, "GET", "/health")
    assert before.status_code == 200
    assert before.json()["manifest"]["profile"]["device_label"] == "Scratch cuff"
    document["device_label"] = "Renamed scratch cuff"
    assert store.reload()
    after = request(app, "GET", "/health")
    assert after.status_code == 200
    assert after.json()["manifest"]["profile"]["device_label"] == "Renamed scratch cuff"


@pytest.mark.trace("T-E07-BP-05", "R26")
@pytest.mark.parametrize("origin", ["SIM", "REPLAY", "LIVE"])
def test_health_manifest_reports_protocol_origin_and_unmeasured_hardware(
    tmp_path: Path, origin: OriginMode
) -> None:
    response = request(application(tmp_path, ProfileStore(profile), Hub(), origin), "GET", "/health")
    assert response.status_code == 200
    body = response.json()
    assert body["adapter_id"] == "cuff-1" and body["state"] == "quiet"
    manifest = body["manifest"]
    assert manifest["family"] == "cuff"
    assert manifest["protocol"] == {"service": "0x1810", "characteristics": ["0x2A35"]}
    assert manifest["hardware_compatibility"] == "unmeasured" and manifest["evidence_ref"] is None
    assert manifest["decoder_evidence"] == "specification_golden_cases"
    assert manifest["profile"]["calibration"] == "unknown"
    assert manifest["operations"][0]["status"] == "unavailable"
    assert manifest["operations"][1]["status"] == ("unavailable" if origin == "LIVE" else "available")
    assert manifest["operations"][1]["origin"] == ("REPLAY" if origin == "LIVE" else origin)
    assert manifest["operations"][2] == {"mode": "manual", "origin": "LIVE", "status": "hub_owned"}


@pytest.mark.trace("T-E07-BP-01", "T-E07-BP-03", "R08", "E07-01", "V11", "V16")
def test_api_capture_delivers_the_journaled_attempt_to_hub(tmp_path: Path) -> None:
    hub = Hub()
    app = application(tmp_path, ProfileStore(profile), hub)
    unauthorized = request(app, "POST", "/acquisitions", body=asdict(binding()))
    assert unauthorized.status_code == 401 and not journal(tmp_path).records()
    accepted = request(app, "POST", "/acquisitions", body=asdict(binding()), token="local-test-token")
    assert accepted.status_code == 200 and accepted.json()["reply"] == "started"
    assert len(hub.calls) == 1
    target, key, data = hub.calls[0]
    assert target == "req-bp-1" and key == "e07:acq-bp-1:1"
    observation = json.loads(data)["observations"][0]
    assert observation["value"] == 120 and observation["unit"] == "mm[Hg]"
    assert observation["code"] == "systolic_bp" and observation["device_id"] == "cuff-1"
    assert not journal(tmp_path).records()
    assert request(app, "GET", "/health").json()["state"] == "connected"
