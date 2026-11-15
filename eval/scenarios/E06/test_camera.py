# mypy: disable-error-code="type-abstract"
# The registry keys providers by Protocol classes (SYSTEM 5), which mypy reports as type-abstract.
"""E06 end to end: E05 measurement requests served by the real camera worker over protocol v1.

The hub side is the real E05 module and the real camera module on SQLite, with E01's memory task queue
running the camera's tasks; the worker is the real crew_health_camera service and HTTP app. The hub reaches
the worker through a transport routed to the worker's app, and the worker records attempts through the hub's
real recordAttempt route with its adapter token. Composition binds E05's adapter ports, the mission state
and the hub side attempt report to the camera module the way follow-up F1 will (A1, A7).

Hermetic scenarios use the SIM frame source: no camera, no network, no wall clock. The real path (camera
twin MJPEG stream, worker process with MediaPipe, hub over HTTP, E05 recordAttempt) is
`test_the_twin_stream_is_measured_end_to_end`, marked hardware and run with `-m hardware`; the twin declares
itself SIM, so its readings are SIM through the normal source rule. A LIVE reading needs a real camera,
which only a person runs (workers/camera test_hardware.py).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pytest

_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ROOT / "hub/tests/measurement", _ROOT / "hub/tests/camera", _ROOT / "workers/camera/src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from camera_harness import CAMERA_MIGRATION, Mission, camera_settings, memory_tasks  # noqa: E402
from crew_health.app.registry import DeclarationRegistry  # noqa: E402
from crew_health.camera import module as camera_module  # noqa: E402
from crew_health.camera.adapters.http_transport import HttpWorkerTransport  # noqa: E402
from crew_health.camera.application import ports as camera_ports  # noqa: E402
from crew_health.camera.application.ports import ReportResult, WorkerReply, WorkerUnreachable  # noqa: E402
from crew_health.contracts_gen.enums import EventType, Role  # noqa: E402
from crew_health.kernel.clock import Clock  # noqa: E402
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore  # noqa: E402
from crew_health.kernel.ids import IdFactory  # noqa: E402
from crew_health.kernel.settings import HubSettings  # noqa: E402
from crew_health.kernel.tasks import TaskQueue, TaskRunner  # noqa: E402
from crew_health.kernel.testing import MemoryClock, MemoryCommands, MemoryUnits  # noqa: E402
from crew_health.kernel.units import UnitRegistry  # noqa: E402
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWorkFactory  # noqa: E402
from crew_health.measurement import module as measurement_module  # noqa: E402
from crew_health.measurement.application import ports as measurement_ports  # noqa: E402
from crew_health.measurement.application.ports import MissionFacts  # noqa: E402
from crew_health_camera.adapters.http_app import build_app  # noqa: E402
from crew_health_camera.adapters.hub_client import HttpHubClient  # noqa: E402
from crew_health_camera.adapters.sim_source import Scene, SimSource  # noqa: E402
from crew_health_camera.application.ports import Camera, Delivery  # noqa: E402
from crew_health_camera.application.service import AcquisitionService  # noqa: E402
from crew_health_camera.tests.support import BOUNDS, FakeClock, MemoryEvidence, policy  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from measurement_harness import (  # noqa: E402
    KIT_MAP,
    MEASUREMENT_DEFAULTS,
    START,
    UI_COPY,
    ConsentFake,
    CountingIds,
    EpisodesFake,
    GapPlansFake,
    HmacFingerprint,
    KitMapFake,
    ObservationsFake,
    SessionsFake,
    SqliteUowFactory,
    act,
    headers,
    pulse_body,
    seed_crew,
    settings_for,
)

pytestmark = pytest.mark.trace("A7", "E06-01", "E06-02", "R09", "R11", "R29")

WORKER_TOKEN = "hub-to-worker-token"
OFFSET = timedelta(seconds=2)  # the worker's clock runs 2 s ahead of the hub's
STEADY = Scene(pulse_hz=1.6, breath_hz=0.25)
QUALITY = policy()
MARA_BOTH = {"camera_1": "mara", "camera_2": "mara"}


@dataclass
class WorkerTransport:
    """The hub's WorkerTransport routed to the worker's FastAPI app; `drop_next` loses start replies after the
    worker has processed the call (a lost reply, V16)."""

    client: TestClient
    up: bool = True
    drop_next: int = 0
    paths: list[str] = field(default_factory=list)

    def call(
        self, method: Literal["GET", "POST"], path: str, body: bytes | None, *, timeout_s: float
    ) -> WorkerReply:
        if not self.up:
            raise WorkerUnreachable("connection refused")
        self.paths.append(path)
        sent = {"Authorization": "Bearer " + WORKER_TOKEN, "Accept": "application/json"}
        if body is not None:
            sent["Content-Type"] = "application/json"  # as HttpWorkerTransport sends it
        response = self.client.request(method, path, content=body, headers=sent)
        if self.drop_next and path == "/v1/acquisitions":
            self.drop_next -= 1
            raise WorkerUnreachable("reply lost")
        return WorkerReply(response.status_code, response.content)


@dataclass
class HubReports:
    """F1 binding of the camera's AttemptReports: E05's real recordAttempt route under the camera adapter's
    token (the same identity the worker records with), outcome failed, no observation, the reason code as
    the reason."""

    client: HttpHubClient | None = None

    def failed(
        self, request_id: str, *, sequence: int, at: datetime, reason: str, idempotency_key: str
    ) -> ReportResult:
        assert self.client is not None
        stamp = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        body = {
            "sequence": sequence,
            "started_at": stamp,
            "ended_at": stamp,
            "outcome": "failed",
            "failure_reason": reason,
            "observations": [],
            "quality_notes": [reason],
        }
        delivery = self.client.record_attempt(request_id, body, idempotency_key=idempotency_key)
        return {"accepted": "recorded", "rejected": "refused"}.get(delivery.kind, "unreachable")  # type: ignore[return-value]


@dataclass
class Stack:
    hub: TestClient
    app: FastAPI
    uows: SqliteUowFactory
    clock: MemoryClock
    observations: ObservationsFake
    sessions: SessionsFake
    live: camera_module.Live
    registry: DeclarationRegistry
    service: AcquisitionService
    worker_clock: FakeClock
    source: SimSource
    transport: WorkerTransport
    evidence: MemoryEvidence
    late: _LateHub
    delivered: int = 0

    def deliver_events(self) -> None:
        """The durable subscriber `camera.dispatch`: every new measurement_request.changed, in order."""
        subscriber = self.registry.declarations["durable_subscribers"][camera_module.SUBSCRIBER]
        _, handler = cast(tuple[object, Callable[[object, object], None]], subscriber)
        events = self.uows.state.events
        while self.delivered < len(events):
            event = events[self.delivered]
            self.delivered += 1
            if event.type == EventType.MEASUREMENT_REQUEST_CHANGED:
                with self.uows.begin(
                    actor=Actor("engine", "events"), correlation_id=f"cor_sub_{event.seq}"
                ) as uow:
                    handler(uow, event)

    def tick(self) -> int:
        """One pass of the camera link: deliver events, probe, reconcile, run the camera tasks."""
        self.deliver_events()
        assert self.live.loop is not None
        ran = self.live.loop.tick()
        self.deliver_events()
        return ran

    def work(self) -> None:
        """The worker captures and decides, the hub clock follows, then the worker records the attempt."""
        self.service.run_next()
        self.clock.at = self.worker_clock.utc_now() - OFFSET
        self.service.deliver_pending()
        self.deliver_events()

    def restart_worker(self) -> None:
        """A new worker process over the same state directory (its in-memory table is gone)."""
        self.service = AcquisitionService(
            policy=QUALITY, source=self.source, clock=self.worker_clock, hub=self.late, bounds=BOUNDS,
            receipt_ids=_receipts(), evidence=self.evidence,
        )  # fmt: skip
        self.transport.client = TestClient(
            build_app(self.service, adapter_id="adp_camera", token=WORKER_TOKEN)
        )

    def request(self, request_id: str, token: str = "mara") -> dict[str, Any]:
        body: dict[str, Any] = self.hub.get(
            f"/v1/measurement-requests/{request_id}", headers=headers(token)
        ).json()
        return body


def _receipts() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"rcp_{next(counter)}"


def build(
    tmp_path: Path,
    scenes: dict[str, Scene],
    *,
    mode: Literal["LIVE", "SIM"] = "SIM",
    pairs: Mapping[str, str | None] | None = None,
    camera_names: dict[str, str] | None = None,
    worker: camera_ports.WorkerTransport | None = None,
) -> Stack:
    """`pairs` maps each camera to the person its device configuration pairs it with (default: camera_1 with
    Mara); a camera it leaves out is paired with nobody and never used (B1). `worker`
    replaces the in-process worker with another transport (the hardware run reaches a worker process over
    HTTP)."""
    settings = camera_settings(settings_for(MEASUREMENT_DEFAULTS))
    clock = MemoryClock(START, 0, "epoch_1")
    ids = CountingIds()
    uows = SqliteUowFactory(
        clock=clock, ids=ids, settings=settings, path=tmp_path / "hub.db", release_id="rel_1"
    )
    uows.writer.db.executescript(CAMERA_MIGRATION.read_text(encoding="utf-8"))
    commands = MemoryCommands(ids=ids, settings=settings, authorize=lambda *_: True, operations={})
    kit = KitMapFake(KIT_MAP, UI_COPY)
    observations, sessions, episodes = ObservationsFake(), SessionsFake(), EpisodesFake()

    worker_clock = FakeClock(utc=START + OFFSET)
    paired = dict(pairs if pairs is not None else {"camera_1": "mara"})
    rig_cameras = tuple(
        Camera(d, (camera_names or {}).get(d, "Camera " + d.rsplit("_", 1)[-1]), "station", paired.get(d))
        for d in sorted(scenes)
    )
    source = SimSource(
        clock=worker_clock, cameras=rig_cameras, scenes=scenes, rate_hz=30.0, seed=7, failing=False
    )
    late, evidence = _LateHub(), MemoryEvidence()
    service = AcquisitionService(
        policy=QUALITY, source=source, clock=worker_clock, hub=late, bounds=BOUNDS, receipt_ids=_receipts(),
        evidence=evidence,
    )  # fmt: skip
    transport = WorkerTransport(TestClient(build_app(service, adapter_id="adp_camera", token=WORKER_TOKEN)))
    reports = HubReports()

    providers: list[tuple[type[object], object]] = [
        (HubSettings, settings),
        (Clock, clock),
        (UnitOfWorkFactory, uows),
        (ReadSnapshotFactory, uows),
        (IdFactory, ids),
        (IdempotencyStore, commands),
        (CommandReceipts, commands),
        (UnitRegistry, MemoryUnits({"/min": ("frequency", Decimal(1), Decimal(0))})),
        (measurement_ports.KitGate, kit),
        (measurement_ports.KitCapabilitiesPort, kit),
        (measurement_ports.MissionStatePort, _MissionFake(mode)),
        (measurement_ports.EpisodeFacts, episodes),
        (measurement_ports.ConsentQueries, ConsentFake()),
        (measurement_ports.ObservationCommands, observations),
        (measurement_ports.GapPlanPort, GapPlansFake(KIT_MAP)),
        (measurement_ports.SessionAuthenticator, sessions),
        (measurement_ports.CommandFingerprint, HmacFingerprint()),
        (camera_ports.WorkerTransport, worker or transport),
        (camera_ports.MissionStatePort, Mission(mode)),
        (camera_ports.AttemptReports, reports),
    ]
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    for port, value in providers:
        registry.provide(port, _const(value))
    live = camera_module.Live()
    camera_module.register(registry, live)
    measurement_module.register(registry)
    tasks = memory_tasks(settings, list(registry.declarations["tasks"].values()))  # type: ignore[arg-type]
    registry.provide(TaskQueue, lambda _resolver: tasks)
    registry.provide(TaskRunner, lambda _resolver: tasks)
    # Composition F1: E05's adapter ports are served by the camera providers.
    registry.provide(
        measurement_ports.AdapterReadinessPort,
        lambda r: r.require(camera_ports.AdapterReadinessPort),
        requires=(camera_ports.AdapterReadinessPort,),
    )
    registry.provide(
        measurement_ports.AdapterAssignmentPort,
        lambda r: _E05Assignments(r.require(camera_ports.AdapterAssignmentPort)),
        requires=(camera_ports.AdapterAssignmentPort,),
    )
    registry.resolve()
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        app.include_router(router)  # type: ignore[arg-type]
    hub = TestClient(app)
    late.client = HttpHubClient(hub, token="camera", timeout_s=5.0)
    reports.client = HttpHubClient(hub, token="camera", timeout_s=5.0)
    stack = Stack(
        hub, app, uows, clock, observations, sessions, live, registry, service, worker_clock, source,
        transport, evidence, late,
    )  # fmt: skip
    hub_stub = _HubStub(stack)
    seed_crew(hub_stub)  # type: ignore[arg-type]
    stack.sessions.crew("presenter", "presenter", Role.CREW)
    return stack


@dataclass
class _MissionFake:
    mode: Literal["LIVE", "SIM"]

    def current(self, uow: object = None) -> MissionFacts | None:
        return MissionFacts("mis_1", self.mode, "lunar_surface", None, "node_hub")


@dataclass
class _E05Assignments:
    """F1 binding: the camera's assignment as E05's own AdapterAssignment class (the dataclasses are nominal,
    so composition converts the value; the protocols are structural)."""

    camera: camera_ports.AdapterAssignmentPort

    def assign(
        self, view: measurement_ports.View, *, capability_code: str, methods: tuple[str, ...]
    ) -> measurement_ports.AdapterAssignment | None:
        value = self.camera.assign(view, capability_code=capability_code, methods=methods)
        if value is None:
            return None
        return measurement_ports.AdapterAssignment(value.adapter_id, value.method, value.device_id)


@dataclass
class _LateHub:
    """The worker's HubClient, bound once the hub's test client exists (the worker reaches the hub's real
    route with its adapter token); `down` makes the hub unreachable."""

    client: HttpHubClient | None = None
    down: bool = False

    def record_attempt(
        self, request_id: str, body: Mapping[str, object], *, idempotency_key: str
    ) -> Delivery:
        assert self.client is not None
        if self.down:
            return Delivery("unreachable", "unreachable")
        return self.client.record_attempt(request_id, body, idempotency_key=idempotency_key)


@dataclass
class _HubStub:
    """seed_crew needs only sessions and episodes; give it this stack's."""

    stack: Stack

    @property
    def sessions(self) -> SessionsFake:
        return self.stack.sessions

    @property
    def episodes(self) -> EpisodesFake:
        provider = self.stack.registry.providers[measurement_ports.EpisodeFacts][0]
        value = provider(None)  # type: ignore[arg-type]
        assert isinstance(value, EpisodesFake)
        return value


