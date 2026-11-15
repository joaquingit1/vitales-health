from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import command_identity
from crew_health.kernel.tasks import TaskInput, TaskLease, TaskRegistration, TaskResult
from crew_health.kernel.testing import MemoryClock, MemoryTaskHandler
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, SessionContext, UnitOfWork
from crew_health.platform.adapters.tasks import TaskRepository
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database


@dataclass
class Station:
    db: SQLiteDatabase
    service: TaskService
    task: TaskInput
    session: SessionContext
    clock: MemoryClock
    granted: list[bool]

    def claim(self) -> TaskLease:
        with self.db.begin(actor=self.session.actor, correlation_id="claim_one") as uow:
            self.service.submit(uow, self.task)
            (lease,) = self.service.claim(
                uow, worker_id="worker_one", handlers=frozenset({"assessment"}), limit=1
            )
            return lease


def station(path: Path, *, broken: bool = False) -> Station:
    clock = MemoryClock(datetime(2026, 11, 15, tzinfo=UTC), 0, "epoch_one")
    db = database(path, clock=clock)
    db.migrate("CREATE TABLE failure_result_probe(task_id TEXT PRIMARY KEY,result_ref TEXT NOT NULL) STRICT;")
    actor = Actor("person", "person_one")
    session = SessionContext("session_one", "crew", actor, actor.id, frozenset({Role.CREW}))
    granted = [True]

    def apply(uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        uow.connection.execute(
            "INSERT INTO failure_result_probe VALUES (?,?)", (lease.task.task_id, result.result_ref)
        )
        if broken:
            raise RuntimeError("apply failed after its write")

    handler = MemoryTaskHandler(lambda lease: result_for(lease), apply, lambda result: None)
    service = TaskService(
        settings=db.settings,
        repository=TaskRepository(),
        ids=db.ids,
        registrations=(TaskRegistration("assessment", "v1", handler, None, None),),
        authorize=lambda view, ctx, task: ctx.actor == task.actor,
        authorize_completion=lambda uow, task: granted[0],
        capture=lambda at: TimeInput(at, "source_one", "segment_one", 1, "source_raw", "SIM"),
        elapsed=lambda view, start, end: end.original_at - start.original_at,
    )
    command = command_identity(
        key="command_one",
        actor_ref=actor.id,
        operation="assessment",
        target="episode_one",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    task = TaskInput(
        "task_one",
        "assessment",
        "v1",
        "input_one",
        "0" * 64,
        "release_1",
        None,
        actor,
        Audience("owner", actor.id, AudienceProjectionKey("test", "task", 1), "1"),
        command,
        None,
    )
    return Station(db, service, task, session, clock, granted)


def result_for(lease: TaskLease) -> TaskResult:
    return TaskResult(lease.task.task_id, lease.generation, "assessment_one", "a" * 64)


@pytest.mark.trace("SYSTEM.5.10.1", "R17")
def test_failure_result_is_atomic_terminal_and_not_success(tmp_path: Path) -> None:
    s = station(tmp_path)
    lease = s.claim()
    with s.db.begin(actor=s.session.actor, correlation_id="failed_one") as uow:
        assert s.service.fail_with_result(uow, lease, result_for(lease), code=ErrorCode.MODEL_UNAVAILABLE)
        assert not s.service.fail_with_result(uow, lease, result_for(lease), code=ErrorCode.MODEL_UNAVAILABLE)
    with s.db.snapshot() as view:
        row = s.service.repository.get(view, s.task.task_id)
        assert row is not None and row.status.state == "failed"
        assert row.status.result_ref == "assessment_one"
        assert row.status.error_code == ErrorCode.MODEL_UNAVAILABLE
        assert row.status.progress.value == 0
        assert view.connection.rows("SELECT result_ref FROM failure_result_probe", (), limit=2) == (
            ("assessment_one",),
        )


@pytest.mark.trace("SYSTEM.5.10.1", "R17")
@pytest.mark.parametrize("invalid", ["cancelled", "expired", "revoked", "wrong_task", "wrong_generation"])
def test_failure_result_never_applies_after_a_fence(tmp_path: Path, invalid: str) -> None:
    s = station(tmp_path)
    lease = s.claim()
    result = result_for(lease)
    if invalid == "expired":
        s.clock.at += timedelta(seconds=2)
    elif invalid == "revoked":
        s.granted[0] = False
    elif invalid == "wrong_task":
        result = replace(result, task_id="task_other")
    elif invalid == "wrong_generation":
        result = replace(result, generation=result.generation + 1)
    with s.db.begin(actor=s.session.actor, correlation_id="fenced_one") as uow:
        if invalid == "cancelled":
            s.service.cancel(uow, s.session, s.task.task_id)
        assert not s.service.fail_with_result(uow, lease, result, code=ErrorCode.MODEL_UNAVAILABLE)
    with s.db.snapshot() as view:
        assert not view.connection.rows("SELECT task_id FROM failure_result_probe", (), limit=1)


@pytest.mark.trace("SYSTEM.5.10.1", "R25")
def test_failure_result_rolls_back_owner_effect_and_task_together(tmp_path: Path) -> None:
    s = station(tmp_path, broken=True)
    lease = s.claim()
    with pytest.raises(RuntimeError, match="apply failed"):
        with s.db.begin(actor=s.session.actor, correlation_id="rollback_one") as uow:
            s.service.fail_with_result(uow, lease, result_for(lease), code=ErrorCode.MODEL_UNAVAILABLE)
    with s.db.snapshot() as view:
        assert not view.connection.rows("SELECT task_id FROM failure_result_probe", (), limit=1)
        row = s.service.repository.get(view, s.task.task_id)
        assert row is not None and row.status.state == "running" and row.status.result_ref is None


@pytest.mark.trace("SYSTEM.5.10.1", "R17")
def test_fail_pending_does_not_take_a_running_lease_or_repeat_failure(tmp_path: Path) -> None:
    s = station(tmp_path)
    with s.db.begin(actor=s.session.actor, correlation_id="queue_one") as uow:
        s.service.submit(uow, s.task)
        assert s.service.fail_pending(uow, s.task.task_id, code=ErrorCode.MODEL_UNAVAILABLE)
        assert not s.service.fail_pending(uow, s.task.task_id, code=ErrorCode.MODEL_UNAVAILABLE)
    with s.db.snapshot() as view:
        row = s.service.repository.get(view, s.task.task_id)
        assert row is not None and row.status.state == "failed" and row.generation == 1
        assert row.status.result_ref is None
    other = station(tmp_path / "running")
    lease = other.claim()
    with other.db.begin(actor=other.session.actor, correlation_id="leave_running") as uow:
        assert not other.service.fail_pending(uow, lease.task.task_id, code=ErrorCode.MODEL_UNAVAILABLE)
        assert other.service.repository.get(uow, lease.task.task_id) is not None


@pytest.mark.trace("SYSTEM.5.10.1", "V26")
def test_fail_pending_rechecks_current_authorization(tmp_path: Path) -> None:
    s = station(tmp_path)
    with s.db.begin(actor=s.session.actor, correlation_id="queue_one") as uow:
        s.service.submit(uow, s.task)
    s.granted[0] = False
    with s.db.begin(actor=s.session.actor, correlation_id="revoked_one") as uow:
        assert not s.service.fail_pending(uow, s.task.task_id, code=ErrorCode.MODEL_UNAVAILABLE)
    with s.db.snapshot() as view:
        row = s.service.repository.get(view, s.task.task_id)
        assert row is not None and row.status.state == "cancelled"


@pytest.mark.trace("SYSTEM.5.10.1", "SYSTEM.5.2")
def test_missing_clock_mapping_never_claims_an_unrecoverable_lease(tmp_path: Path) -> None:
    s = station(tmp_path)
    known_capture = s.service.capture
    assert known_capture is not None
    s.service.capture = lambda at: replace(known_capture(at), source_clock_version=0)
    with s.db.begin(actor=s.session.actor, correlation_id="unmapped_claim") as uow:
        s.service.submit(uow, s.task)
        assert s.service.claim(uow, worker_id="worker_one", handlers=frozenset({"assessment"}), limit=1) == ()
    with s.db.snapshot() as view:
        row = s.service.repository.get(view, s.task.task_id)
        assert row is not None and row.status.state == "queued"
        assert row.attempts == 0 and row.generation == 0
    s.service.capture = known_capture
    lease = s.claim()
    assert lease.generation == 1
