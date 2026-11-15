"""CONTRACTS 26: the log is ground only, including every failure and empty result."""

import re
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from crew_health.catchup.api.errors import envelope, to_problem
from crew_health.catchup.api.views import log_wire
from crew_health.catchup.application.composer import CatchUpDeps, parse_since
from crew_health.catchup.application.ports import GroundLogView
from crew_health.catchup.domain.errors import CatchUpError
from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN
from crew_health.kernel.uow import SessionContext


# Adapter over E02 `require_roles(operation_id)` (crew_health.identity); structural copy until it is on main.
class RoleGate(Protocol):
    def session_for(self, request: Request, operation_id: str) -> SessionContext: ...


class Runtime(Protocol):
    @property
    def deps(self) -> CatchUpDeps: ...
    @property
    def log_view(self) -> GroundLogView: ...
    @property
    def gate(self) -> RoleGate | None: ...


def build_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    def guarded(request: Request, run: Callable[[], Response]) -> Response:
        supplied = request.headers.get("X-Correlation-Id")
        correlation = (
            supplied if supplied is not None and re.fullmatch(ID_PATTERN, supplied) else "cuc_unassigned"
        )
        try:
            response = run()
        except CatchUpError as error:
            response = envelope(to_problem(error), correlation)
        except ProblemError as problem:
            response = envelope(problem, correlation)
        response.headers["X-Correlation-Id"] = correlation
        try:
            with runtime.deps.snapshots.snapshot() as view:
                response.headers["Config-Release"] = view.release.release_id
        except ProblemError, CatchUpError:
            pass
        return response

    @router.get("/v1/ground/log", operation_id="getGroundLog")
    def get_log(request: Request) -> Response:
        def run() -> Response:
            gate = runtime.gate
            if gate is None:
                raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
            session = gate.session_for(request, "getGroundLog")
            if Role.GROUND not in session.roles or session.person_id is None:
                # SYSTEM 5.7.1: the log goes to a named ground recipient only.
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
            recipient = session.person_id
            since = None
            values = request.query_params.getlist("since")
            try:
                if len(values) > 1:
                    raise ValueError("duplicate since")
                if values:
                    since = parse_since(values[0])
            except ValueError as error:
                raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
            return JSONResponse(log_wire(runtime.log_view.log(since, recipient=recipient)))

        return guarded(request, run)

    return router