def _const(value: object) -> Callable[[object], object]:
    return lambda _resolver: value


def start_pulse(
    stack: Stack, token: str = "mara", body: dict[str, object] | None = None, *, tag: str = ""
) -> str:
    """Proposed, submitted, accepted and started by the subject (self operator), as E05's scenario does."""
    created = stack.hub.post(
        "/v1/measurement-requests",
        json=body or pulse_body(),
        headers=headers(token, f"idem-key-{token}{tag}-create"),
    )
    assert created.status_code == 201, created.json()
    request_id = str(created.json()["request_id"])
    holder = _Client(stack.hub)
    act(holder, token, request_id, "submit", 1, f"idem-key-{token}{tag}-submit")  # type: ignore[arg-type]
    act(holder, token, request_id, "accept", 2, f"idem-key-{token}{tag}-accept")  # type: ignore[arg-type]
    act(holder, token, request_id, "start", 3, f"idem-key-{token}{tag}-start")  # type: ignore[arg-type]
    return request_id


@dataclass
class _Client:
    client: TestClient


def presenter_body(code: str = "pulse") -> dict[str, object]:
    body = dict(pulse_body())
    body.pop("episode_id")
    body["capability_code"] = code
    return body


# ------------------------------------------------------------------------------------------------ scenarios


