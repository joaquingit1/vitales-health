"""CONTRACTS 12: synchronous HTTP use cases and generated boundary validation."""

import json
import re
from collections.abc import Callable
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.requests_schema import GroundLinkDemoSwitch
from crew_health.groundlink.api.errors import envelope, to_problem
from crew_health.groundlink.api.views import link_wire
from crew_health.groundlink.application.runtime import GroundLinkServices
from crew_health.groundlink.application.service import unavailable
from crew_health.groundlink.domain.errors import GroundLinkError
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN
from crew_health.kernel.uow import SessionContext


# Adapter over E02 `require_roles(operation_id)` (crew_health.identity); structural copy until it is on main.
class RoleGate(Protocol):
    def session_for(self, request: Request, operation_id: str) -> SessionContext: ...


class Runtime(Protocol):
    @property
    def services(self) -> GroundLinkServices: ...
    @property
    def gate(self) -> RoleGate | None: ...


async def _body(request: Request) -> bytes:
    return await request.body()


def _session(runtime: Runtime, request: Request, operation: str, roles: frozenset[Role]) -> SessionContext:
    if runtime.gate is None:
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    session = runtime.gate.session_for(request, operation)
    if not session.roles & roles:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session


def _key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key")
    if value is None or re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$", value) is None:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return value


def _parse_demo(raw: bytes) -> GroundLinkDemoSwitch:
    try:
        value: object = json.loads(raw)
        # The generated optional fields also accept explicit null; the wire schema does not.
        if isinstance(value, dict):
            if "use_schedule" in value and value["use_schedule"] is not True:
                raise ValueError("use_schedule must be true")
            if any(k in value and value[k] is None for k in ("gap_kind", "gap_planned")):
                raise ValueError("non-null optional field")
            if "gap_planned" in value and not isinstance(value["gap_planned"], bool):
                raise ValueError("gap_planned must be boolean")
            delay = value.get("one_way_delay_s")
            if delay is not None and (isinstance(delay, bool) or not isinstance(delay, (int, float))):
                raise ValueError("one_way_delay_s must be numeric")
        return GroundLinkDemoSwitch.model_validate_json(raw)
    except (ValueError, ValidationError) as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error


def build_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    def guarded(request: Request, run: Callable[[str], Response]) -> Response:
        supplied = request.headers.get("X-Correlation-Id")
        correlation = (
            supplied if supplied is not None and re.fullmatch(ID_PATTERN, supplied) else "glc_unassigned"
        )
        try:
            response = run(correlation)
        except GroundLinkError as error:
            response = envelope(to_problem(error), correlation)
        except ProblemError as problem:
            response = envelope(problem, correlation)
        except ValueError, ValidationError:
            response = envelope(
                ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value), correlation
            )
        response.headers["X-Correlation-Id"] = correlation
        if "Config-Release" not in response.headers:
            try:
                with runtime.services.deps.snapshots.snapshot() as view:
                    response.headers["Config-Release"] = view.release.release_id
            except ProblemError:
                pass
        return response

    @router.get("/v1/ground-link", operation_id="getGroundLink")
    def get_link(request: Request) -> Response:
        def run(correlation: str) -> Response:
            _session(
                runtime,
                request,
                "getGroundLink",
                frozenset(
                    {
                        Role.CREW,
                        Role.CREW_MEDICAL_OFFICER,
                        Role.GROUND,
                        Role.OPERATOR,
                        Role.STATION,
                        Role.ADAPTER,
                        Role.EDITOR,
                    }
                ),
            )
            with runtime.services.deps.snapshots.snapshot() as view:
                link = runtime.services.reader.current(view)
                if link is None:
                    raise unavailable()
                return JSONResponse(link_wire(link), headers={"Config-Release": view.release.release_id})

        return guarded(request, run)

    @router.post("/v1/demo/ground-link", operation_id="demoSetGroundLink")
    def set_link(request: Request, raw: Annotated[bytes, Depends(_body)]) -> Response:
        def run(correlation: str) -> Response:
            session = _session(runtime, request, "demoSetGroundLink", frozenset({Role.OPERATOR}))
            key = _key(request)
            parsed = _parse_demo(raw)
            status, body = runtime.services.commands.set_demo(
                session, parsed, key=key, correlation_id=correlation
            )
            return Response(body, status_code=status, media_type="application/json")

        return guarded(request, run)

    return router
