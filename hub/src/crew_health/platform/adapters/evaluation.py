from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.evaluation import EvaluationInput, PendingEvaluation
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.adapters import codec
from crew_health.platform.domain.errors import fail


class EvaluationRepository:
    def __init__(self) -> None:
        self.codec = codec(EvaluationInput)

    def get(self, view: ReadSnapshot | UnitOfWork, completion_id: str) -> PendingEvaluation | None:
        rows = view.connection.rows(
            "SELECT value, first_failed_at, attempts, fault_code FROM platform_evaluation_pending WHERE completion_id=?",
            (completion_id,),
            limit=1,
        )
        if not rows:
            return None
        return self.decode(rows[0])

    def decode(self, row: tuple[object, ...]) -> PendingEvaluation:
        from datetime import datetime

        if not isinstance(row[0], bytes):
            raise fail(ErrorCode.INTERNAL_ERROR)
        return PendingEvaluation(
            self.codec.validate_json(row[0]),
            datetime.fromisoformat(str(row[1])),
            int(str(row[2])),
            str(row[3]),
        )

    def failed(self, uow: UnitOfWork, value: EvaluationInput, code: str) -> PendingEvaluation:
        prior = self.get(uow, value.completion_id)
        marker = PendingEvaluation(
            value, prior.first_failed_at if prior else uow.now, prior.attempts + 1 if prior else 1, code
        )
        uow.connection.execute(
            "INSERT INTO platform_evaluation_pending VALUES (?, ?, ?, ?, ?) ON CONFLICT(completion_id) DO UPDATE SET attempts=excluded.attempts, fault_code=excluded.fault_code",
            (
                value.completion_id,
                self.codec.dump_json(value),
                marker.first_failed_at.isoformat(),
                marker.attempts,
                code,
            ),
        )
        return marker
