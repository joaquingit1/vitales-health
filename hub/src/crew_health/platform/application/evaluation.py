from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.evaluation import (
    EvaluationInput,
    EvaluatorComputationFailure,
    EvaluatorRegistration,
    PendingEvaluation,
)
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import ElapsedDeadline, ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.platform.domain.errors import fail


class EvaluationStore(Protocol):
    def get(self, view: ReadSnapshot | UnitOfWork, completion_id: str) -> PendingEvaluation | None: ...
    def failed(self, uow: UnitOfWork, value: EvaluationInput, code: str) -> PendingEvaluation: ...
    def decode(self, row: tuple[object, ...]) -> PendingEvaluation: ...


class EvaluationService:
    def __init__(
        self,
        registrations: tuple[EvaluatorRegistration, ...],
        settings: HubSettings,
        ids: IdFactory,
        *,
        repository: EvaluationStore,
        capture: Callable[[UnitOfWork], TimeInput],
        fault_codes: frozenset[str] = frozenset(),
    ) -> None:
        self.registrations = {(r.name, r.version): r.evaluator for r in registrations}
        if len(self.registrations) != len(registrations) or any(
            not r.name or not r.version for r in registrations
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        self.settings, self.ids, self.fault_codes = settings, ids, fault_codes
        self.repository, self.capture = repository, capture

    def run(self, uow: UnitOfWork, value: EvaluationInput) -> Literal["completed", "pending"]:
        rows = uow.connection.rows(
            "SELECT completion_id FROM platform_evaluation_completed WHERE completion_id=?",
            (value.completion_id,),
            limit=1,
        )
        if rows:
            return "completed"
        evaluator = self.registrations.get((value.evaluator, value.evaluator_version))
        if evaluator is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        try:
            with uow.savepoint(self.ids.new("evaluation")):
                evaluator.evaluate(uow, value)
        except EvaluatorComputationFailure as error:
            if error.code not in self.fault_codes:
                raise
            marker = self.repository.failed(uow, value, error.code)
            self._schedule(uow, marker)
            self._declare(uow, value)
            uow.audit(
                AuditEntry(
                    self.ids.new("audit"),
                    "evaluation.pending",
                    1,
                    uow.actor,
                    {"input": value.input_ref, "completion": value.completion_id},
                    uow.correlation_id,
                    None,
                    {},
                )
            )
            return "pending"
        uow.connection.execute("INSERT INTO platform_evaluation_completed VALUES (?)", (value.completion_id,))
        uow.connection.execute(
            "DELETE FROM platform_evaluation_pending WHERE completion_id=?", (value.completion_id,)
        )
        self._declare(uow, value)
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                "evaluation.completed",
                1,
                uow.actor,
                {"input": value.input_ref, "completion": value.completion_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        return "completed"

    def _declare(self, uow: UnitOfWork, value: EvaluationInput) -> None:
        uow.replicate(
            ReplicationChange(
                None,
                "evaluation",
                value.completion_id,
                "version",
                canonical_json({"completion_id": value.completion_id, "input_ref": value.input_ref}),
                (value.input_ref,),
                value.release_id,
                Audience(
                    "evaluation.internal",
                    value.completion_id,
                    AudienceProjectionKey("platform", "evaluation", 1),
                    "1",
                ),
            )
        )

    def _schedule(self, uow: UnitOfWork, marker: PendingEvaluation) -> None:
        if marker.attempts > self.settings.integer("evaluation.retry_max"):
            return
        key = self.ids.derived("retry", marker.input.completion_id)
        rows = uow.connection.rows("SELECT generation FROM platform_schedule WHERE key=?", (key,), limit=1)
        generation = int(str(rows[0][0])) if rows else None
        deadline = ElapsedDeadline(
            self.capture(uow), timedelta(milliseconds=self.settings.integer("evaluation.retry_backoff_ms"))
        )
        uow.schedule(
            ScheduledJob(
                key,
                "platform.evaluation.retry",
                deadline,
                {"completion_id": marker.input.completion_id},
                None,
            ),
            expected_generation=generation,
        )

    def retry(self, uow: UnitOfWork, *, completion_id: str) -> Literal["completed", "pending"]:
        marker = self.repository.get(uow, completion_id)
        if marker is None:
            rows = uow.connection.rows(
                "SELECT completion_id FROM platform_evaluation_completed WHERE completion_id=?",
                (completion_id,),
                limit=1,
            )
            if rows:
                return "completed"
            raise fail(ErrorCode.NOT_FOUND)
        return self.run(uow, marker.input)

    def pending(
        self, snapshot: ReadSnapshot, *, after_id: str | None, limit: int
    ) -> tuple[PendingEvaluation, ...]:
        if not 1 <= limit <= self.settings.integer("evaluation.page_size"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        rows = snapshot.connection.rows(
            "SELECT value, first_failed_at, attempts, fault_code FROM platform_evaluation_pending WHERE completion_id>? ORDER BY completion_id LIMIT ?",
            (after_id or "", limit),
            limit=limit,
        )
        return tuple(self.repository.decode(row) for row in rows)
