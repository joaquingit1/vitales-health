"""FastAPI routes of the supplies module (E32 6.1): getSupplyCover and recordSupplyUse."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.requests_schema import SupplyUseCreate
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN
from crew_health.kernel.uow import SessionContext
from crew_health.supplies.api.errors import envelope, malformed, to_problem
from crew_health.supplies.application.use_cases import CoverResult, UseRequest, UseResult
from crew_health.supplies.application.wire import encode
from crew_health.supplies.domain.errors import SuppliesError


# Provided by E02 (crew_health.identity), bound under this class by app/privacy.py. Resolves the caller's session for one
# operation or raises the contract's UNAUTHENTICATED problem.
class RequestAuthenticator(Protocol):
    def session(self, request: Request, operation_id: str) -> SessionContext: ...


type Authenticate = Callable[[Request, str], SessionContext]


class SuppliesApi(Protocol):
    def cover(self, environment_id: str | None) -> CoverResult: ...
    def record(self, session: SessionContext, request: UseRequest) -> UseResult: ...
    def correlation(self) -> str: ...


READ_ROLES = frozenset(
    {
        Role.CREW,
        Role.CREW_MEDICAL_OFFICER,
        Role.GROUND,
        Role.OPERATOR,
        Role.STATION,
        Role.ADAPTER,
        Role.EDITOR,
    }
)
USE_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.STATION})
ENVIRONMENTS = frozenset(
    {"station_leo", "transit", "lunar_surface", "mars_surface"}
)  # common.schema environment_id
_KEY_MIN, _KEY_MAX = 8, 128  # openapi IdempotencyKey
_BODY_MAX = 16384  # bytes; a SupplyUseCreate is under two hundred


def _correlation(request: Request, api: SuppliesApi) -> str:
    """The caller's X-Correlation-Id when it is an ID (common.schema id), else one the hub issues."""
    value = request.headers.get("X-Correlation-Id")
    if value is not None and re.fullmatch(ID_PATTERN, value) is not None:
        return value
    return api.correlation()


def _session(
    authenticate: Authenticate, request: Request, operation: str, roles: frozenset[Role]
) -> SessionContext:
    session = authenticate(request, operation)
    if not session.roles & roles:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session


def _key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if key is None or not _KEY_MIN <= len(key) <= _KEY_MAX:
        raise malformed("Idempotency-Key missing or malformed", "/Idempotency-Key")
    return key


def _no_constant(text: str) -> object:
    raise ValueError("NaN or infinity: " + text)


def _parse(raw: bytes) -> tuple[SupplyUseCreate, Decimal, str]:
    """Shape from the generated contract model; the quantity itself is read exactly, never through a float."""
    if len(raw) > _BODY_MAX:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        value = json.loads(raw, parse_float=Decimal, parse_constant=_no_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise malformed("body is not JSON", "/") from error
    if not isinstance(value, dict):
        raise malformed("body is not an object", "/")
    quantity = value.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int | Decimal):
        raise malformed("quantity is a number", "/quantity")
    try:
        parsed = SupplyUseCreate.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        pointer = "/" + "/".join(str(p) for p in first.get("loc", ()))
        if first.get("type") in {"greater_than", "greater_than_equal"} and pointer == "/quantity":
            raise ProblemError(ErrorCode.INVALID_VALUE, "quantity is above zero") from error
        raise malformed(str(first.get("msg", "invalid body")), pointer) from error
    # The fingerprint covers the body's meaning, not its spacing or key order: exact numbers, sorted keys.
    fingerprint = hashlib.sha256(encode(value, sort_keys=True)).hexdigest()
    return parsed, Decimal(quantity), fingerprint


def build_router(api: SuppliesApi, authenticate: Authenticate) -> APIRouter:
    router = APIRouter()

    def problem(correlation: str, error: SuppliesError | ProblemError) -> Response:
        return envelope(to_problem(error) if isinstance(error, SuppliesError) else error, correlation)

    def headers(correlation: str, release_id: str) -> dict[str, str]:
        """Config-Release names the release the body was computed from, read in the same snapshot (DD8)."""
        return {"Config-Release": release_id, "X-Correlation-Id": correlation}

    @router.get("/v1/supplies", operation_id="getSupplyCover")
    def get_supply_cover(request: Request) -> Response:
        correlation = _correlation(request, api)
        try:
            _session(authenticate, request, "getSupplyCover", READ_ROLES)
            environment_id = request.query_params.get("environment_id")
            if environment_id is not None and environment_id not in ENVIRONMENTS:
                raise malformed("environment_id is not an environment", "/environment_id")
            shown = api.cover(environment_id)
            return Response(
                shown.body,
                status_code=200,
                media_type="application/json",
                headers=headers(correlation, shown.release_id),
            )
        except (SuppliesError, ProblemError) as error:
            return problem(correlation, error)

    @router.post("/v1/supplies/{supply_id}/uses", operation_id="recordSupplyUse", status_code=201)
    async def record_supply_use(request: Request, supply_id: str) -> Response:
        raw = await request.body()
        correlation = _correlation(request, api)
        try:
            session = _session(authenticate, request, "recordSupplyUse", USE_ROLES)
            key = _key(request)
            parsed, quantity, fingerprint = _parse(raw)
            use = UseRequest(
                supply_id=supply_id,
                quantity=quantity,
                plan_run_id=None if parsed.plan_run_id is None else parsed.plan_run_id.root,
                help_session_id=None if parsed.help_session_id is None else parsed.help_session_id.root,
                step_id=None if parsed.step_id is None else parsed.step_id.root,
                client_time=parsed.client_time.root,
                key=key,
                config_release=request.headers.get("Config-Release"),
                body_sha256=fingerprint,
                correlation_id=correlation,
            )
            result = api.record(session, use)
        except (SuppliesError, ProblemError) as error:
            return problem(correlation, error)
        out = headers(correlation, result.release_id)
        out["Location"] = f"/v1/supplies/{supply_id}/uses/{result.supply_use_id}"
        return Response(result.body, status_code=result.status, media_type="application/json", headers=out)

    return router
