"""createMeasurementRequest on the served demo hub (the production composition, not the harness): the keyed body
fingerprint and the audit policies resolve, a retry replays across a restart, and the same key with another body
or on another operation conflicts (SYSTEM 5.8, AGENTS section 6)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_health.app import cli, demo
from crew_health.app.station import HubResources

_BODY = {
    "capability_code": "pulse",
    "reason": "Pulse check after exertion",
    "decision_affected": "Whether to rest",
}


def _served(directory: Path, run: Callable[[TestClient], None]) -> None:
    state = json.loads((directory / demo.STATE).read_text(encoding="utf-8"))
    fixture = demo.Fixture.load(Path(state["root"]))

    def server(app: FastAPI, host: str, port: int) -> None:
        run(TestClient(app))

    code = cli.serve(
        database=directory / "hub.sqlite",
        settings=directory / "hub-settings.json",
        host="127.0.0.1",
        port=0,
        resources=HubResources(directory / "resources"),
        origin="SIM",
        confirmation=cli.TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
        server=server,
        demo=fixture.presenter(),
        mission=fixture.mission(directory),
    )
    assert code == 0


def _headers(client: TestClient, directory: Path, key: str) -> dict[str, str]:
    credential = json.loads((directory / demo.CREDENTIALS).read_text(encoding="utf-8"))["mara"]
    signed = client.post("/v1/sessions", json={"kind": "crew", "person_id": "mara", "credential": credential})
    assert signed.status_code in (200, 201), signed.text
    return {"Authorization": "Bearer " + signed.json()["token"], "Idempotency-Key": key}


def test_pulse_request_accepted_replayed_after_restart_and_conflicts(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    demo.reset(directory)
    key = secrets.token_hex(16)
    seen: dict[str, object] = {}

    def first(client: TestClient) -> None:
        headers = _headers(client, directory, key)
        created = client.post("/v1/measurement-requests", json=_BODY, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "proposed"
        seen["request"] = created.json()
        replay = client.post("/v1/measurement-requests", json=_BODY, headers=headers)
        assert replay.status_code == 201, replay.text
        assert replay.json()["request_id"] == created.json()["request_id"]
        other = client.post(
            "/v1/measurement-requests", json={**_BODY, "capability_code": "spo2"}, headers=headers
        )
        assert other.status_code == 409, other.text
        assert other.json()["code"] == "DUPLICATE_COMMAND"
        assert other.json()["current_state"] is None

    _served(directory, first)
    assert len((directory / "command-fingerprint.key").read_bytes()) == 32

    def after_restart(client: TestClient) -> None:
        replay = client.post("/v1/measurement-requests", json=_BODY, headers=_headers(client, directory, key))
        assert replay.status_code == 201, replay.text
        request = seen["request"]
        assert isinstance(request, dict)
        assert replay.json()["request_id"] == request["request_id"]

    _served(directory, after_restart)
