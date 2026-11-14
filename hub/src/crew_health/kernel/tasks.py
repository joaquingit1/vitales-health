from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode, EvidenceKind
from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Unit

if TYPE_CHECKING:
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.kernel.scheduler import ElapsedDeadline
    from crew_health.kernel.units import Quantity
    from crew_health.kernel.uow import Actor, Audience, ReadSnapshot, SessionContext, UnitOfWork


@dataclass(frozen=True, slots=True)
class TaskInput:
    task_id: str
    handler: str
    handler_version: str
    input_ref: str
    input_fingerprint: str
    release_id: str
    model_release_id: str | None
    actor: Actor
    audience: Audience
    command: CommandIdentity
    public_kind: Literal["assessment", "report_extraction", "participation_rebuild"] | None  # internal enum


@dataclass(frozen=True, slots=True)
class TaskLease:
    task: TaskInput
    generation: int
    worker_id: str
    expires: ElapsedDeadline


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    generation: int
    result_ref: str
    result_fingerprint: str


@dataclass(frozen=True, slots=True)
class TaskStatus:
    task_id: str
    job_id: str | None
    public_kind: (
        Literal["assessment", "report_extraction", "participation_rebuild"] | None
    )  # wire: Job.kind for public tasks
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]  # internal enum
    progress: Quantity
    result_ref: str | None
    error_code: ErrorCode | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        require_utc(self.updated_at)
        if self.progress.unit != Unit("1") or not Decimal(0) <= self.progress.value <= Decimal(1):
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)


class TaskQueue(Protocol):
    def submit(self, uow: UnitOfWork, task: TaskInput) -> TaskStatus: ...
    def cancel(self, uow: UnitOfWork, session: SessionContext, task_id: str) -> TaskStatus: ...


class TaskRunner(Protocol):
    def claim(
        self, uow: UnitOfWork, *, worker_id: str, handlers: frozenset[str], limit: int
    ) -> tuple[TaskLease, ...]: ...
    def progress(self, uow: UnitOfWork, lease: TaskLease, value: Quantity) -> bool: ...
    def complete(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> bool: ...
    def fail(self, uow: UnitOfWork, lease: TaskLease, *, code: ErrorCode, retryable: bool) -> bool: ...
    def recover(self, uow: UnitOfWork, *, limit: int) -> int: ...


class TaskHandler(Protocol):
    def execute(self, lease: TaskLease) -> TaskResult: ...
    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None: ...
    def discard(self, result: TaskResult) -> None: ...


@dataclass(frozen=True, slots=True)
class JobResultRef:
    kind: EvidenceKind
    ref_id: str


class JobResultProjector(Protocol):
    def project(
        self, snapshot: ReadSnapshot | UnitOfWork, session: SessionContext, result_ref: str
    ) -> JobResultRef | None: ...


class JobQueries(Protocol):
    def get(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> TaskStatus: ...
    def result(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> JobResultRef | None: ...


@dataclass(frozen=True, slots=True)
class TaskRegistration:
    name: str
    version: str
    handler: TaskHandler
    result_projector: JobResultProjector | None
    public_kind: Literal["assessment", "report_extraction", "participation_rebuild"] | None  # internal enum
