"""The demo-mara-fd19 hub as the 30 second cut drives it (DEMO-PLAN 3): `crew-health demo reset`, `demo serve` on a
real loopback port, the crew signing in, and the operator's cues, shot by shot. Every request goes over HTTP to the
served hub; the cues run through `crew-health demo cue`, as the operator runs them."""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

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

    def act(
        self, path: str, token: str, action: str, current: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        revision = current["revision"]
        assert isinstance(revision, int)
        status, reply = self.hub.call(
            "POST", path, token=token, body={"action": action}, release=self.release(), if_match=revision
        )
        assert isinstance(reply, dict), reply
        return status, reply

    def cue(self, name: str) -> int:
        return demo.main(["cue", name, "--dir", str(self.directory), "--hub", self.hub.base_url])


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    directory = tmp_path_factory.mktemp("demo") / "hub"
    port = _free_port()
    demo.reset(directory, root=ROOT, camera_port=_free_port(), twin_port=_free_port())
    started: list[uvicorn.Server] = []

    def server(app: FastAPI, host: str, bound: int) -> None:
        instance = uvicorn.Server(uvicorn.Config(app, host=host, port=bound, log_config=None))
        started.append(instance)
        instance.run()

    thread = threading.Thread(
        target=lambda: demo.serve(directory, host="127.0.0.1", port=port, server=server),
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


def test_the_camera_reads_ready_as_soon_as_its_worker_answers(served: Served) -> None:
    """The worker starts after the hub serves; the hub probes it every workers.camera.startup_probe_ms until it
    answers, so the camera-only capability turns available within that, not one 5 s health interval later."""
    state = json.loads((served.directory / demo.STATE).read_text(encoding="utf-8"))
    token = served.sign_in("mara")
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", int(state["camera_port"]))) == 0:
                break
        time.sleep(0.05)
    listening = time.monotonic()
    while True:
        items = served.get("/v1/capabilities", token)[1]["items"]
        assert isinstance(items, list)
        camera_only = next(item for item in items if item["methods"] == ["camera_estimate"])
        waited = time.monotonic() - listening
        if camera_only["status"] == "available" or waited > 5:
            break
        time.sleep(0.05)
    assert camera_only["status"] == "available" and waited < 2, (waited, camera_only)


def test_health_lists_the_camera_adapter_from_its_worker_probe(served: Served) -> None:
    """GET /v1/health adapters comes from the camera worker's last probe: the configured camera adapter, connected
    once the worker described itself (Health.adapters, contracts health.schema.json)."""
    deadline = time.monotonic() + 240
    while True:
        status, health = served.hub.call("GET", "/v1/health")
        assert status == 200 and isinstance(health, dict), health
        if health["adapters"] == [{"adapter_id": "adp_camera", "state": "connected"}]:
            break
        assert health["adapters"] == [{"adapter_id": "adp_camera", "state": "disconnected"}], health
        assert time.monotonic() < deadline, health
        time.sleep(0.2)


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


def test_shot_c_a_camera_pulse_request_reaches_the_twin_and_records_a_sim_reading(served: Served) -> None:
    """Mara asks for her pulse by camera: the request is submitted, accepted and started; the hub assigns the
    camera worker, the worker reads the camera twin's synthetic stream (never a real webcam) and records the
    attempt over recordAttempt with the adapter token. The reading lands in Mara's record labeled SIM."""
    token = served.sign_in("mara")
    # Until the hub's probe has seen the worker describe itself, the camera-only capability reads adapter_down.
    deadline = time.monotonic() + 240
    while True:
        items = served.get("/v1/capabilities", token)[1]["items"]
        assert isinstance(items, list)
        camera_only = next(item for item in items if item["methods"] == ["camera_estimate"])
        if camera_only["status"] == "available" or time.monotonic() > deadline:
            break
        time.sleep(2)
    assert camera_only["status"] == "available", camera_only
    status, request = served.post(
        "/v1/measurement-requests",
        token,
        {
            "capability_code": "pulse",
            "reason": "Pulse check after exertion",
            "decision_affected": "Whether to rest",
        },
    )
    assert status == 201, request
    request_id = request["request_id"]
    for action in ("submit", "accept", "start"):
        status, request = served.act(f"/v1/measurement-requests/{request_id}/actions", token, action, request)
        assert status == 200, request
    deadline = time.monotonic() + 240
    while request["state"] == "in_progress" and time.monotonic() < deadline:
        time.sleep(2)
        request = served.get(f"/v1/measurement-requests/{request_id}", token)[1]
    assert request["state"] == "completed", request
    attempts = request["attempts"]
    assert isinstance(attempts, list) and len(attempts) == 1
    attempt = attempts[0]
    assert (attempt["outcome"], attempt["adapter_id"]) == ("success", "adp_camera")
    observation_ids = request["result_observation_ids"]
    assert isinstance(observation_ids, list) and observation_ids == attempt["observation_ids"]
    # GET /v1/observations is RecordHttpServices (R-E02-31, not bound); the stored record is read directly.
    with sqlite3.connect(f"file:{served.directory / 'hub.sqlite'}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT patient_id, origin_mode, code, payload FROM records_observation WHERE observation_id = ?",
            (observation_ids[0],),
        ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [("mara", "SIM", "pulse")]
    payload = json.loads(rows[0][3])
    assert (payload["device_id"], payload["acquisition_method"]) == ("camera_twin_1", "camera_estimate")
    assert payload["quantity"]["unit"] == "/min"


def _steps(plan: dict[str, object]) -> dict[str, dict[str, object]]:
    stages = plan["stages"]
    assert isinstance(stages, list)
    return {step["step_id"]: step for stage in stages for step in stage.get("steps", [])}


def test_the_icl10_plan_shows_real_availability_and_the_ground_line(served: Served) -> None:
    """GET /v1/conditions/ICL10/plan on the served hub: content facts from the release's onboard plans and source
    registry, the environment from the demo mission, roles' operators over E02's roster and the demo overlays."""
    status, plan = served.get("/v1/conditions/ICL10/plan", served.sign_in("mara"))
    assert status == 200, plan
    steps = _steps(plan)
    note = steps["icl10_note_episodes"]
    assert (note["availability"], note["availability_text"]) == ("aboard", None)
    who = note["who_can"]
    assert isinstance(who, list) and {p["person_id"] for p in who} == {"mara", "ilya", "sofia", "tomas"}
    review = steps["icl10_mo_review"]
    assert review["availability"] == "aboard"
    assert isinstance(review["who_can"], list) and "ilya" in {p["person_id"] for p in review["who_can"]}
    # Nobody has a recorded pulse check practice yet: the step waits for a skill holder, it is not "Unknown".
    assert steps["icl10_pulse_by_crewmate"]["availability"] == "needs_skill_holder"
    stages = plan["stages"]
    assert isinstance(stages, list)
    ground = next(s for s in stages if s["stage_id"] == "ground_log")
    assert ground["link_text"]


def test_shot_d_eva_ilya_puts_the_medical_officer_on_a_surface_walk(served: Served) -> None:
    """`demo cue eva-ilya` records Ilya's eva overlay; roles reads it through OverlayPort, so the plan's medical
    officer step and every crew step leave her out while she walks."""
    assert served.cue("eva-ilya") == 0
    assert served.cue("eva-ilya") == 0  # I-O1: a second start keeps the one active walk
    overlays = json.loads((served.directory / "overlays.json").read_text(encoding="utf-8"))["overlays"]
    assert [(o["person_id"], o["overlay"], o["state"]) for o in overlays] == [("ilya", "eva", "active")]
    status, plan = served.get("/v1/conditions/ICL10/plan", served.sign_in("tomas"))
    assert status == 200, plan
    steps = _steps(plan)
    for step_id in ("icl10_mo_review", "icl10_note_episodes"):
        who = steps[step_id]["who_can"]
        assert isinstance(who, list) and who and "ilya" not in {p["person_id"] for p in who}


def test_shot_d_roles_propose_tomas_on_the_icl10_run_and_his_guided_check_starts(served: Served) -> None:
    """Shot D on the served hub. `demo cue drill-tomas-pulse` runs E31's own drill commands as Tomas, so his SIM
    practice makes him a pulse_check_wrist holder, and Ilya is on her walk (the eva-ilya shot above). Mara presses
    Someone needs help, the ICL10 run starts from that help session, E31 proposes Tomas as its helper (freshest
    holder, with Ilya named as the officer on the walk), Tomas accepts, and the guided check starts for that run."""
    assert served.cue("drill-tomas-pulse") == 0
    mara, tomas = served.sign_in("mara"), served.sign_in("tomas")
    opened_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, session = served.post(
        "/v1/help-sessions",
        mara,
        {"subject": {"kind": "self"}, "entry": {"kind": "button"}, "client_opened_at": opened_at},
    )
    assert status == 201, session
    status, run = served.post(
        "/v1/plan-runs",
        mara,
        {"plan_id": "plan_icl10", "subject_person_id": "mara", "help_session_id": session["help_session_id"]},
    )
    assert status == 201, run
    assert (run["state"], run["help_session_id"], run["episode_id"]) == (
        "active",
        session["help_session_id"],
        None,
    )
    steps = run["steps"]
    assert isinstance(steps, list)
    assert {"step_id": "icl10_pulse_by_crewmate", "state": "open"}.items() <= steps[0].items()
    path = f"/v1/plan-runs/{run['plan_run_id']}/roles"
    deadline = time.monotonic() + 60
    items: list[dict[str, object]] = []
    while not items and time.monotonic() < deadline:
        status, roles = served.get(path, mara)
        assert status == 200, roles
        items = cast(list[dict[str, object]], roles["items"])
        if not items:
            time.sleep(0.5)
    assert len(items) == 1, items
    helper = items[0]
    assert (helper["role_id"], helper["skill_id"], helper["state"]) == (
        "lead_helper",
        "pulse_check_wrist",
        "proposed",
    )
    assert helper["person"] == {"person_id": "tomas", "display_name": "Tomas Lind"}
    assert helper["reason_code"] == "freshest_skill"
    reasons = cast(list[dict[str, object]], helper["context_reasons"])
    assert [r["reason_code"] for r in reasons] == ["officer_on_eva"]
    revision = helper["revision"]
    assert isinstance(revision, int)
    status, accepted = served.hub.call(
        "POST",
        path,
        token=tomas,
        body={
            "action": "accept",
            "role_assignment_id": helper["role_assignment_id"],
            "client_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        release=served.release(),
        if_match=revision,
    )
    assert status == 200 and isinstance(accepted, dict), accepted
    assert accepted["state"] == "accepted"
    status, check = served.post(
        "/v1/guided-checks",
        mara,
        {
            "skill_id": "pulse_check_wrist",
            "helper_person_id": "tomas",
            "subject_person_id": "mara",
            "plan_run_id": run["plan_run_id"],
            "role_assignment_id": helper["role_assignment_id"],
        },
    )
    assert status == 201, check
    assert (check["state"], check["teach_step_no"]) == ("teaching", 1)
    assert check["helper"] == {"person_id": "tomas", "display_name": "Tomas Lind"}


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
