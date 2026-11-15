"""Participation API (E22 6; D10): the operations of the contract by name, plus the crew goal consent of D10.

The session comes from E01's middleware (`request.state.session`), which already checked the operation's roles from
the contract. Responses are validated against the generated contract models before they leave."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from crew_health.contracts_gen.schemas.crew_goal_view_schema import CrewGoalView
from crew_health.contracts_gen.schemas.patch_award_schema import PatchAward
from crew_health.contracts_gen.schemas.rebuild_result_schema import RebuildResult
from crew_health.contracts_gen.schemas.streak_view_schema import StreakView
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.participation.application.consent import ConsentCommands
from crew_health.participation.application.context import Deps
from crew_health.participation.application.errors import problem
from crew_health.participation.application.queries import Queries
from crew_health.participation.application.rebuild import Rebuild
from crew_health.participation.application.wire import award_wire, streak_wire
from crew_health.participation.domain.errors import ParticipationError
from crew_health.participation.domain.privacy import reject_breakdown, require_no_selector

_CURSOR = re.compile(r"^t\.([1-9][0-9]{0,8})$")
_IF_MATCH = re.compile(r'^(?:W/)?"?([0-9]{1,9})"?$')
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@dataclass(slots=True)
class ParticipationApi:
    """Bound late by module.py; an unbound API answers CAPABILITY_UNAVAILABLE rather than crashing."""

    deps: Deps | None = None
    queries: Queries | None = None
    consent: ConsentCommands | None = None
    rebuild: Rebuild | None = None

    def bound(self) -> tuple[Deps, Queries, ConsentCommands, Rebuild]:
        if self.deps is None or self.queries is None or self.consent is None or self.rebuild is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)
        return self.deps, self.queries, self.consent, self.rebuild


def _session(request: Request) -> SessionContext:
    session: object = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return session


def _person(session: SessionContext) -> str:
    """Own reads belong to the session person; no person (station, operator) is refused."""
    if session.person_id is None:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session.person_id


def _query(request: Request) -> Mapping[str, str]:
    return dict(request.query_params.multi_items())


def _malformed(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST,
        ErrorCode.MALFORMED_REQUEST.value,
        field_errors=(FieldError(field, ErrorCode.MALFORMED_REQUEST.value, message),),
    )


def _release(deps: Deps) -> str:
    with deps.snapshots.snapshot() as view:
        return view.release.release_id


def _respond(
    model: type[BaseModel], wire: Mapping[str, object], deps: Deps, status: int = 200
) -> JSONResponse:
    model.model_validate(wire)
    return JSONResponse(dict(wire), status_code=status, headers={"Config-Release": _release(deps)})


def _guard[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except ParticipationError as error:
        raise problem(error) from error


def _if_match(value: str | None) -> int | None:
    if value is None:
        return None
    match = _IF_MATCH.fullmatch(value.strip())
    if match is None:
        raise _malformed("/If-Match", "If-Match must be the revision")
    return int(match.group(1))


def _key(value: str | None) -> str:
    if value is None or _KEY.fullmatch(value) is None:
        raise _malformed("/Idempotency-Key", "Idempotency-Key is required")
    return value


def _page(request: Request) -> tuple[int, int]:
    """`cursor` and `limit` only; any other parameter is refused (E22 6). Returns (after threshold, limit)."""
    query = _query(request)
    extra = {k: v for k, v in query.items() if k not in ("cursor", "limit")}
    _guard(lambda: require_no_selector(extra))
    limit = _DEFAULT_LIMIT
    if "limit" in query:
        if not query["limit"].isdecimal() or not 1 <= int(query["limit"]) <= _MAX_LIMIT:
            raise _malformed("/limit", "limit is 1 to 200")
        limit = int(query["limit"])
    after = 0
    if "cursor" in query:
        match = _CURSOR.fullmatch(query["cursor"])
        if match is None:
            raise _malformed("/cursor", "cursor is not valid")
        after = int(match.group(1))
    return after, limit


def build_router(api: ParticipationApi) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/me/streak", operation_id="getMyStreak")
    def get_my_streak(request: Request) -> JSONResponse:
        deps, queries, _, _ = api.bound()
        person = _person(_session(request))
        _guard(lambda: require_no_selector(_query(request)))
        return _respond(StreakView, streak_wire(_guard(lambda: queries.streak(person))), deps)

    @router.get("/v1/me/patches", operation_id="getMyPatches")
    def get_my_patches(request: Request) -> JSONResponse:
        deps, queries, _, _ = api.bound()
        person = _person(_session(request))
        after, limit = _page(request)
        everything = _guard(lambda: queries.patches(person))
        awards = [a for a in everything if a.threshold > after]
        shown = awards[:limit]
        for award in shown:
            PatchAward.model_validate(award_wire(award))
        more = len(awards) > limit
        body = {
            "items": [award_wire(a) for a in shown],
            "page": {"next_cursor": f"t.{shown[-1].threshold}" if more else None, "total": len(everything)},
        }
        return JSONResponse(body, headers={"Config-Release": _release(deps)})

    @router.get("/v1/crew-goal", operation_id="getCrewGoal")
    def get_crew_goal(request: Request) -> JSONResponse:
        deps, queries, _, _ = api.bound()
        _session(request)
        _guard(lambda: reject_breakdown(_query(request)))
        return _respond(CrewGoalView, _guard(queries.crew_goal).wire(), deps)

    @router.get("/v1/me/crew-goal-consent")
    def get_consent(request: Request) -> JSONResponse:
        deps, _, consent, _ = api.bound()
        state = consent.get(_session(request))
        return JSONResponse(
            state.wire(), headers={"Config-Release": _release(deps), "ETag": f'"{state.revision}"'}
        )

    @router.put("/v1/me/crew-goal-consent")
    async def put_consent(
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> JSONResponse:
        deps, _, consent, _ = api.bound()
        session = _session(request)
        enabled = _enabled(await _json(request))
        key, revision = _key(idempotency_key), _if_match(if_match)
        state = consent.put(session, enabled=enabled, if_match=revision, key=key)
        return JSONResponse(
            state.wire(), headers={"Config-Release": _release(deps), "ETag": f'"{state.revision}"'}
        )

    @router.post("/v1/participation/rebuild", operation_id="rebuildParticipation")
    async def rebuild_participation(
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        deps, _, _, rebuild = api.bound()
        session = _session(request)
        body = await _json(request)
        origin = body.get("origin_mode")
        if set(body) != {"origin_mode"} or origin not in ("LIVE", "SIM"):
            raise _malformed("/origin_mode", "origin_mode is LIVE or SIM")
        key = _key(idempotency_key)
        result = rebuild.run(session, origin_mode="LIVE" if origin == "LIVE" else "SIM", key=key)
        return _respond(RebuildResult, result, deps)

    return router


async def _json(request: Request) -> dict[str, object]:
    try:
        parsed: object = await request.json()
    except ValueError as error:
        raise _malformed("/", "the body is not JSON") from error
    if not isinstance(parsed, dict):
        raise _malformed("/", "the body is not an object")
    return parsed


def _enabled(body: Mapping[str, object]) -> bool:
    value = body.get("enabled")
    if set(body) != {"enabled"} or not isinstance(value, bool):
        raise _malformed("/enabled", "enabled is a boolean")
    return value


__all__ = ["ParticipationApi", "build_router"]
