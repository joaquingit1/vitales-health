"""A loopback stand-in evaluation hub and the E21 harness ports over it, for the product-suite commands.

The hub serves the package's own example payloads (`contracts/api/examples`), adjusted per case, so every body
passes the hub's generated contract models that the driver parses with. Its behaviour is a script, not a
product: an opened concern gets a pulse request awaiting acceptance and an ECG request the kit gate refused
(with its gap), a rule raises one warning, and the assessment job needs the model, which the recorder counts.
Nothing it yields is a result about any model or about the product.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from crew_health.kernel.units import Quantity

from crew_health_bench.application.harness_ports import (
    EvaluationHub,
    HubCredentials,
    HubResponse,
    RunningHub,
)
from crew_health_bench.domain.cases import SimMeasurement
from crew_health_bench.domain.readiness import DRIVER_OPERATIONS

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/api/examples"
PERSONA = "sim_persona_1"

ROUTES = {
    "createSession": ("POST", "/v1/sessions"),
    "getContext": ("GET", "/v1/context"),
    "startEpisode": ("POST", "/v1/episodes"),
    "getEpisode": ("GET", "/v1/episodes/{episode_id}"),
    "getMeasurementRequest": ("GET", "/v1/measurement-requests/{request_id}"),
    "actOnMeasurementRequest": ("POST", "/v1/measurement-requests/{request_id}/actions"),
    "recordAttempt": ("POST", "/v1/measurement-requests/{request_id}/attempts"),
    "startAssessment": ("POST", "/v1/episodes/{episode_id}/assessments"),
    "getJob": ("GET", "/v1/jobs/{job_id}"),
    "getAssessment": ("GET", "/v1/assessments/{assessment_id}"),
    "getEvents": ("GET", "/v1/events"),
    "setAssistantRuntime": ("POST", "/v1/assistant/runtime"),
}


def example(name: str) -> dict[str, object]:
    value = json.loads((EXAMPLES / name).read_bytes())["value"]
    assert isinstance(value, dict)
    return value


@dataclass
class HubScript:
    """How the stand-in behaves: the warning level its rule raises and whether the assessment cites an
    observation nobody measured (the fabrication `NoFabricatedResult` must catch)."""

    mission_mode: str = "SIM"
    warning_level: str = "act"
    fabricate: bool = False
    measured_capability: str = "fixture_protocol_quantity"


@dataclass
class _HubState:
    script: HubScript
    model_running: bool = True
    model_requests: list[str] = field(default_factory=list)
    episodes: dict[str, dict[str, object]] = field(default_factory=dict)
    requests: dict[str, dict[str, object]] = field(default_factory=dict)
    events: list[dict[str, object]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    seeded: list[dict[str, object]] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    server: _HubServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, status: int, body: Mapping[str, object]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, code: str, *, retryable: bool = False) -> None:
        self._reply(
            status,
            {
                "code": code,
                "message": "stand-in",
                "retryable": retryable,
                "field_errors": [],
                "correlation_id": None,
                "current_state": None,
                "supported_alternative": None,
            },
        )

    def do_GET(self) -> None:
        self._route("GET", None)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self._route("POST", json.loads(self.rfile.read(length)) if length else None)

    def _route(self, method: str, body: dict[str, object] | None) -> None:
        state = self.server.state
        path = self.path.split("?", 1)[0]
        token = self.headers.get("Authorization")
        state.calls.append((method, path, token))
        for operation, (verb, template) in ROUTES.items():
            pattern = "^" + re.sub(r"\{[a-z_]+\}", "([A-Za-z0-9_.:-]+)", template) + "$"
            match = re.match(pattern, path)
            if verb == method and match:
                if operation not in ("createSession",) and not token:
                    self._error(401, "UNAUTHENTICATED")
                    return
                status, reply = getattr(self.server, "_" + operation)(body, *match.groups(), token=token)
                if "code" in reply:
                    self._error(status, str(reply["code"]))
                else:
                    self._reply(status, reply)
                return
        self._error(404, "NOT_FOUND")


class _HubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, script: HubScript) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = _HubState(script)

    @property
    def url(self) -> str:
        return "http://127.0.0.1:" + str(self.server_address[1])

    def _createSession(
        self, body: dict[str, object] | None, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        assert body is not None
        if body.get("credential") not in ("crew-pin", "operator-pin"):
            return 401, {"code": "UNAUTHENTICATED"}
        session = example("session/create-crew.json")
        session["token"] = "token-" + str(body["kind"]) + "-" + str(len(self.state.calls))
        session["session_id"] = "ses_" + str(len(self.state.calls))
        return 201, session

    def _getContext(self, body: object, *, token: str | None) -> tuple[int, dict[str, object]]:
        context = example("context/station.json")
        context["mission_mode"] = self.state.script.mission_mode
        return 200, context

    def _startEpisode(
        self, body: dict[str, object] | None, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        assert body is not None and body["origin_mode"] == "SIM"
        episode = example("clinical/episode.json")
        episode_id = "epi_" + str(len(self.state.episodes) + 1)
        pulse = example("clinical/measurement-request-pulse.json")
        pulse.update(request_id="mrq_pulse_" + episode_id, episode_id=episode_id, state="awaiting_acceptance")
        pulse["capability_code"] = self.state.script.measured_capability
        pulse.update(attempts=[], result_observation_ids=[], revision=1)
        ecg = example("clinical/measurement-request-ecg-blocked.json")
        ecg.update(request_id="mrq_ecg_" + episode_id, episode_id=episode_id)
        for request in (pulse, ecg):
            self.state.requests[str(request["request_id"])] = request
        gaps = deepcopy(episode["capability_gaps"])
        assert isinstance(gaps, list)
        for gap in gaps:
            gap["episode_id"] = episode_id
        episode.update(
            episode_id=episode_id,
            revision=1,
            patient_id=PERSONA,
            concern_text=body["concern_text"],
            answers=[],
            measurement_request_ids=[pulse["request_id"], ecg["request_id"]],
            capability_gaps=gaps,
            assessment_ids=[],
        )
        self.state.episodes[episode_id] = episode
        event = example("events/warning-raised.json")
        data = event["data"]
        assert isinstance(data, dict)
        data.update(warning_id="wrn_" + episode_id, level=self.state.script.warning_level, raised_by="rule")
        event.update(seq=len(self.state.events) + 1, event_id="evn_" + episode_id)
        self.state.events.append(event)
        return 201, episode

    def _getEpisode(
        self, body: object, episode_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        return 200, self.state.episodes[episode_id]

    def _getMeasurementRequest(
        self, body: object, request_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        return 200, self.state.requests[request_id]

    def _actOnMeasurementRequest(
        self, body: dict[str, object] | None, request_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        request = self.state.requests[request_id]
        assert body == {"action": "accept"} and request["state"] == "awaiting_acceptance"
        request.update(state="accepted", revision=int(str(request["revision"])) + 1)
        return 200, request

    def _recordAttempt(
        self, body: dict[str, object] | None, request_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        assert body is not None and token == "Bearer adapter-token"
        request = self.state.requests[request_id]
        if body["outcome"] == "success":
            observation = "obs_" + request_id
            self.state.observations.append(observation)
            request.update(state="completed", result_observation_ids=[observation])
        else:
            request.update(state="failed")
        request.update(revision=int(str(request["revision"])) + 1)
        return 200, request

    def _startAssessment(
        self, body: dict[str, object] | None, episode_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        if not self.state.model_running:
            return 503, {"code": "MODEL_UNAVAILABLE"}
        self.state.model_requests.append(episode_id)
        job = example("clinical/job.json")
        job.update(job_id="job_" + episode_id, state="running", progress=0.5, result_ref=None)
        return 202, job

    def _getJob(self, body: object, job_id: str, *, token: str | None) -> tuple[int, dict[str, object]]:
        job = example("clinical/job.json")
        job.update(job_id=job_id)
        job["result_ref"] = {"kind": "insight", "ref_id": "asm_" + job_id.removeprefix("job_")}
        return 200, job

    def _getAssessment(
        self, body: object, assessment_id: str, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        episode_id = assessment_id.removeprefix("asm_")
        assessment = example("clinical/assessment.json")
        cited = "obs_fabricated" if self.state.script.fabricate else "obs_mrq_pulse_" + episode_id
        assessment.update(assessment_id=assessment_id, episode_id=episode_id, state_snapshot_version=1)
        findings = assessment["findings"]
        assert isinstance(findings, list)
        findings[0]["evidence"] = [{"kind": "observation", "ref_id": cited}]
        return 200, assessment

    def _getEvents(self, body: object, *, token: str | None) -> tuple[int, dict[str, object]]:
        return 200, {
            "events": self.state.events,
            "next_cursor": str(len(self.state.events)),
            "has_more": False,
        }

    def _setAssistantRuntime(
        self, body: dict[str, object] | None, *, token: str | None
    ) -> tuple[int, dict[str, object]]:
        assert body == {"action": "stop"} and token is not None and "operator" in token
        self.state.model_running = False
        return 200, example("assistant/status-stopped.json")


class _Client:
    """E21 `HubClient.call` over loopback HTTP, wire JSON in and out."""

    def __init__(self, server: _HubServer) -> None:
        self.server = server
        self.http = httpx.Client(base_url=server.url, trust_env=False)

    def call(
        self,
        operation_id: str,
        *,
        path_params: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        idempotency_key: str | None,
        session: str | None,
        timeout: Quantity,
    ) -> HubResponse:
        method, template = ROUTES[operation_id]
        headers = {} if session is None else {"Authorization": "Bearer " + session}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        response = self.http.request(
            method,
            template.format(**path_params),
            params=dict(query),
            json=None if body is None else dict(body),
            headers=headers,
            timeout=float(timeout.value) / 1000,
        )
        return HubResponse(response.status_code, response.json(), response.headers.get("ETag"))


class _Gate:
    def __init__(self, state: _HubState) -> None:
        self.state, self.marks = state, [0]

    def mark(self, label: str) -> int:
        self.marks.append(len(self.state.model_requests))
        return len(self.marks) - 1

    def requests_between(self, start_mark: int, end_mark: int) -> Sequence[object]:
        return tuple(self.state.model_requests[self.marks[start_mark] : self.marks[end_mark]])


class _Sim:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def post_attempt(self, request_id: str, outcome: SimMeasurement, *, session: str) -> str:
        observations: list[dict[str, object]] = []
        if outcome.outcome == "accepted" and outcome.value is not None:
            observations.append(
                {
                    "patient_id": PERSONA,
                    "code": self.client.server.state.requests[request_id]["capability_code"],
                    "value": float(outcome.value.value),
                    "unit": outcome.value.unit.code,
                    "observed_at": "2026-11-15T09:00:00Z",
                    "origin_mode": "SIM",
                    "acquisition_method": "device",
                }
            )
        body: dict[str, object] = {
            "sequence": 1,
            "started_at": "2026-11-15T09:00:00Z",
            "ended_at": "2026-11-15T09:01:00Z",
            "outcome": "success" if observations else "failed",
            "observations": observations,
        }
        if not observations:
            body["failure_reason"] = outcome.reason or outcome.outcome
        response = self.client.http.post(
            "/v1/measurement-requests/" + request_id + "/attempts",
            json=body,
            headers={"Authorization": "Bearer adapter-token"},
        )
        assert response.status_code == 200
        return "att_" + request_id


@dataclass
class StandInHub:
    hub: RunningHub
    client: _Client
    gate: _Gate
    sim: _Sim
    credentials: HubCredentials
    server: _HubServer
    thread: threading.Thread

    def seed(self, steps: Sequence[Mapping[str, object]]) -> int:
        """E21's `SeedLoader` applies seed steps; the stand-in keeps them so a test can see what was
        seeded."""
        self.server.state.seeded.extend(dict(step) for step in steps)
        return len(steps)


class StandInHarness:
    """The E21 harness ports over fresh stand-in hubs, one per case, each with its own data directory."""

    def __init__(
        self, root: Path, script: HubScript | None = None, *, operations: tuple[str, ...] = DRIVER_OPERATIONS
    ) -> None:
        self.root, self.script, self.operations = root, script or HubScript(), operations
        self.started: list[StandInHub] = []
        self.stopped = 0

    @property
    def version(self) -> str:
        script = self.script
        return "stand-in-1:" + ":".join((script.mission_mode, script.warning_level, str(script.fabricate)))

    def mounted_operations(self) -> tuple[str, ...]:
        return self.operations

    def start(self, profile: str, *, run_id: str) -> EvaluationHub:
        assert profile == "evaluation"
        data_root = self.root / ("hub-" + str(len(self.started)))
        data_root.mkdir(parents=True)
        marker = {
            "kind": "evaluation",
            "run_id": run_id,
            "fixture_id": "fixture_suite",
            "created_at": "2026-11-15T09:00:00Z",
        }
        (data_root / ".chc-eval-marker.json").write_text(json.dumps(marker), encoding="utf-8")
        server = _HubServer(deepcopy(self.script))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = _Client(server)
        hub = StandInHub(
            RunningHub(data_root, server.url, "node_" + str(len(self.started)), profile),
            client,
            _Gate(server.state),
            _Sim(client),
            HubCredentials(PERSONA, "crew-pin", "operator-pin"),
            server,
            thread,
        )
        self.started.append(hub)
        return hub

    def stop(self, hub: EvaluationHub) -> None:
        assert isinstance(hub, StandInHub)
        hub.client.http.close()
        hub.server.shutdown()
        hub.server.server_close()
        hub.thread.join(timeout=5)
        shutil.rmtree(hub.hub.data_root)
        self.stopped += 1


@contextmanager
def harness(root: Path, script: HubScript | None = None) -> Iterator[StandInHarness]:
    value = StandInHarness(root, script)
    try:
        yield value
    finally:
        assert value.stopped == len(value.started)
