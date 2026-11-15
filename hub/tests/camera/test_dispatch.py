"""Camera readiness, assignment, dispatch through E01 tasks and reconciliation on a real SQLite store (A7; B1, M1, M2,
M4, M6, m7, m9; V09, V11, V13, V16)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from camera_harness import (
    ADAPTER,
    START,
    Mission,
    Reports,
    ScriptedTransport,
    Store,
    attempt_row,
    camera_store,
    changed,
    describe,
    kernel_settings,
    memory_tasks,
    reply,
    status,
)

from crew_health.camera.adapters.repository import SqliteDispatchStore
from crew_health.camera.application.assignments import CameraAssignments
from crew_health.camera.application.dispatch import Dispatcher, DispatchLimits, Intent
from crew_health.camera.application.link import WorkerLink
from crew_health.camera.application.ports import WorkerReply, WorkerUnreachable
from crew_health.camera.application.tasks import CameraTasks
from crew_health.kernel.tasks import TaskRegistration
from crew_health.kernel.uow import Actor

pytestmark = pytest.mark.trace("A7", "R29", "V11", "V13", "V16", "AGENTS-6.bounds")

MARA = Actor("person", "mara")
LIMITS = DispatchLimits(
    batch=8,
    timeout_ms=2000,
    acquisition_ttl_ms=120_000,
    dispatch_deadline_ms=30_000,
    max_per_request=8,
    warmup_max_ms=5_000,
)


@dataclass
class Worker:
    """The camera worker as the hub sees it over protocol v1, route by route."""

    probe: WorkerReply = field(default_factory=describe)
    starts: list[WorkerReply | Exception] = field(default_factory=list)
    states: dict[str, WorkerReply] = field(default_factory=dict)
    up: bool = True
    lose_next_reply: int = 0
    started: set[str] = field(default_factory=set)

    def __call__(self, method: str, path: str, body: bytes | None) -> WorkerReply:
        if not self.up:
            raise WorkerUnreachable("connection refused")
        answer = self._answer(path)
        if (
            path == "/v1/acquisitions"
            and body is not None
            and isinstance(answer, WorkerReply)
            and answer.status < 300
        ):
            self.started.add(str(json.loads(body)["acquisition_id"]))
        if self.lose_next_reply and path == "/v1/acquisitions":
            self.lose_next_reply -= 1
            raise WorkerUnreachable("reply lost")
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _answer(self, path: str) -> WorkerReply | Exception:
        if path == "/v1/describe":
            return self.probe
        if path == "/v1/acquisitions":
            return self.starts.pop(0) if self.starts else reply(202, {"state": "accepted"})
        acquisition = path.split("/")[3]
        if path.endswith("/cancel"):
            return reply(200, {"acquisition_id": acquisition, "result": "stopped"})
        if acquisition in self.states:
            return self.states[acquisition]
        if acquisition in self.started:
            return status(acquisition, "accepted")
        return reply(404, {"code": "NOT_FOUND", "detail": "acquisition"})


class Rig:
    def __init__(
        self, tmp_path: Path, *, mission: str | None = "SIM", limits: DispatchLimits = LIMITS
    ) -> None:
        self.store: Store = camera_store(tmp_path)
        self.worker = Worker()
        self.transport = ScriptedTransport(fallback=self.worker)
        self.link = WorkerLink(self.transport, self.store.clock, timeout_ms=2000, stale_ms=5000)
        self.rows = SqliteDispatchStore()
        self.mission = Mission(mission)  # type: ignore[arg-type]
        self.reports = Reports()
        self.queue = memory_tasks(kernel_settings(), [])
        self.assignments = CameraAssignments(
            self.link, self.rows, adapter_id=ADAPTER, mission=self.mission, usable=True
        )
        common = {"store": self.rows, "link": self.link, "queue": self.queue, "uows": self.store.uows}
        common |= {
            "snapshots": self.store.uows,
            "ids": self.store.ids,
            "clock": self.store.clock,
            "limits": limits,
        }
        self.dispatcher = Dispatcher(mission=self.mission, **common)  # type: ignore[arg-type]
        self.tasks = CameraTasks(runner=self.queue, reports=self.reports, **common)  # type: ignore[arg-type]
        self.queue.registrations = {
            (name, "1"): TaskRegistration(name, "1", handler, None, None)
            for name, handler in self.tasks.handlers.items()
        }

    def start(self, correlation_id: str = "cor_start") -> None:
        """E05's start: assign inside the command's unit of work, then its event is delivered."""
        self.link.probe()
        with self.store.uows.begin(actor=MARA, correlation_id=correlation_id) as uow:
            assignment = self.assignments.assign(uow, capability_code="pulse", methods=("camera_estimate",))
            assert assignment is not None
        self.deliver(changed(correlation_id))

    def deliver(self, event: object) -> None:
        with self.store.uows.begin(actor=Actor("engine", "events"), correlation_id="cor_delivery") as uow:
            self.dispatcher.on_changed(uow, event)  # type: ignore[arg-type]

    def tick(self) -> int:
        self.link.probe()
        self.dispatcher.reconcile()
        return self.tasks.drain()

    def intent(self, correlation_id: str = "cor_start") -> Intent:
        with self.store.uows.snapshot() as view:
            found = self.rows.get(view.connection, correlation_id)
        assert found is not None
        return found

    def sent(self, path_end: str = "/v1/acquisitions") -> list[dict[str, object]]:
        return [body or {} for _, path, body, _ in self.transport.calls if path.endswith(path_end)]

    def later(self, seconds: float) -> None:
        self.store.clock.at += timedelta(seconds=seconds)


# ------------------------------------------------------------------------------------------- assignment


def test_assignment_names_the_trusted_adapter_and_leaves_the_device_to_best_view_selection(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.link.probe()
    with rig.store.uows.snapshot() as view:
        assert rig.assignments.ready_methods(view) == frozenset({"camera_estimate"})
        assignment = rig.assignments.assign(
            view, capability_code="pulse", methods=("manual_instrument", "camera_estimate")
        )
    assert assignment is not None
    assert (assignment.adapter_id, assignment.method, assignment.device_id) == (
        ADAPTER,
        "camera_estimate",
        None,
    )


@pytest.mark.parametrize(
    ("probe", "code", "methods"),
    [
        (None, "pulse", ("camera_estimate",)),
        (describe(adapter_id="adp_rogue"), "pulse", ("camera_estimate",)),
        (describe(methods=()), "pulse", ("camera_estimate",)),
        (describe(), "blood_pressure", ("camera_estimate",)),
        (describe(), "pulse", ("manual_instrument",)),
    ],
)
def test_no_assignment_without_a_fresh_trusted_ready_worker_for_that_capability(
    tmp_path: Path, probe: WorkerReply | None, code: str, methods: tuple[str, ...]
) -> None:
    rig = Rig(tmp_path)
    rig.worker.up = probe is not None
    rig.worker.probe = probe or describe()
    rig.link.probe()
    with rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        assert rig.assignments.assign(uow, capability_code=code, methods=methods) is None


@pytest.mark.trace("V09", "M4", "AGENTS-1.2")
@pytest.mark.parametrize(("worker", "mission"), [("SIM", "LIVE"), ("LIVE", "SIM"), ("SIM", None)])
def test_a_worker_of_another_origin_than_the_mission_is_never_assigned(
    tmp_path: Path, worker: str, mission: str | None
) -> None:
    rig = Rig(tmp_path, mission=mission)
    rig.worker.probe = describe(origin=worker)
    rig.link.probe()
    with rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        assert rig.assignments.ready_methods(uow) is None
        assert rig.assignments.assign(uow, capability_code="pulse", methods=("camera_estimate",)) is None
    assert rig.transport.calls[-1][1] == "/v1/describe"  # nothing was ever sent to the worker


@pytest.mark.trace("M6")
def test_without_e01_tasks_or_the_e05_report_the_camera_is_never_assigned(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    closed = CameraAssignments(rig.link, rig.rows, adapter_id=ADAPTER, mission=rig.mission, usable=False)
    rig.link.probe()
    with rig.store.uows.snapshot() as view:
        assert closed.ready_methods(view) is None
        assert closed.assign(view, capability_code="pulse", methods=("camera_estimate",)) is None


def test_unknown_readiness_when_the_worker_is_absent_stale_or_untrusted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.store.uows.snapshot() as view:
        assert rig.assignments.ready_methods(view) is None  # never probed
    rig.worker.probe = describe(adapter_id="adp_rogue")
    rig.link.probe()
    with rig.store.uows.snapshot() as view:
        assert rig.assignments.ready_methods(view) is None  # wrong adapter
    rig.worker.probe = describe()
    rig.link.probe()
    rig.later(6)
    with rig.store.uows.snapshot() as view:
        assert rig.assignments.ready_methods(view) is None  # stale


def test_the_intent_commits_or_rolls_back_with_the_start(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.link.probe()
    with pytest.raises(RuntimeError), rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        rig.assignments.assign(uow, capability_code="pulse", methods=("camera_estimate",))
        raise RuntimeError("start failed after assignment")
    with rig.store.uows.snapshot() as view:
        assert rig.rows.get(view.connection, "cor_start") is None


# ------------------------------------------------------------------------------------------- binding and pairing


@pytest.mark.trace("B1", "R29", "V08")
def test_a_start_names_only_the_cameras_paired_with_the_subject_and_the_origin_it_requires(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.worker.probe = describe(cameras=1)
    rig.start()
    bound = rig.intent()
    assert (bound.state, bound.device_ids, bound.required_origin, bound.sequence) == (
        "ready",
        ("camera_1",),
        "SIM",
        1,
    )
    assert rig.tick() == 1
    (body,) = rig.sent()
    assert (body["device_ids"], body["required_origin"], body["patient_id"]) == (["camera_1"], "SIM", "mara")
    assert rig.intent().state == "dispatched"


@pytest.mark.trace("B1", "R29", "V08", "M2")
@pytest.mark.parametrize(
    ("probe", "mission", "reason"),
    [
        (describe(subject="nadia"), "SIM", "camera_not_paired"),
        (describe(subject=None), "SIM", "camera_not_paired"),
        (describe(origin="LIVE"), "SIM", "camera_origin_mismatch"),
    ],
)
def test_a_start_the_cameras_cannot_serve_is_not_measured_with_its_reason_and_never_sent(
    tmp_path: Path, probe: WorkerReply, mission: str, reason: str
) -> None:
    rig = Rig(tmp_path, mission=mission)
    rig.link.probe()
    with rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        rig.rows.record(
            uow.connection,
            correlation_id="cor_start",
            capability_code="pulse",
            adapter_id=ADAPTER,
            at=uow.now,
        )
    rig.worker.probe = probe  # the cameras changed between the assignment and the bind
    rig.link.probe()
    rig.deliver(changed("cor_start"))
    rig.tick()
    assert rig.sent() == []
    assert rig.reports.calls == [("mrq_1", 1, reason, f"{rig.intent().acquisition_id}.hub")]
    assert rig.intent().state == "reported"


def test_the_start_event_binds_the_intent_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    first = rig.intent()
    rig.deliver(changed("cor_start"))  # a duplicate delivery
    assert rig.intent() == first
    assert rig.tick() == 1 and rig.tick() == 0


@pytest.mark.parametrize(
    "event",
    [
        changed("cor_start", state="cancelled"),
        changed("cor_start", patient=None),
        changed("cor_start", raw=b"not json"),
        changed("cor_start", attempts=({"sequence": "x"},)),
    ],
)
def test_an_unusable_start_event_drops_the_intent(tmp_path: Path, event: object) -> None:
    rig = Rig(tmp_path)
    rig.link.probe()
    with rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        rig.assignments.assign(uow, capability_code="pulse", methods=("camera_estimate",))
    rig.deliver(event)
    assert rig.intent().state == "dropped"
    assert rig.tick() == 0 and rig.reports.calls == []


# ------------------------------------------------------------------------------------------- dispatch through E01 tasks


def test_dispatch_sends_the_probed_clock_offset_and_a_lifetime_the_worker_accepts(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.worker.probe = describe(at=START + timedelta(seconds=2), max_ttl_ms=60_000)
    rig.start()
    rig.tick()
    (body,) = rig.sent()
    assert body["clock_offset_ms"] == 2000 and body["ttl_ms"] == 60_000  # m7: never above the worker's max
    assert body["warmup_max_ms"] == LIMITS.warmup_max_ms  # the camera settles its exposure first
    assert body["acquisition_id"] == rig.intent().acquisition_id
    deadline = rig.intent().deadline
    assert deadline is not None and deadline == START + timedelta(milliseconds=60_000 + 30_000)


@pytest.mark.trace("M1", "V16")
def test_a_lost_reply_is_retried_by_the_task_queue_with_the_same_identity_after_the_clocks_moved(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.worker.lose_next_reply = 1
    rig.tick()
    assert rig.intent().state == "ready"
    rig.later(0.003)
    rig.worker.probe = describe(at=START + timedelta(seconds=1))  # a fresh probe with a new offset
    rig.tick()
    first, second = rig.sent()
    assert first["acquisition_id"] == second["acquisition_id"]
    assert first["clock_offset_ms"] != second["clock_offset_ms"]  # the worker leaves it out of the identity
    assert rig.intent().state == "dispatched"


@pytest.mark.trace("M2", "M6")
def test_a_refusal_is_not_measured_with_its_code_and_busy_is_retried(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.worker.starts = [reply(429, {"code": "BUSY", "detail": "max_acquisitions"})]
    rig.worker.starts.append(reply(409, {"code": "NO_CAMERA", "detail": "camera_2"}))
    rig.start()
    rig.tick()
    assert rig.intent().state == "ready"
    rig.tick()  # refused for good: reported in the same pass's queue
    rig.tick()
    assert rig.reports.calls[0][2] == "camera_refused_no_camera"
    assert rig.intent().state == "reported"


@pytest.mark.trace("M2", "M6", "V11")
def test_a_start_never_answered_is_not_measured_at_its_deadline(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.worker.up = False
    for _ in range(6):  # E01 retries until tasks.max_attempts, then the queue gives up
        rig.tick()
    assert rig.intent().state == "ready" and rig.reports.calls == []
    rig.later(31)
    rig.tick()
    rig.tick()
    assert rig.reports.calls[0][2] == "camera_unavailable"
    assert rig.intent().state == "reported"


# ------------------------------------------------------------------------------------------- reconciliation


@pytest.mark.trace("M2")
@pytest.mark.parametrize(
    ("worker_state", "reason"),
    [
        ("expired", "camera_expired"),
        ("cancelled", "camera_cancelled"),
        ("rejected", "camera_attempt_rejected"),
    ],
)
def test_an_acquisition_the_worker_ended_without_an_attempt_is_not_measured_with_its_reason(
    tmp_path: Path, worker_state: str, reason: str
) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    acquisition = rig.intent().acquisition_id
    assert acquisition is not None
    rig.worker.states[acquisition] = status(acquisition, worker_state)
    rig.tick()
    rig.tick()
    assert [c[2] for c in rig.reports.calls] == [reason]
    assert rig.intent().state == "reported"


@pytest.mark.trace("M2", "AGENTS-2.contain")
def test_a_worker_restart_that_lost_the_acquisition_is_not_measured_never_in_progress_forever(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()  # dispatched
    rig.worker.started.clear()  # the worker restarts with an empty table: 404 on status
    rig.tick()
    rig.tick()
    assert [c[2] for c in rig.reports.calls] == ["camera_lost"]


@pytest.mark.trace("M2")
def test_an_unreachable_worker_is_reported_only_after_the_acquisition_deadline(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rig.worker.up = False
    rig.later(100)
    rig.tick()
    assert rig.reports.calls == [] and rig.intent().state == "dispatched"
    rig.later(60)
    rig.tick()
    rig.tick()
    assert [c[2] for c in rig.reports.calls] == ["camera_unreachable"]


@pytest.mark.trace("M2")
def test_a_capture_in_progress_or_delivering_is_left_to_the_worker(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    acquisition = rig.intent().acquisition_id
    assert acquisition is not None
    for state in ("accepted", "capturing", "delivering"):
        rig.worker.states[acquisition] = status(acquisition, state)
        rig.later(500)  # delivering never gives up on the worker, past any deadline
        rig.tick()
        assert rig.intent().state == "dispatched" and rig.reports.calls == []
    rig.worker.states[acquisition] = status(acquisition, "delivered")
    rig.tick()
    assert rig.intent().state == "done" and rig.reports.calls == []


@pytest.mark.trace("M2")
def test_the_attempt_the_worker_recorded_closes_the_intent(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rig.deliver(changed("cor_attempt", attempts=(attempt_row(1, "failed"),)))
    assert rig.intent().state == "done"
    rig.later(1000)
    rig.tick()
    assert rig.reports.calls == []


@pytest.mark.trace("m9", "D08")
def test_a_failed_camera_attempt_never_starts_another_capture_by_itself(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rig.deliver(changed("cor_attempt", attempts=(attempt_row(1, "failed"),)))
    assert rig.tick() == 0
    assert len(rig.sent()) == 1


@pytest.mark.trace("M2", "M6")
def test_an_unreachable_e05_report_is_retried_and_reported_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.worker.probe = describe(subject="nadia")
    rig.reports.answers = ["unreachable", "recorded"]
    rig.link.probe()
    with rig.store.uows.begin(actor=MARA, correlation_id="cor_start") as uow:
        rig.rows.record(
            uow.connection,
            correlation_id="cor_start",
            capability_code="pulse",
            adapter_id=ADAPTER,
            at=uow.now,
        )
    rig.deliver(changed("cor_start"))
    rig.tick()
    rig.tick()
    rig.tick()
    assert [c[3] for c in rig.reports.calls] == [f"{rig.intent().acquisition_id}.hub"] * 2
    assert rig.intent().state == "reported"


# ------------------------------------------------------------------------------------------- cancellation


def test_cancelling_the_request_cancels_a_dispatched_acquisition(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rig.deliver(changed("cor_cancel", state="cancelled"))
    assert rig.intent().state == "cancelling"
    rig.tick()
    assert rig.sent("/cancel") == [{}]
    assert (rig.intent().state, rig.intent().code) == ("cancelled", "stopped")


def test_cancelling_before_dispatch_never_sends_the_start(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.deliver(changed("cor_cancel", state="cancelled"))
    rig.tick()
    assert rig.sent() == [] and rig.intent().state == "dropped"


def test_completion_never_cancels_the_acquisition_that_completed_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rig.deliver(changed("cor_done", state="completed", attempts=(attempt_row(1, "success"),)))
    rig.tick()
    assert rig.sent("/cancel") == [] and rig.intent().state == "done"


def test_a_cancel_that_arrives_during_a_send_stops_what_the_worker_accepted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()

    def accept_then_cancel(method: str, path: str, body: bytes | None) -> WorkerReply:
        rig.deliver(changed("cor_cancel", state="cancelled"))
        return reply(202, {"state": "accepted"})

    rig.worker.starts = []
    rig.transport.steps = [describe(), accept_then_cancel]
    rig.tick()
    rig.tick()
    assert rig.intent().state == "dropped"
    assert len(rig.sent("/cancel")) == 1


def test_dispatch_rows_survive_a_restart(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    rig.tick()
    rows = (
        sqlite3.connect(tmp_path / "hub.db")
        .execute("SELECT state, device_ids, required_origin FROM camera_dispatch")
        .fetchall()
    )
    assert rows == [("dispatched", '["camera_1", "camera_2"]', "SIM")]
