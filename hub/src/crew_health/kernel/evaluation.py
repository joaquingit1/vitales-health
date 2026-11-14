from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.kernel.clock import require_utc

if TYPE_CHECKING:
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


class EvaluatorComputationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    code: str


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    evaluator: str
    evaluator_version: str
    input_ref: str
    observed: TimeInput
    release_id: str
    partition_ref: str
    input_revision: int
    completion_id: str


@dataclass(frozen=True, slots=True)
class PendingEvaluation:
    input: EvaluationInput
    first_failed_at: datetime
    attempts: int
    fault_code: str

    def __post_init__(self) -> None:
        require_utc(self.first_failed_at)


class Evaluator(Protocol):
    def evaluate(self, uow: UnitOfWork, value: EvaluationInput) -> None: ...


class EvaluationIsolation(Protocol):
    def run(
        self, uow: UnitOfWork, value: EvaluationInput
    ) -> Literal["completed", "pending"]: ...  # internal enum
    def retry(
        self, uow: UnitOfWork, *, completion_id: str
    ) -> Literal["completed", "pending"]: ...  # internal enum
    def pending(
        self, snapshot: ReadSnapshot, *, after_id: str | None, limit: int
    ) -> tuple[PendingEvaluation, ...]: ...


@dataclass(frozen=True, slots=True)
class EvaluatorRegistration:
    name: str
    version: str
    evaluator: Evaluator