@pytest.mark.trace("D08", "V05", "E06-01")
def test_d08_too_much_movement_is_a_failed_attempt_and_measuring_again_is_an_explicit_new_request(
    tmp_path: Path,
) -> None:
    moving = Scene(pulse_hz=1.6, breath_hz=0.25, head_motion=0.3)
    stack = build(tmp_path, {"camera_1": moving, "camera_2": moving}, pairs=MARA_BOTH)
    stack.tick()  # probe: the worker is ready, so E05 can assign the camera
    first = start_pulse(stack)
    assert stack.tick() == 1
    stack.work()

    failed = stack.request(first)
    (attempt,) = failed["attempts"]
    assert (attempt["outcome"], attempt["failure_reason"]) == ("failed", QUALITY.reasons["too_much_movement"])
    assert stack.observations.drafts == []
    assert stack.tick() == 0  # the camera never starts another capture by itself (m9, PLAN line 56)
    assert stack.service.run_next() is False

    act(_Client(stack.hub), "mara", first, "cancel", 5, "idem-key-mara-cancel")  # type: ignore[arg-type]
    stack.source.set_scene("camera_1", STEADY)
    stack.source.set_scene("camera_2", STEADY)
    stack.tick()
    again = start_pulse(stack, tag="-again")  # the person asks again, explicitly
    assert stack.tick() == 1
    stack.work()

    done = stack.request(again)
    assert done["state"] == "completed"
    assert [a["outcome"] for a in done["attempts"]] == ["success"]
    selected = stack.observations.drafts[0]
    assert (selected.origin_mode, selected.acquisition_method, selected.patient_id) == (
        "SIM",
        "camera_estimate",
        "mara",
    )
    assert selected.quantity.unit.code == "/min"
    assert selected.quantity.value == Decimal(96)
    assert selected.context["camera_selection"] == "selected"
    assert stack.tick() == 0  # nothing more to send


