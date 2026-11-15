"""Terminal task outcomes that may retain a deliberately incomplete owner result."""

from typing import Protocol

from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.ids import require_id
from crew_health.kernel.tasks import TaskLease, TaskResult
from crew_health.kernel.uow import Audience, AudienceProjectionKey, UnitOfWork


class TaskFailureResults(Protocol):
    def fail_with_result(
        self, uow: UnitOfWork, lease: TaskLease, result: TaskResult, *, code: ErrorCode
    ) -> bool: ...

    def fail_pending(self, uow: UnitOfWork, task_id: str, *, code: ErrorCode) -> bool: ...


def job_audience(task_id: str) -> Audience:
    return Audience(
        "platform.job.owner", require_id(task_id), AudienceProjectionKey("platform", "job", 1), "1"
    )
