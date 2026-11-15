from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request, Response

from crew_health.app.errors import to_envelope
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.job_schema import Job
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.tasks import JobQueries, JobResultRef, TaskStatus
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext
from crew_health.platform.domain.errors import fail


def job_body(status: TaskStatus, result: JobResultRef | None, correlation_id: str) -> bytes:
    if status.job_id is None or status.public_kind is None:
        raise fail(ErrorCode.NOT_FOUND)
    raw = canonical_json(
        {
            "job_id": status.job_id,
            "kind": status.public_kind,
            "state": status.state,
            "progress": float(status.progress.value),
            "result_ref": None if result is None else {"kind": result.kind.value, "ref_id": result.ref_id},
            "error": None
            if status.error_code is None
            else to_envelope(
                ProblemError(status.error_code, status.error_code.value), correlation_id
            ).model_dump(mode="json"),
            "created_at": status.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "updated_at": status.updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    )
    Job.model_validate_json(raw)
    return raw


def create_router(
    *,
    snapshots: ReadSnapshotFactory,
    queries: JobQueries,
    authenticate: Callable[[Request], SessionContext],
    cancel: Callable[[Request, SessionContext, str], TaskStatus],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/jobs/{job_id}", operation_id="getJob")
    def get_job(request: Request, job_id: str) -> Response:
        session = authenticate(request)
        with snapshots.snapshot() as view:
            status = queries.get(view, session, job_id)
            result = queries.result(view, session, job_id)
        return Response(
            job_body(status, result, request.headers.get("X-Correlation-Id", "job.query")),
            media_type="application/json",
        )

    @router.post("/v1/jobs/{job_id}/cancel", operation_id="cancelJob")
    def cancel_job(request: Request, job_id: str) -> Response:
        session = authenticate(request)
        with snapshots.snapshot() as view:
            queries.get(view, session, job_id)
        status = cancel(request, session, job_id)
        return Response(
            job_body(status, None, request.headers.get("X-Correlation-Id", "job.cancel")),
            media_type="application/json",
        )

    return router
