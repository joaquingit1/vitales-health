from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import AbsoluteDeadline, ElapsedDeadline, ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork
from crew_health.platform.application.uow import codec
from crew_health.platform.domain.errors import fail


class SchedulerService:
    def __init__(
        self,
        settings: HubSettings,
        *,
        deadline: Callable[[UnitOfWork, ScheduledJob], datetime | None] | None = None,
        ownership: Callable[[UnitOfWork, ScheduledJob], bool] | None = None,
    ) -> None:
        self.settings, self.deadline, self.ownership = settings, deadline, ownership
        self.codec = codec(ScheduledJob)
        self.handlers: dict[str, Callable[[UnitOfWork, ScheduledJob], None]] = {}

    def _encode(self, job: ScheduledJob) -> bytes:
        raw = (
            codec(AbsoluteDeadline).dump_json(job.deadline)
            if isinstance(job.deadline, AbsoluteDeadline)
            else codec(ElapsedDeadline).dump_json(job.deadline)
        )
        return canonical_json(
            {
                "key": job.key,
                "handler": job.handler,
                "deadline": json.loads(raw),
                "payload": dict(job.payload),
                "ownership_epoch": job.ownership_epoch,
            }
        )

    def replace(self, uow: UnitOfWork, job: ScheduledJob, *, expected_generation: int | None) -> int:
        rows = uow.connection.rows(
            "SELECT generation FROM platform_schedule WHERE key=?", (job.key,), limit=1
        )
        current = int(str(rows[0][0])) if rows else None
        if current != expected_generation:
            raise fail(ErrorCode.STALE_VERSION)
        if current is None:
            count = uow.connection.rows("SELECT count(*) FROM platform_schedule", (), limit=1)
            if int(str(count[0][0])) >= self.settings.integer("scheduler.max_jobs"):
                raise fail(ErrorCode.INTERNAL_ERROR)
        generation = 1 if current is None else current + 1
        due = (
            job.deadline.at
            if isinstance(job.deadline, AbsoluteDeadline)
            else self.deadline(uow, job)
            if self.deadline
            else None
        )
        uow.connection.execute(
            "INSERT INTO platform_schedule VALUES (?, ?, ?, ?, 0) ON CONFLICT(key) DO UPDATE SET generation=excluded.generation, job=excluded.job, due=excluded.due, completed=0",
            (job.key, generation, self._encode(job), due.isoformat() if due else None),
        )
        return generation

    def cancel(self, uow: UnitOfWork, key: str, *, expected_generation: int) -> bool:
        rows = uow.connection.rows(
            "SELECT generation, completed FROM platform_schedule WHERE key=?", (key,), limit=1
        )
        if not rows or rows[0][0] != expected_generation:
            raise fail(ErrorCode.STALE_VERSION)
        if rows[0][1]:
            return False
        uow.connection.execute(
            "UPDATE platform_schedule SET completed=1 WHERE key=? AND generation=?",
            (key, expected_generation),
        )
        return True

    def register(self, handler: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None:
        if handler in self.handlers or len(self.handlers) >= self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self.handlers[handler] = fn

    def dispatch(self, uow: UnitOfWork, *, limit: int) -> int:
        if not 1 <= limit <= self.settings.integer("scheduler.batch_size"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        rows = uow.connection.rows(
            "SELECT key, generation, job FROM platform_schedule WHERE completed=0 AND due<=? ORDER BY due,key LIMIT ?",
            (uow.now.isoformat(), limit),
            limit=limit,
        )
        completed = 0
        for key, generation, raw in rows:
            if not isinstance(raw, bytes):
                raise fail(ErrorCode.INTERNAL_ERROR)
            job = self.codec.validate_json(raw)
            if job.handler not in self.handlers:
                continue
            if job.ownership_epoch is not None and (self.ownership is None or not self.ownership(uow, job)):
                continue
            self.handlers[job.handler](uow, job)
            uow.connection.execute(
                "UPDATE platform_schedule SET completed=1 WHERE key=? AND generation=?", (key, generation)
            )
            completed += 1
        return completed

    def recalculate(self, uow: UnitOfWork, *, limit: int) -> int:
        if not 1 <= limit <= self.settings.integer("scheduler.batch_size") or self.deadline is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        rows = uow.connection.rows(
            "SELECT key, job FROM platform_schedule WHERE completed=0 ORDER BY key LIMIT ?",
            (limit,),
            limit=limit,
        )
        for key, raw in rows:
            if not isinstance(raw, bytes):
                raise fail(ErrorCode.INTERNAL_ERROR)
            job = self.codec.validate_json(raw)
            if not isinstance(job.deadline, AbsoluteDeadline):
                due = self.deadline(uow, job)
                uow.connection.execute(
                    "UPDATE platform_schedule SET due=? WHERE key=?", (due.isoformat() if due else None, key)
                )
        return len(rows)

    def recalculate_all(self, uow: UnitOfWork) -> int:
        if self.deadline is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        after, count = "", 0
        batch = self.settings.integer("scheduler.batch_size")
        maximum = self.settings.integer("scheduler.max_jobs")
        for _ in range(maximum // batch + 1):
            rows = uow.connection.rows(
                "SELECT key,job FROM platform_schedule WHERE completed=0 AND key>? ORDER BY key LIMIT ?",
                (after, batch),
                limit=batch,
            )
            for key, raw in rows:
                if not isinstance(raw, bytes) or not isinstance(key, str):
                    raise fail(ErrorCode.INTERNAL_ERROR)
                job = self.codec.validate_json(raw)
                if not isinstance(job.deadline, AbsoluteDeadline):
                    due = self.deadline(uow, job)
                    uow.connection.execute(
                        "UPDATE platform_schedule SET due=? WHERE key=?",
                        (due.isoformat() if due else None, key),
                    )
                after = key
                count += 1
                if count > maximum:
                    raise fail(ErrorCode.INTERNAL_ERROR)
            if len(rows) < batch:
                return count
        raise fail(ErrorCode.INTERNAL_ERROR)
