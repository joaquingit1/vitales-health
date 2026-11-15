from __future__ import annotations

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.observations import RecordContext


class OwnerAuthorization:
    """Safe owner-only binding; no officer or helper chart grant is inferred."""

    def require_read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None:
        if session.kind != "crew" or session.person_id != patient_id or session.actor.kind != "person":
            raise RecordError(ErrorCode.UNAUTHORIZED_SCOPE)

    def require_write(self, uow: UnitOfWork, session: SessionContext, context: RecordContext) -> None:
        if (
            session.actor != uow.actor
            or session.kind != "crew"
            or session.person_id != context.patient_id
            or session.person_id != context.operator_id
        ):
            raise RecordError(ErrorCode.UNAUTHORIZED_SCOPE)
