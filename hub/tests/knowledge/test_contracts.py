import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from crew_health.app.errors import to_envelope
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, Role, SessionContext
from crew_health.knowledge.api.passages import create_router


@pytest.mark.trace("T-E10-11", "T-E10-60", "V23", "D10", "C5")
def test_unsafe_http_representation_is_disabled_with_exact_error_envelope() -> None:
    app = FastAPI()
    app.include_router(create_router())

    @app.middleware("http")
    async def session(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.headers.get("Authorization") == "Bearer unit-fixture":
            request.state.session = SessionContext(
                "session_a", "crew", Actor("person", "person_a"), "person_a", frozenset({Role.CREW})
            )
        return await call_next(request)

    @app.exception_handler(ProblemError)
    async def problem(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(
            to_envelope(error, "correlation_a").model_dump(mode="json"), status_code=error.status
        )

    async def requests() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            path = "/v1/evidence/passages/psg_a"
            anonymous = await client.get(path)
            assert anonymous.status_code == 401
            assert anonymous.json()["code"] == ErrorCode.UNAUTHENTICATED.value
            denied = await client.get(path, headers={"Authorization": "Bearer unit-fixture"})
            assert denied.status_code == 422
            assert denied.json()["code"] == ErrorCode.SOURCE_NOT_APPROVED.value
            assert denied.json()["retryable"] is False
            invalid = await client.get(path + "!", headers={"Authorization": "Bearer unit-fixture"})
            assert invalid.status_code == 400
            assert invalid.json()["code"] == ErrorCode.MALFORMED_REQUEST.value
            assert "text" not in denied.json()

    asyncio.run(requests())


@pytest.mark.trace("T-E10-11", "C5")
def test_registered_operation_matches_frozen_contract() -> None:
    import yaml

    contract = yaml.safe_load(Path("contracts/api/openapi.yaml").read_text(encoding="utf-8"))
    operation = contract["paths"]["/v1/evidence/passages/{passage_id}"]["get"]
    router = create_router()
    app = FastAPI()
    app.include_router(router)
    assert (
        app.openapi()["paths"]["/v1/evidence/passages/{passage_id}"]["get"]["operationId"]
        == operation["operationId"]
    )
