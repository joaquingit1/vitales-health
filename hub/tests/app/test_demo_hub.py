"""The demo-mara-fd19 hub as the 30 second cut drives it (DEMO-PLAN 3): `crew-health demo reset`, `demo serve` on a
real loopback port, the crew signing in, and the operator's cues, shot by shot. Every request goes over HTTP to the
served hub; the cues run through `crew-health demo cue`, as the operator runs them."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from crew_health.app import demo
from crew_health.app.demo import Hub

pytestmark = pytest.mark.trace("D15", "D18", "D28", "D33", "R-E02-31", "R-E02-32", "R-E02-33")

ROOT = Path(__file__).resolve().parents[3]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@dataclass(frozen=True, slots=True)
class Served:
    directory: Path
    hub: Hub
    credentials: dict[str, str]

    def sign_in(self, person_id: str) -> str:
        status, body = self.hub.call(
            "POST",
            "/v1/sessions",
            body={"kind": "crew", "person_id": person_id, "credential": self.credentials[person_id]},
        )
        assert status == 201, body
        assert isinstance(body, dict)
        return str(body["token"])

    def release(self) -> str:
        _, health = self.hub.call("GET", "/v1/health")
        assert isinstance(health, dict)
        return str(health["config_release_id"])

    def get(self, path: str, token: str) -> tuple[int, dict[str, object]]:
        status, body = self.hub.call("GET", path, token=token)
        assert isinstance(body, dict), body
        return status, body

    def post(self, path: str, token: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        status, reply = self.hub.call("POST", path, token=token, body=body, release=self.release())
        assert isinstance(reply, dict), reply
        return status, reply

    def cue(self, name: str) -> int:
        return demo.main(["cue", name, "--dir", str(self.directory), "--hub", self.hub.base_url])


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    directory = tmp_path_factory.mktemp("demo") / "hub"
    port = _free_port()
    demo.reset(directory, root=ROOT)
    started: list[uvicorn.Server] = []

    def server(app: FastAPI, host: str, bound: int) -> None:
        instance = uvicorn.Server(uvicorn.Config(app, host=host, port=bound, log_config=None))
        started.append(instance)
        instance.run()

    thread = threading.Thread(
        target=lambda: demo.serve(directory, host="127.0.0.1", port=port, server=server, camera=False),
        daemon=True,
    )
    thread.start()
    hub = Hub(f"http://127.0.0.1:{port}")
    deadline = time.monotonic() + 300
    while True:
        try:
            if hub.call("GET", "/v1/health")[0] == 200:
                break
        except OSError:
            pass
        if time.monotonic() > deadline or not thread.is_alive():
            raise TimeoutError("the demo hub did not serve")
        time.sleep(0.5)
    credentials = json.loads((directory / demo.CREDENTIALS).read_text(encoding="utf-8"))
    try:
        yield Served(directory, hub, credentials)
    finally:
        for instance in started:
            instance.should_exit = True
        thread.join(60)


def test_the_hub_serves_ready_with_the_presenter_and_tomas_signed_in(served: Served) -> None:
    status, health = served.hub.call("GET", "/v1/health")
    assert status == 200 and isinstance(health, dict) and health["status"] == "ok"
    mara, tomas = served.sign_in("mara"), served.sign_in("tomas")
    _, crew = served.get("/v1/crew", tomas)
    items = crew["items"]
    assert isinstance(items, list)
    people = {item["person_id"]: item for item in items}
    assert {"mara", "tomas", "ilya"} <= people.keys()
    assert people["ilya"]["medical_officer_rank"] == 1
    assert served.get("/v1/crew", mara)[0] == 200


def test_shot_a_the_kit_map_counts_come_from_the_server(served: Served) -> None:
    status, kit = served.get("/v1/kit-map", served.sign_in("mara"))
    assert status == 200, kit
    assert kit["environment_id"] == "station_leo"
    counts = kit["counts"]
    assert isinstance(counts, dict) and isinstance(kit["conditions"], list)
    configured = json.loads((ROOT / "config/kit-map.json").read_text(encoding="utf-8"))["conditions"]
    assert counts["total"] == len(kit["conditions"]) == len(configured)
    assert (
        counts["measured_here"] + counts["report_only"] + counts["needs_test_not_aboard"] == counts["total"]
    )
    assert {row["condition_id"] for row in kit["conditions"]} >= {"ICL10"}


def test_shot_b_the_ground_link_goes_down_with_next_contact_26_hours_ahead(served: Served) -> None:
    token = served.sign_in("mara")
    assert served.cue("environment-lunar-surface") == 0
    before = datetime.now(UTC)
    assert served.cue("ground-link-down") == 0
    status, link = served.get("/v1/ground-link", token)
    assert status == 200, link
    assert (link["status"], link["source"], link["gap_planned"]) == ("down", "demo_switch", True)
    assert link["gap_kind"] == "planned_line_of_sight"
    assert link["environment_id"] == "lunar_surface"
    hours = (datetime.fromisoformat(str(link["next_contact_at"])) - before).total_seconds() / 3600
    assert 25.9 < hours < 26.1
    _, kit = served.get("/v1/kit-map", token)
    assert kit["environment_id"] == "lunar_surface"


def test_shot_e_help_opens_with_the_model_stopped(served: Served) -> None:
    """Someone needs help on the served hub (AGENTS 1 rule 3): no model is running, the session opens and reads
    back. setAssistantRuntime is not on main, so model-stop is refused as absent and the assistant stays as
    health reports it: unavailable."""
    _, health = served.hub.call("GET", "/v1/health")
    assert isinstance(health, dict) and health["assistant_state"] == "unavailable"
    assert served.cue("model-stop") == 1
    token = served.sign_in("mara")
    opened_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, session = served.post(
        "/v1/help-sessions",
        token,
        {
            "subject": {"kind": "crewmate", "person_id": "tomas"},
            "entry": {"kind": "button"},
            "client_opened_at": opened_at,
        },
    )
    assert status == 201, session
    assert session["state"] == "open"
    status, read = served.get(f"/v1/help-sessions/{session['help_session_id']}", token)
    assert status == 200 and read["subject"] == session["subject"]
