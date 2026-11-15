from __future__ import annotations

from fastapi import APIRouter, Request

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.evidence_passage_schema import EvidencePassage
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import SessionContext


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/evidence/passages/{passage_id}", operation_id="getEvidencePassage")
    def passage(request: Request, passage_id: str) -> EvidencePassage:
        session: object = getattr(request.state, "session", None)
        if not isinstance(session, SessionContext):
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        if not session.roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        require_id(passage_id)
        raise ProblemError(ErrorCode.SOURCE_NOT_APPROVED, ErrorCode.SOURCE_NOT_APPROVED.value)

    return router