@pytest.mark.trace("D09", "V07", "V47", "R11", "E06-02")
def test_d09_a_presenter_reading_goes_to_the_presenter_with_both_views_and_mara_is_unchanged(
    tmp_path: Path,
) -> None:
    """The presenter is measured on the cameras paired with the presenter; Mara's record is never touched. The
    hermetic run is SIM (the twin and the SIM source declare SIM); the LIVE origin needs a real camera,
    run by a person through the worker's hardware test."""
    scenes = {"camera_1": STEADY, "camera_2": Scene(pulse_hz=1.7, breath_hz=0.25, noise=0.01)}
    names = {"camera_1": "Galley camera", "camera_2": "Bunk camera"}
    pairs = {"camera_1": "presenter", "camera_2": "presenter"}
    stack = build(tmp_path, scenes, camera_names=names, pairs=pairs)
    stack.tick()
    request_id = start_pulse(stack, "presenter", presenter_body())
    stack.tick()
    stack.work()

    done = stack.request(request_id, "presenter")
    assert done["state"] == "completed"
    drafts = stack.observations.drafts
    assert len(drafts) == 2  # both qualified views kept, never averaged
    selected, other = drafts
    assert {d.patient_id for d in drafts} == {"presenter"}  # the demo operator's record, never Mara's
    assert {d.origin_mode for d in drafts} == {"SIM"}
    assert selected.context["camera_selection"] == "selected"
    assert other.context["camera_selection"] == "also_qualified"
    assert selected.context["camera_name"] in names.values()
    assert selected.device_id != other.device_id
    assert {d.context["views_disagree"] for d in drafts} == {"true"}
    assert selected.quantity.value != other.quantity.value
    notes = done["attempts"][0]["quality_notes"]
    assert any(n.startswith(selected.context["camera_name"]) for n in notes)
    assert done["result_observation_ids"]


