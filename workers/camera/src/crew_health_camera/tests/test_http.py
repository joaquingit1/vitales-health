"""The hub to worker protocol over HTTP (A7): authentication, start/status/cancel replies and error codes, and the
published schema; plus recordAttempt delivery to the hub (V11, V13, V35)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from crew_health_camera.adapters.http_app import build_app
from crew_health_camera.adapters.hub_client import HttpHubClient
from crew_health_camera.adapters.protocol import DescribeReply, StatusReply, schema_document
from crew_health_camera.adapters.sim_source import Scene
from crew_health_camera.application.ports import Delivery
from crew_health_camera.tests.support import BOUNDS, STEADY, Rig, reference, rig

TOKEN = "hub-to-worker-secret"
AUTH = {"Authorization": "Bearer " + TOKEN}
SCHEMA = Path(__file__).resolve().parents[1] / "protocol.schema.json"


def client(r: Rig) -> TestClient:
    return TestClient(build_app(r.service, adapter_id="adp_camera", token=TOKEN))


def start_body(acquisition_id: str = "acq_1", **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "acquisition_id": acquisition_id,
        "kind": "measurement",
        "capability_code": "pulse",
        "request_id": "mrq_1",
        "patient_id": "person_a",
        "sequence": 1,
        "device_ids": ["camera_1"],
        "required_origin": "SIM",
        "ttl_ms": 60000,
        "warmup_max_ms": 5000,
    }
    body.update(extra)
    return body


@pytest.mark.trace("A7", "AGENTS-1.7")
def test_every_protocol_route_but_health_needs_the_token() -> None:
    http = client(rig({"camera_1": STEADY}))
    assert http.get("/health").json() == {"status": "ok"}
    for method, path in (
        ("GET", "/v1/describe"),
        ("POST", "/v1/acquisitions"),
        ("GET", "/v1/acquisitions/a"),
        ("POST", "/v1/acquisitions/a/cancel"),
    ):
        response = http.request(method, path, json=start_body(), headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401 and response.json()["code"] == "UNAUTHORIZED"
    with pytest.raises(ValueError):
        build_app(rig({}).service, adapter_id="adp_camera", token="")


@pytest.mark.trace("A7", "AGENTS-1.7", "m5")
def test_only_a_bearer_credential_is_accepted_and_it_is_checked_before_the_body() -> None:
    http = client(rig({"camera_1": STEADY}))
    assert http.get("/v1/describe", headers={"Authorization": TOKEN}).status_code == 401
    assert http.get("/v1/describe", headers={"Authorization": "Basic " + TOKEN}).status_code == 401
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        bad = http.post("/v1/acquisitions", json={"nope": 1}, headers=headers)
        assert bad.status_code == 401 and bad.json() == {"code": "UNAUTHORIZED", "detail": "token"}
    huge = http.post("/v1/acquisitions", content=b"{" + b" " * 70_000 + b"}", headers=AUTH)
    assert huge.status_code == 422 and huge.json()["detail"] == "body_too_large"


@pytest.mark.trace("A7", "V01")
def test_describe_reports_cameras_readiness_and_origin() -> None:
    reply = DescribeReply.model_validate(
        client(rig({"camera_1": STEADY, "camera_2": STEADY})).get("/v1/describe", headers=AUTH).json()
    )
    assert [c.device_id for c in reply.cameras] == ["camera_1", "camera_2"] and reply.origin_mode == "SIM"
    assert {c.subject_id for c in reply.cameras} == {"person_a"} and reply.max_ttl_ms == BOUNDS.max_ttl_ms
    assert reply.ready_methods == ["camera_estimate"] and reply.capability_codes == [
        "pulse",
        "respiratory_rate",
    ]
    empty = DescribeReply.model_validate(client(rig({})).get("/v1/describe", headers=AUTH).json())
    assert empty.ready_methods == [] and empty.capability_codes == []


@pytest.mark.trace("A7", "V13", "D09")
def test_start_status_and_cancel_round_trip() -> None:
    r = rig({"camera_1": STEADY})
    http = client(r)
    started = http.post("/v1/acquisitions", json=start_body(), headers=AUTH)
    assert started.status_code == 202 and started.json()["state"] == "accepted"
    again = http.post("/v1/acquisitions", json=start_body(), headers=AUTH)
    assert again.json()["receipt_id"] == started.json()["receipt_id"]
    conflict = http.post("/v1/acquisitions", json=start_body(sequence=2), headers=AUTH)
    assert conflict.status_code == 409 and conflict.json()["code"] == "CONFLICT"
    r.service.run_next()
    status = StatusReply.model_validate(http.get("/v1/acquisitions/acq_1", headers=AUTH).json())
    assert status.state == "delivered" and status.outcome is not None
    assert status.outcome.kind == "reading" and status.outcome.selected is not None
    assert (status.outcome.selected.value, status.outcome.selected.camera_name) == ("96", "Camera 1")
    assert http.post("/v1/acquisitions/acq_1/cancel", headers=AUTH).json()["result"] == "cannot_stop"
    missing = http.get("/v1/acquisitions/nope", headers=AUTH)
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"


@pytest.mark.trace("E30-X8", "Q8", "M8")
def test_a_persisted_reading_is_released_only_by_a_cancel_naming_the_recorded_observation() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(reference(persist=True))
    r.clock.advance(5)
    r.service.run_next()
    http = client(r)
    assert http.post("/v1/acquisitions/ref_1/cancel", headers=AUTH).json()["result"] == "cannot_stop"
    bad = http.post("/v1/acquisitions/ref_1/cancel", content=b"{", headers=AUTH)
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID"
    named = {"recorded_observation_id": "obs_1"}
    assert http.post("/v1/acquisitions/ref_1/cancel", json=named, headers=AUTH).json()["result"] == "stopped"


@pytest.mark.trace("A7", "V11")
def test_refusals_map_to_protocol_codes_and_malformed_bodies_are_rejected() -> None:
    r = rig({"camera_1": Scene(pulse_hz=None, breath_hz=None)}, bounds=BOUNDS)
    http = client(r)
    assert (
        http.post("/v1/acquisitions", json=start_body(capability_code="spo2"), headers=AUTH).status_code
        == 422
    )
    assert http.post("/v1/acquisitions", json=start_body(extra_field=1), headers=AUTH).status_code == 422
    for index in range(BOUNDS.max_acquisitions):
        http.post("/v1/acquisitions", json=start_body(f"acq_{index}"), headers=AUTH)
    busy = http.post("/v1/acquisitions", json=start_body("acq_more"), headers=AUTH)
    assert busy.status_code == 429 and busy.json()["code"] == "BUSY"
    r.service.run_next()
    status = StatusReply.model_validate(http.get("/v1/acquisitions/acq_0", headers=AUTH).json())
    assert status.outcome is not None and status.outcome.kind == "no_reading"
    assert status.outcome.reason is not None and status.outcome.views == []
    assert status.outcome.reason_text == r.service.policy.reasons[status.outcome.reason]


@pytest.mark.trace("A7")
def test_published_protocol_schema_matches_the_models() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == json.loads(
        json.dumps(schema_document(), sort_keys=True)
    )


def hub_with(handler: httpx.MockTransport) -> HttpHubClient:
    return HttpHubClient(
        httpx.Client(base_url="http://hub", transport=handler), token="adapter-token", timeout_s=1.0
    )


@pytest.mark.trace("V35", "V13")
def test_hub_client_maps_replies_and_sends_key_and_token() -> None:
    seen: list[httpx.Request] = []

    def answer(status: int, body: object) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json=body)

        return httpx.MockTransport(handle)

    assert hub_with(answer(200, {})).record_attempt("mrq_1", {}, idempotency_key="k1") == Delivery(
        "accepted", None
    )
    assert (
        seen[0].headers["idempotency-key"] == "k1"
        and seen[0].headers["authorization"] == "Bearer adapter-token"
    )
    assert seen[0].url.path == "/v1/measurement-requests/mrq_1/attempts"
    assert hub_with(answer(403, {"code": "ADAPTER_NOT_TRUSTED"})).record_attempt(
        "m", {}, idempotency_key="k"
    ) == Delivery("rejected", "ADAPTER_NOT_TRUSTED")
    assert hub_with(answer(422, ["x"])).record_attempt("m", {}, idempotency_key="k") == Delivery(
        "rejected", "hub_422"
    )
    assert hub_with(answer(503, {})).record_attempt("m", {}, idempotency_key="k") == Delivery(
        "unreachable", "hub_503"
    )

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    assert hub_with(httpx.MockTransport(down)).record_attempt("m", {}, idempotency_key="k") == Delivery(
        "unreachable", "unreachable"
    )

    def text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="not json")

    assert hub_with(httpx.MockTransport(text)).record_attempt("m", {}, idempotency_key="k") == Delivery(
        "rejected", "hub_400"
    )
    with pytest.raises(ValueError):
        HttpHubClient(httpx.Client(), token="", timeout_s=1.0)


@pytest.mark.trace("V15")
def test_worker_utc_is_reported_for_the_hub_offset() -> None:
    reply = client(rig({"camera_1": STEADY})).get("/v1/describe", headers=AUTH).json()
    assert datetime.fromisoformat(reply["worker_utc"]) == datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
