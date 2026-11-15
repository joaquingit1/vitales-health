"""Small authenticated transport. Patient data never appears in error details."""

import asyncio
import hmac

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse

from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.application.service import ForecastRequest, ForecastService


def create_app(service: ForecastService, settings: WorkerSettings, token: str | None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        result = service.health()
        if not token:
            result = result.model_copy(update={"state": "unavailable", "reason": "token_missing"})
        return JSONResponse(result.model_dump(mode="json"))

    @app.post("/v1/forecast")
    async def forecast(request: Request) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")
        ):
            return JSONResponse({"code": "UNAUTHORIZED"}, status_code=401)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings.max_body_bytes:
                return JSONResponse({"code": "BODY_TOO_LARGE"}, status_code=413)
            body.extend(chunk)
        try:
            parsed = ForecastRequest.model_validate_json(bytes(body))
        except ValidationError:
            return JSONResponse({"code": "MALFORMED_REQUEST"}, status_code=422)
        result = await asyncio.to_thread(service.forecast, parsed, settings.timeout_ms / 1000)
        return JSONResponse(result.model_dump(mode="json"))

    return app