@pytest.mark.trace("E06-02", "R29")
def test_a_camera_paired_with_someone_else_is_never_used_for_the_subject(tmp_path: Path) -> None:
    scenes = {"camera_1": STEADY, "camera_2": Scene(pulse_hz=1.2, breath_hz=0.25)}
    stack = build(tmp_path, scenes, pairs={"camera_1": "mara", "camera_2": "presenter"})
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    stack.work()
    assert stack.request(request_id)["state"] == "completed"
    (draft,) = stack.observations.drafts
    assert (draft.device_id, draft.patient_id, draft.quantity.value) == ("camera_1", "mara", Decimal(96))


@pytest.mark.trace("V05", "R09", "E06-01")
def test_v05_breathing_fails_with_its_own_reason_once_while_pulse_passes(tmp_path: Path) -> None:
    no_shoulders = Scene(pulse_hz=1.6, breath_hz=0.25, shoulders=False)
    pairs = {"camera_1": "presenter", "camera_2": "mara"}
    stack = build(tmp_path, {"camera_1": no_shoulders, "camera_2": no_shoulders}, pairs=pairs)
    stack.tick()
    breathing = start_pulse(stack, "presenter", presenter_body("respiratory_rate"))
    stack.tick()
    stack.work()
    stack.tick()  # the loop keeps the probe fresh after the capture moved the clock
    pulse = start_pulse(stack, "mara")
    for _ in range(3):
        stack.tick()
        stack.work()
    attempts = stack.request(breathing, "presenter")["attempts"]
    assert [(a["outcome"], a["failure_reason"]) for a in attempts] == [
        ("failed", QUALITY.reasons["shoulders_not_visible"])
    ]  # one attempt: measuring again is the person's explicit choice
    assert stack.request(pulse)["state"] == "completed"
    assert [d.code for d in stack.observations.drafts] == ["pulse"]


