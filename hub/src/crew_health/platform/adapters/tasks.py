from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.tasks import TaskInput, TaskLease, TaskResult, TaskStatus
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.adapters import codec
from crew_health.platform.application.tasks import TaskRow
from crew_health.platform.domain.errors import fail


def _instant(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(raw: tuple[object, ...]) -> TaskRow:
    (
        task_raw,
        job_id,
        state,
        progress,
        generation,
        attempts,
        worker_id,
        expires,
        result_ref,
        error,
        created_at,
        updated_at,
    ) = raw
    if not isinstance(task_raw, bytes) or type(generation) is not int or type(attempts) is not int:
        raise fail(ErrorCode.INTERNAL_ERROR)
    task = codec(TaskInput).validate_json(task_raw)
    status = TaskStatus(
        task.task_id,
        cast(str | None, job_id),
        task.public_kind,
        cast(Literal["queued", "running", "succeeded", "failed", "cancelled"], state),
        Quantity(Decimal(str(progress)), Unit("1")),
        cast(str | None, result_ref),
        None if error is None else ErrorCode(str(error)),
        datetime.fromisoformat(str(created_at)),
        datetime.fromisoformat(str(updated_at)),
    )
    deadline = None if expires is None else codec(ElapsedDeadline).validate_json(str(expires))
    return TaskRow(task, status, generation, attempts, cast(str | None, worker_id), deadline)


class TaskRepository:
    def task_size(self, task: TaskInput) -> int:
        return len(codec(TaskInput).dump_json(task))

    def result_size(self, result: TaskResult) -> int:
        return len(codec(TaskResult).dump_json(result))

    def get(self, view: ReadSnapshot | UnitOfWork, task_id: str) -> TaskRow | None:
        rows = view.connection.rows(
            "SELECT input,job_id,state,progress,generation,attempts,worker_id,expires,result_ref,error_code,created_at,updated_at FROM platform_task WHERE task_id=?",
            (task_id,),
            limit=1,
        )
        return None if not rows else _row(rows[0])

    def pending_count(self, uow: UnitOfWork) -> int:
        rows = uow.connection.rows(
            "SELECT COUNT(*) FROM platform_task WHERE state IN ('queued','running')", (), limit=1
        )
        return int(str(rows[0][0]))

    def insert(self, uow: UnitOfWork, task: TaskInput) -> TaskRow:
        status = TaskStatus(
            task.task_id,
            task.task_id if task.public_kind is not None else None,
            task.public_kind,
            "queued",
            Quantity(Decimal(0), Unit("1")),
            None,
            None,
            uow.now,
            uow.now,
        )
        row = TaskRow(task, status, 0, 0, None, None)
        uow.connection.execute(
            "INSERT INTO platform_task(task_id,input,job_id,state,progress,generation,attempts,worker_id,expires,result_ref,error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.task_id,
                codec(TaskInput).dump_json(task),
                status.job_id,
                "queued",
                "0",
                0,
                0,
                None,
                None,
                None,
                None,
                _instant(uow.now),
                _instant(uow.now),
            ),
        )
        return row

    def update(self, uow: UnitOfWork, previous: TaskRow, row: TaskRow) -> None:
        result = uow.connection.execute(
            "UPDATE platform_task SET state=?,progress=?,generation=?,attempts=?,worker_id=?,expires=?,result_ref=?,error_code=?,updated_at=? WHERE task_id=? AND generation=? AND state=?",
            (
                row.status.state,
                str(row.status.progress.value),
                row.generation,
                row.attempts,
                row.worker_id,
                None if row.deadline is None else codec(ElapsedDeadline).dump_json(row.deadline).decode(),
                row.status.result_ref,
                None if row.status.error_code is None else row.status.error_code.value,
                _instant(row.status.updated_at),
                row.task.task_id,
                previous.generation,
                previous.status.state,
            ),
        )
        if result.affected_rows != 1:
            raise fail(ErrorCode.STALE_VERSION)

    def attempt(self, uow: UnitOfWork, lease: TaskLease) -> None:
        uow.connection.execute(
            "INSERT INTO platform_task_attempt(task_id,generation,worker_id,lease) VALUES(?,?,?,?)",
            (lease.task.task_id, lease.generation, lease.worker_id, codec(TaskLease).dump_json(lease)),
        )

    def page(
        self, view: ReadSnapshot | UnitOfWork, *, state: str, after_id: str, limit: int
    ) -> tuple[TaskRow, ...]:
        rows = view.connection.rows(
            "SELECT input,job_id,state,progress,generation,attempts,worker_id,expires,result_ref,error_code,created_at,updated_at FROM platform_task WHERE state=? AND task_id>? ORDER BY task_id LIMIT ?",
            (state, after_id, limit),
            limit=limit,
        )
        return tuple(_row(row) for row in rows)
