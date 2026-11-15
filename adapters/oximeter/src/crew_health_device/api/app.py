"""Loopback worker control surface; clinical records remain hub owned."""

import hmac
from collections.abc import Callable, Mapping
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from crew_health.contracts_gen.schemas.health_schema import Adapter
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.delivery import Undelivered, release, undelivered
from crew_health_device.application.health import HealthTracker
from crew_health_device.application.ports import Journal
from crew_health_device.domain.acquisition import parse_binding
from crew_health_device.domain.errors import BindingError


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: StrictInt


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: StrictStr


def _row(record: Undelivered) -> dict[str, str | None]:
    return {
        "key": record.key,
        "request_id": record.request_id,
        "state": record.state,
        "detail": record.detail,
    }


def build_app(
    *,
    service: AcquisitionService,
    health: HealthTracker,
    token: str,
    adapter_id: str,
    family: str,
    manifest: Callable[[], Mapping[str, object]],
    after_capture: Callable[[], object] | None = None,
    journal: Journal | None = None,
    journal_limit: int = 0,
) -> FastAPI:
    if not token:
        raise ValueError("empty_token")
    Adapter.model_validate({"adapter_id": adapter_id, "state": health.state()})
    app = FastAPI(title=family)

    @app.exception_handler(HTTPException)
    async def api_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    def authenticated(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = ("Bearer " + token).encode("utf-8")
        provided = (authorization or "").encode("utf-8")
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(401, detail={"code": "UNAUTHENTICATED"})

    def capture(acquisition_id: str) -> None:
        service.capture(acquisition_id)
        if after_capture is not None:
            after_capture()

    @app.get("/health")
    def get_health() -> dict[str, object]:
        entry = Adapter.model_validate({"adapter_id": adapter_id, "state": health.state()})
        return {**entry.model_dump(), "manifest": manifest()}

    @app.post("/acquisitions", dependencies=[Depends(authenticated)])
    def start(document: dict[str, object], background: BackgroundTasks) -> dict[str, str]:
        try:
            binding = parse_binding(document)
        except BindingError as exc:
            raise HTTPException(422, detail={"code": "INVALID_VALUE", "reason": exc.reason}) from exc
        reply = service.start(binding)
        if reply == "started":
            background.add_task(capture, binding.acquisition_id)
        return {"acquisition_id": binding.acquisition_id, "reply": reply}

    @app.post("/acquisitions/{acquisition_id}/cancel", dependencies=[Depends(authenticated)])
    def cancel(acquisition_id: str, document: CancelBody) -> dict[str, str]:
        return {"reply": service.cancel(acquisition_id, document.generation)}

    @app.get("/acquisitions/{acquisition_id}", dependencies=[Depends(authenticated)])
    def status(acquisition_id: str, generation: int) -> dict[str, str]:
        return {"state": service.status(acquisition_id, generation)}

    if journal is not None:
        sends = journal

        @app.get("/deliveries", dependencies=[Depends(authenticated)])
        def deliveries() -> dict[str, object]:
            return {"items": [_row(record) for record in undelivered(sends, journal_limit)]}

        @app.post("/deliveries/release", dependencies=[Depends(authenticated)])
        def release_held(document: ReleaseBody) -> dict[str, str]:
            return {"reply": "released" if release(sends, document.key) else "unknown"}

    return app