@pytest.mark.trace("V06", "E06-01", "AGENTS-1.5")
def test_v06_a_pulse_outside_the_analysis_range_is_not_measured_never_clipped(tmp_path: Path) -> None:
    fast = Scene(pulse_hz=3.4, breath_hz=0.25)
    stack = build(tmp_path, {"camera_1": fast})
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    stack.work()
    (attempt,) = stack.request(request_id)["attempts"]
    assert (attempt["outcome"], attempt["failure_reason"]) == (
        "failed",
        QUALITY.reasons["outside_analysis_range"],
    )
    assert stack.observations.drafts == []


@pytest.mark.trace("V08", "R29", "E06-02")
def test_v08_a_second_face_in_view_assigns_nothing_to_the_patient(tmp_path: Path) -> None:
    crowded = Scene(pulse_hz=1.6, breath_hz=0.25, faces=2)
    stack = build(tmp_path, {"camera_1": crowded, "camera_2": crowded}, pairs=MARA_BOTH)
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    stack.work()
    (attempt,) = stack.request(request_id)["attempts"]
    assert (attempt["outcome"], attempt["failure_reason"]) == ("failed", QUALITY.reasons["ambiguous_subject"])
    assert stack.observations.drafts == []


def _refused_start(stack: Stack) -> tuple[str, Any]:
    created = stack.hub.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-m-create")
    )
    request_id = str(created.json()["request_id"])
    holder = _Client(stack.hub)
    act(holder, "mara", request_id, "submit", 1, "idem-key-m-submit")  # type: ignore[arg-type]
    act(holder, "mara", request_id, "accept", 2, "idem-key-m-accept")  # type: ignore[arg-type]
    refused = stack.hub.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": "start"},
        headers=headers("mara", "idem-key-m-start", If_Match='"3"'),
    )
    return request_id, refused


@pytest.mark.trace("V09", "V47", "AGENTS-1.2")
def test_v09_a_sim_camera_is_never_assigned_to_a_live_mission_and_start_fails_closed(tmp_path: Path) -> None:
    stack = build(tmp_path, {"camera_1": STEADY}, mode="LIVE")
    stack.tick()
    request_id, refused = _refused_start(stack)
    assert refused.status_code >= 400
    assert "adapter_unassigned" in refused.text
    assert (
        stack.request(request_id)["state"] == "accepted"
    )  # never in progress without a camera that can serve it
    assert stack.uows.count("camera_dispatch") == 0
    assert stack.tick() == 0
    assert "/v1/acquisitions" not in stack.transport.paths
    assert stack.observations.drafts == []


@pytest.mark.trace("V11", "AGENTS-1.3", "AGENTS-6.safety")
def test_v11_without_the_worker_readiness_is_unknown_and_start_fails_closed(tmp_path: Path) -> None:
    stack = build(tmp_path, {"camera_1": STEADY})
    stack.transport.up = False
    stack.tick()
    capabilities = stack.hub.get("/v1/capabilities", headers=headers("mara")).json()
    pulse = next(c for c in _items(capabilities) if c["code"] == "pulse")
    assert pulse["status"] == "available"  # the manual path stays: the camera is optional
    request_id, refused = _refused_start(stack)
    assert refused.status_code >= 400
    assert "adapter_unassigned" in refused.text
    assert stack.request(request_id)["state"] == "accepted"
    assert stack.uows.count("camera_dispatch") == 0
    assert stack.observations.drafts == []


