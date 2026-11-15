"""HTTP edge for getKitMap and kitMapWhatIf (contracts/api/openapi.yaml, E27 6).

Both are reads: no Idempotency-Key, no write, no event. Errors leave as the contract ErrorEnvelope (D5).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.requests_schema import KitMapWhatIf as WhatIfRequest
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import SessionContext
from crew_health.kitmap.api.views import kit_map_view, kit_map_what_if
from crew_health.kitmap.application.errors import InvalidEnvironment, WhatIfInvalid
from crew_health.kitmap.application.queries import KitMapReads
from crew_health.kitmap.application.whatif import RunWhatIf, WhatIfCommand

GET_KIT_MAP = "getKitMap"
KIT_MAP_WHAT_IF = "kitMapWhatIf"
CORRELATION_HEADER = "X-Correlation-Id"
RELEASE_HEADER = "Config-Release"
CORRELATION_PREFIX = "corr"
JSON_MEDIA_TYPE = "application/json"


# Provided by E02 (crew_health.identity); structural copy until it is on main. E02 2: require_roles(operation_id)
# returns the session dependency, which raises UNAUTHENTICATED or UNAUTHORIZED_SCOPE from the generated role table.
class RequireRoles(Protocol):
    def __call__(self, operation_id: str) -> Callable[[Request], SessionContext]: ...


class KitMapApi:
    """Late-bound collaborators of the two routes; every one missing fails closed."""

    def __init__(self) -> None:
        self.reads: KitMapReads | None = None
        self.what_if: RunWhatIf | None = None
        self.require_roles: RequireRoles | None = None
        self.ids: IdFactory | None = None


def _correlation_id(api: KitMapApi, request: Request) -> str:
    supplied = request.headers.get(CORRELATION_HEADER)
    if supplied is not None:
        try:
            return require_id(supplied)
        except ProblemError:
            pass  # an unusable header is replaced, never echoed
    if api.ids is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "no id factory", retryable=True)
    return api.ids.new(CORRELATION_PREFIX)


def _json(model: BaseModel, status: int, headers: Mapping[str, str]) -> Response:
    return _bytes(model.model_dump_json(by_alias=True).encode("utf-8"), status, headers)


def _bytes(content: bytes, status: int, headers: Mapping[str, str]) -> Response:
    return Response(
        content=content,
        status_code=status,
        media_type=JSON_MEDIA_TYPE,
        headers=dict(headers),
    )


def _error(error: ProblemError, correlation_id: str) -> Response:
    envelope = ErrorEnvelope.model_validate(
        {
            "code": error.code.value,
            "message": str(error) or error.code.value,
            "retryable": error.retryable,
            "field_errors": [
                {"field": f.field or "/", "code": f.code, "message": f.message} for f in error.field_errors
            ],
            "correlation_id": correlation_id,
            "current_state": error.current_state,
            "supported_alternative": error.supported_alternative,
        }
    )
    return _json(envelope, error.status, {CORRELATION_HEADER: correlation_id})


def _authorize(api: KitMapApi, operation_id: str, request: Request) -> SessionContext:
    if api.require_roles is None:
        # No identity module: nobody is signed in (fail closed).
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return api.require_roles(operation_id)(request)


def _reject_constant(token: str) -> object:
    raise ValueError("non-finite number " + token)


def _pointer(loc: tuple[int | str, ...]) -> str:
    return "/" + "/".join(str(part) for part in loc)


def parse_what_if(raw: bytes, environments: tuple[str, ...]) -> WhatIfCommand:
    """Parse the body with numbers as Decimal; NaN and Infinity are malformed, schema failures INVALID_VALUE."""
    try:
        body = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except ValueError, UnicodeDecodeError:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from None
    if not isinstance(body, dict):
        raise WhatIfInvalid((FieldError("/", "type", "object expected"),))
    environment = body.get("environment_id")
    if isinstance(environment, str) and environment not in environments:
        raise InvalidEnvironment(environment)
    try:
        WhatIfRequest.model_validate(body, strict=False)
    except ValidationError as error:
        raise WhatIfInvalid(
            [FieldError(_pointer(e["loc"]), e["type"], e["msg"]) for e in error.errors()]
        ) from None
    errors = _schema_extras(body)
    if errors:
        raise WhatIfInvalid(errors)
    return WhatIfCommand(
        add_instruments=tuple(body.get("add_instruments") or ()),
        add_supplies=tuple((s["supply_id"], Decimal(s["quantity"])) for s in body.get("add_supplies") or ()),
        add_skills=tuple((s["skill_id"], tuple(s["person_ids"])) for s in body.get("add_skills") or ()),
        environment_id=environment if isinstance(environment, str) else None,
    )


def _schema_extras(body: Mapping[str, object]) -> list[FieldError]:
    """The schema keywords the generated model does not carry: minProperties 1 and uniqueItems."""
    if not any(key in body for key in ("add_instruments", "add_supplies", "add_skills")):
        return [FieldError("/", "min_properties", "add instruments, supplies or skills")]
    errors: list[FieldError] = []
    instruments = body.get("add_instruments")
    if isinstance(instruments, list):
        errors.extend(
            FieldError(f"/add_instruments/{i}", "unique_items", "repeated instrument")
            for i, item in enumerate(instruments)
            if item in instruments[:i]
        )
    skills = body.get("add_skills")
    for i, skill in enumerate(skills if isinstance(skills, list) else ()):
        people = skill.get("person_ids") if isinstance(skill, dict) else None
        if isinstance(people, list):
            errors.extend(
                FieldError(f"/add_skills/{i}/person_ids/{p}", "unique_items", "repeated person")
                for p, person in enumerate(people)
                if person in people[:p]
            )
    return errors


def _is_json(request: Request) -> bool:
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media == JSON_MEDIA_TYPE


def build_router(api: KitMapApi) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/kit-map", operation_id=GET_KIT_MAP)
    def get_kit_map(request: Request) -> Response:
        correlation_id = ""
        try:
            correlation_id = _correlation_id(api, request)
            _authorize(api, GET_KIT_MAP, request)
            if api.reads is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "kit map not wired", retryable=True)
            result = api.reads.view(request.query_params.get("environment_id"))
            return _bytes(
                kit_map_view(result),
                200,
                {CORRELATION_HEADER: correlation_id, RELEASE_HEADER: result.ctx.release.release_id},
            )
        except ProblemError as error:
            return _error(error, correlation_id or _fallback_id(api))
        except Exception:
            # Any other failure leaves as INTERNAL_ERROR in the envelope, never a bare 500.
            return _error(_internal(), correlation_id or _fallback_id(api))

    @router.post("/v1/kit-map/what-if", operation_id=KIT_MAP_WHAT_IF)
    async def what_if(request: Request) -> Response:
        correlation_id = ""
        try:
            correlation_id = _correlation_id(api, request)
            _authorize(api, KIT_MAP_WHAT_IF, request)
            if not _is_json(request):
                raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, ErrorCode.UNSUPPORTED_MEDIA_TYPE.value)
            if api.what_if is None or api.reads is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "kit map not wired", retryable=True)
            command = parse_what_if(await request.body(), api.reads.vocabulary.environments)
            result = api.what_if.execute(command)
            return _bytes(
                kit_map_what_if(result),
                200,
                {CORRELATION_HEADER: correlation_id, RELEASE_HEADER: result.after.ctx.release.release_id},
            )
        except ProblemError as error:
            return _error(error, correlation_id or _fallback_id(api))
        except Exception:
            return _error(_internal(), correlation_id or _fallback_id(api))

    return router


def _internal() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)


def _fallback_id(api: KitMapApi) -> str:
    """Correlation ID when the header path itself failed; a fixed reference only when no factory exists."""
    if api.ids is not None:
        try:
            return api.ids.new(CORRELATION_PREFIX)
        except ProblemError:
            pass
    return CORRELATION_PREFIX + "_unavailable"