@pytest.mark.trace("V13", "E06-01")
def test_v13_cancelling_the_request_stops_the_acquisition_and_nothing_is_recorded(tmp_path: Path) -> None:
    stack = build(tmp_path, {"camera_1": STEADY})
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    act(_Client(stack.hub), "mara", request_id, "cancel", 4, "idem-key-m-cancel")  # type: ignore[arg-type]
    stack.tick()
    acquisition = _acquisition(stack, request_id, 1)
    assert stack.transport.paths[-1] == f"/v1/acquisitions/{acquisition}/cancel"
    stack.work()
    request = stack.request(request_id)
    assert request["state"] == "cancelled"
    assert request["attempts"] == []
    assert stack.observations.drafts == []


@pytest.mark.trace("V16", "V35")
def test_v16_a_lost_start_reply_is_retried_after_the_clocks_moved_and_measured_once(tmp_path: Path) -> None:
    stack = build(tmp_path, {"camera_1": STEADY})
    stack.tick()
    request_id = start_pulse(stack)
    stack.transport.drop_next = 1
    stack.tick()  # the worker accepted the start; its reply was lost, so E01's queue keeps the task
    stack.clock.at += timedelta(seconds=3)
    stack.worker_clock.advance(4.5)  # the probed offset changes before the retry (M1)
    stack.tick()  # the same acquisition identity again: the worker answers with the same receipt
    starts = [p for p in stack.transport.paths if p == "/v1/acquisitions"]
    assert len(starts) == 2
    stack.work()
    assert stack.service.run_next() is False  # one acquisition only
    request = stack.request(request_id)
    assert request["state"] == "completed"
    assert len(request["attempts"]) == 1


@pytest.mark.trace("E06-01", "AGENTS-1.3")
def test_a_worker_restart_that_lost_the_acquisition_is_not_measured_with_its_reason(tmp_path: Path) -> None:
    stack = build(tmp_path, {"camera_1": STEADY})
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    stack.restart_worker()  # nothing was captured yet, so nothing was saved: the acquisition is gone
    stack.tick()  # reconciliation asks the worker, hears "not found" and reports through E05
    stack.deliver_events()
    request = stack.request(request_id)
    (attempt,) = request["attempts"]
    assert (attempt["outcome"], attempt["failure_reason"]) == ("failed", "camera_lost")
    assert stack.observations.drafts == []
    assert stack.tick() == 0  # reported once


@pytest.mark.trace("E06-01", "AGENTS-6")
def test_an_attempt_decided_during_a_hub_outage_survives_a_worker_restart_and_is_recorded_once(
    tmp_path: Path,
) -> None:
    stack = build(tmp_path, {"camera_1": STEADY})
    stack.tick()
    request_id = start_pulse(stack)
    stack.tick()
    stack.late.down = True
    stack.work()
    assert stack.request(request_id)["attempts"] == []
    assert len(stack.evidence.saved) == 1  # durable before the first delivery
    stack.restart_worker()
    stack.late.down = False
    stack.worker_clock.advance(60)
    assert stack.service.deliver_pending() == 1
    stack.deliver_events()
    request = stack.request(request_id)
    assert request["state"] == "completed"
    assert len(request["attempts"]) == 1
    assert len(stack.observations.drafts) == 1
    assert stack.evidence.saved == {}
    assert stack.tick() == 0


# ------------------------------------------------------------------------------------------------ hardware

TWIN = _ROOT / "simulators" / "camera"
WORKER = _ROOT / "workers" / "camera"
INJECTED_PULSE = Decimal(72)  # the twin's synthetic scene (simulators/camera scenes.py)


def _wanted(config: pytest.Config) -> bool:
    expression = str(config.getoption("markexpr") or "")
    return "hardware" in expression and "not hardware" not in expression


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise RuntimeError("uv not on PATH: the twin and the worker run in their own uv projects")
    return found


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def twin(request: pytest.FixtureRequest) -> Iterator[str]:
    if not _wanted(request.config):
        pytest.skip("hardware: run with -m hardware (camera twin, MediaPipe, real time)")
    subprocess.run([_uv(), "run", "--project", str(TWIN), "camera-twin-fetch"], check=True, timeout=900)
    process = subprocess.Popen(
        [_uv(), "run", "--project", str(TWIN), "camera-twin", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )  # fmt: skip
    ready: list[str] = []
    found = threading.Event()

    def watch() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if "listening on" in line:
                ready.append(line.rsplit(" ", 1)[-1].strip())
                found.set()

    threading.Thread(target=watch, daemon=True).start()
    try:
        if not found.wait(120):
            raise RuntimeError("camera twin did not start")
        yield ready[0]
    finally:
        process.terminate()
        process.wait(10)


@pytest.mark.hardware
@pytest.mark.trace("E06-01", "AGENTS-4")
def test_the_twin_stream_is_measured_end_to_end(tmp_path: Path, twin: str) -> None:
    """Twin MJPEG stream, the worker process with MediaPipe, the hub over HTTP, E05 recordAttempt: the
    synthetic pulse is recorded as a SIM camera estimate within the gate tolerance of the injected rate."""
    subprocess.run(
        [_uv(), "run", "--project", str(WORKER), "--extra", "capture", "crew-health-camera-models"],
        check=True, timeout=900,
    )  # fmt: skip
    hub_port, worker_port = _free_port(), _free_port()
    transport = HttpWorkerTransport(
        f"http://127.0.0.1:{worker_port}", token=WORKER_TOKEN, max_reply_bytes=1_048_576
    )
    stack = build(tmp_path, {"camera_1": STEADY}, worker=transport)
    stack.clock.at = datetime.now(UTC)
    import uvicorn  # noqa: PLC0415 (only the hardware run serves the hub over HTTP)

    server = uvicorn.Server(uvicorn.Config(stack.app, host="127.0.0.1", port=hub_port, log_level="warning"))
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    (tmp_path / "worker.token").write_text(WORKER_TOKEN, encoding="utf-8")
    (tmp_path / "adapter.token").write_text("camera", encoding="utf-8")
    deployment = {
        "host": "127.0.0.1",
        "port": worker_port,
        "hub_url": f"http://127.0.0.1:{hub_port}",
        "adapter_id": "adp_camera",
        "worker_token_file": str(tmp_path / "worker.token"),
        "adapter_token_file": str(tmp_path / "adapter.token"),
        "state_dir": str(tmp_path / "state"),
        "cameras": [
            {"device_id": "camera_1", "name": "Twin", "kind": "station", "subject_id": "mara",
             "stream": f"{twin}/cameras/synthetic/stream.mjpg"},
        ],
    }  # fmt: skip
    (tmp_path / "deployment.json").write_text(json.dumps(deployment), encoding="utf-8")
    worker = subprocess.Popen(
        [_uv(), "run", "--project", str(WORKER), "--extra", "capture", "crew-health-camera"],
        env={**os.environ, "CREW_HEALTH_CAMERA_DEPLOYMENT": str(tmp_path / "deployment.json")},
    )
    try:
        _until(lambda: _healthy(transport), seconds=120)
        stack.clock.at = datetime.now(UTC)
        stack.tick()
        request_id = start_pulse(stack)

        def settled() -> bool:
            stack.clock.at = datetime.now(UTC)
            stack.tick()
            return bool(stack.request(request_id)["attempts"])

        _until(settled, seconds=240)
    finally:
        worker.terminate()
        worker.wait(15)
        server.should_exit = True
        serving.join(15)
    request = stack.request(request_id)
    (attempt,) = request["attempts"]
    assert (request["state"], attempt["outcome"]) == ("completed", "success"), attempt
    selected = stack.observations.drafts[0]
    assert (selected.origin_mode, selected.patient_id, selected.device_id) == ("SIM", "mara", "camera_1")
    assert abs(selected.quantity.value - INJECTED_PULSE) <= QUALITY.agreement_tolerance_per_min


def _healthy(transport: HttpWorkerTransport) -> bool:
    try:
        return transport.call("GET", "/health", None, timeout_s=2.0).status == 200
    except WorkerUnreachable:
        return False


def _until(check: Callable[[], bool], *, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError(f"not reached within {seconds} s")
        time.sleep(0.5)


def _acquisition(stack: Stack, request_id: str, sequence: int) -> str:
    ids = stack.registry.providers[IdFactory][0](None)  # type: ignore[arg-type]
    value = ids.derived("cad", request_id, str(sequence))  # type: ignore[attr-defined]
    assert isinstance(value, str)
    return value


def _items(body: object) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    items = body.get("items") or body.get("capabilities") or []
    assert isinstance(items, list)
    return items
