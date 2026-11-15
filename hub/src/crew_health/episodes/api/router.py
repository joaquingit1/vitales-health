from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from crew_health.contracts_gen.schemas.clinical_review_schema import ClinicalReview
from crew_health.contracts_gen.schemas.episode_schema import Episode as ContractEpisode
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.episodes.api.models import ReviewRequest, ReviewResponse, action_adapter
from crew_health.episodes.application import ports
from crew_health.episodes.application.codec import obj, text
from crew_health.episodes.application.queries import EpisodeQueries
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.kernel.commands import canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError, TargetBusy
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWorkFactory


@dataclass(slots=True)
class EpisodeApi:
    service: EpisodeService | None = None
    queries: EpisodeQueries | None = None
    uows: UnitOfWorkFactory | None = None
    snapshots: ReadSnapshotFactory | None = None
    auth: ports.SessionAuthenticator | None = None
    fingerprint: ports.CommandFingerprint | None = None
    ids: IdFactory | None = None
    max_body_bytes: int = 65536


def _response(body: object, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(
        canonical_json(body), status_code=status, media_type="application/json", headers=headers or {}
    )


def _base_episode(result: dict[str, object], *, extended: bool = False) -> dict[str, object]:
    if result.get("projection_kind") == "delivered_episode":
        return result
    base = {k: v for k, v in result.items() if k in ContractEpisode.model_fields}
    base["followup_tasks"] = (
        [
            {k: v for k, v in obj(t).items() if k in ("task_id", "action", "due_at", "owner", "state")}
            for t in result["followup_tasks"]
        ]
        if isinstance(result["followup_tasks"], list)
        else []
    )
    ContractEpisode.model_validate(base)
    return result if extended else base


def _error(exc: ProblemError, correlation: str) -> Response:
    body = {
        "code": exc.code.value,
        "message": str(exc),
        "retryable": exc.retryable,
        "field_errors": [{"field": f.field, "code": f.code, "message": f.message} for f in exc.field_errors],
        "current_state": exc.current_state,
        "supported_alternative": exc.supported_alternative,
        "correlation_id": correlation,
    }
    ErrorEnvelope.model_validate(body)
    return _response(body, exc.status, {"X-Correlation-Id": correlation})


def _auth(api: EpisodeApi, request: Request, operation: str) -> SessionContext:
    if api.auth is None:
        raise ProblemError(ErrorCode.UNAUTHENTICATED, "identity unavailable")
    session = api.auth.authenticate(dict(request.headers), operation)
    if session is None:
        raise ProblemError(ErrorCode.UNAUTHENTICATED, "identity unavailable")
    if session.actor.kind == "assistant":
        raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "human session required")
    return session


def _version(request: Request) -> int | None:
    value = request.headers.get("If-Match")
    if value is None:
        return None
    value = value.strip('"')
    if not value.isdigit() or len(value) > 15:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid revision")
    return int(value)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate key")
        result[k] = v
    return result


def build_router(api: EpisodeApi) -> APIRouter:
    router = APIRouter()

    def wrap(request: Request, call: Callable[[], Response]) -> Response:
        correlation = api.ids.new("corr") if api.ids else "corr_unavailable"
        try:
            response = call()
            response.headers["X-Correlation-Id"] = correlation
            return response
        except TargetBusy:
            # Another command on this episode held the writer past storage.busy_timeout_ms: a concurrent edit that
            # supersedes the If-Match revision, so the loser gets the contract conflict, never a 500 (T-E15-025).
            return _error(ProblemError(ErrorCode.STALE_VERSION, "episode changed concurrently"), correlation)
        except ProblemError as exc:
            return _error(exc, correlation)
        except ValueError, ValidationError:
            return _error(ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid request"), correlation)
        except Exception:
            return _error(
                ProblemError(ErrorCode.INTERNAL_ERROR, "episode operation failed", retryable=True),
                correlation,
            )

    def query(request: Request, episode_id: str, operation: str) -> Response:
        session = _auth(api, request, operation)
        require_id(episode_id)
        if api.queries is None or api.snapshots is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "episodes unavailable", retryable=True)
        with api.snapshots.snapshot() as view:
            if operation == "getEpisode":
                result = _base_episode(
                    api.queries.for_session(
                        view,
                        session,
                        episode_id,
                        extended="application/vnd.crew-health.episode+json"
                        in request.headers.get("accept", ""),
                    ),
                    extended="application/vnd.crew-health.episode+json" in request.headers.get("accept", ""),
                )
                headers = {
                    **({"ETag": '"' + str(result["revision"]) + '"'} if "revision" in result else {}),
                    "Config-Release": view.release.release_id,
                }
            elif operation == "getEpisodeHistory":
                result = api.queries.history_page(
                    view,
                    session,
                    episode_id,
                    request.query_params.get("kind", "ack"),
                    request.query_params.get("cursor"),
                    int(request.query_params.get("limit", "50")),
                )
                headers = {}
            elif operation == "getEpisodeTimeline":
                result = api.queries.timeline(
                    view,
                    session,
                    episode_id,
                    request.query_params.get("cursor"),
                    int(request.query_params.get("limit", "50")),
                )
                headers = {"Config-Release": view.release.release_id}
            else:
                if session.kind == "ground":
                    return _response(api.queries.delivered(view, session, episode_id, field="reviews"))
                ep = api.queries.repo.require(view, episode_id)
                api.queries.service.authorize(view, session, ep, fields=frozenset({"reviews"}))
                limit = int(request.query_params.get("limit", "50"))
                cursor = request.query_params.get("cursor", "")
                if not 1 <= limit <= 200:
                    raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid limit")
                rows = view.connection.rows(
                    "SELECT review_id,data FROM episodes_review WHERE episode_id=? AND review_id>? ORDER BY review_id LIMIT ?",
                    (episode_id, cursor, limit + 1),
                    limit=limit + 1,
                )
                from crew_health.episodes.application.codec import decode

                result = {
                    "episode_id": episode_id,
                    "revision": ep.revision,
                    "items": [decode(r[1]) for r in rows[:limit]],
                    "next_cursor": rows[limit - 1][0] if len(rows) > limit else None,
                }
                headers = {"Config-Release": view.release.release_id}
        return _response(result, headers=headers)

    @router.get("/v1/episodes/{episode_id}", operation_id="getEpisode")
    def get_episode(request: Request, episode_id: str) -> Response:
        return wrap(request, lambda: query(request, episode_id, "getEpisode"))

    @router.get("/v1/episodes/{episode_id}/history", operation_id="getEpisodeHistory")
    def get_history(request: Request, episode_id: str) -> Response:
        return wrap(request, lambda: query(request, episode_id, "getEpisodeHistory"))

    @router.get("/v1/episodes/{episode_id}/timeline", operation_id="getEpisodeTimeline")
    def get_timeline(request: Request, episode_id: str) -> Response:
        return wrap(request, lambda: query(request, episode_id, "getEpisodeTimeline"))

    @router.get("/v1/episodes/{episode_id}/reviews", operation_id="listReviews")
    def list_reviews(request: Request, episode_id: str) -> Response:
        return wrap(request, lambda: query(request, episode_id, "listReviews"))

    @router.get("/v1/episodes", operation_id="listEpisodes")
    def list_episodes(request: Request) -> Response:
        def run() -> Response:
            session = _auth(api, request, "listEpisodes")
            if api.snapshots is None or api.queries is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "episodes unavailable", retryable=True)
            with api.snapshots.snapshot() as view:
                result = api.queries.list_episodes(
                    view,
                    session,
                    request.query_params.get("patient_id"),
                    request.query_params.get("cursor"),
                    int(request.query_params.get("limit", "50")),
                    tuple(request.query_params.getlist("state")),
                )
            return _response(result)

        return wrap(request, run)

    async def command(
        request: Request, episode_id: str, operation: Literal["actOnEpisode", "recordReview"]
    ) -> Response:
        raw = bytearray()
        try:
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > api.max_body_bytes:
                    raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "body bound")
        except ProblemError as exc:
            return _error(exc, api.ids.new("corr") if api.ids else "corr_unavailable")

        def run() -> Response:
            session = _auth(api, request, operation)
            require_id(episode_id)
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "JSON required")
            if api.service is None or api.queries is None or api.uows is None or api.fingerprint is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "episode command not bound", retryable=True)
            parsed = obj(
                json.loads(
                    raw,
                    object_pairs_hook=_unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
                )
            )
            if operation == "actOnEpisode":
                body = action_adapter().validate_python(parsed).model_dump(mode="json", exclude_unset=True)
            else:
                body = ReviewRequest.model_validate(parsed).model_dump(mode="json", exclude_unset=True)
            key = require_id(request.headers.get("Idempotency-Key", ""))
            identity = command_identity(
                key=key,
                actor_ref=session.actor.id,
                operation=operation,
                target=episode_id,
                parameters_json=canonical_json({}),
                body_fingerprint=api.fingerprint.fingerprint(canonical_json(body)),
                fingerprint_kind="hmac_sha256",
            )
            with api.uows.begin(actor=session.actor, command=identity, correlation_id=key) as uow:
                if operation == "actOnEpisode":
                    ep = api.service.act(uow, session, episode_id, body, _version(request), identity)
                    headers = {"ETag": '"' + str(ep.revision) + '"', "Config-Release": uow.release.release_id}
                else:
                    result = api.service.review(uow, session, episode_id, body, _version(request), identity)
                    ReviewResponse.model_validate(result)
                    if result["role"] != "acting_medical_officer":
                        ClinicalReview.model_validate(
                            {k: v for k, v in result.items() if k in ClinicalReview.model_fields}
                        )
                    headers = {
                        "Location": "/v1/episodes/" + episode_id + "/reviews/" + text(result["review_id"]),
                        "Config-Release": uow.release.release_id,
                    }
            if operation == "actOnEpisode":
                if api.snapshots is None:
                    raise ProblemError(
                        ErrorCode.INTERNAL_ERROR, "snapshot provider unavailable", retryable=True
                    )
                with api.snapshots.snapshot() as view:
                    result = _base_episode(
                        api.queries.for_session(
                            view,
                            session,
                            episode_id,
                            extended="application/vnd.crew-health.episode+json"
                            in request.headers.get("accept", ""),
                        ),
                        extended="application/vnd.crew-health.episode+json"
                        in request.headers.get("accept", ""),
                    )
                    headers["ETag"] = '"' + str(result["revision"]) + '"'
            if (
                operation == "recordReview"
                and result.get("role") != "acting_medical_officer"
                and "application/vnd.crew-health.episode+json" not in request.headers.get("accept", "")
            ):
                result = {k: v for k, v in result.items() if k in ClinicalReview.model_fields}
            return _response(result, 200 if operation == "actOnEpisode" else 201, headers)

        return wrap(request, run)

    @router.post("/v1/episodes/{episode_id}/actions", operation_id="actOnEpisode")
    async def act_episode(request: Request, episode_id: str) -> Response:
        return await command(request, episode_id, "actOnEpisode")

    @router.post("/v1/episodes/{episode_id}/reviews", operation_id="recordReview")
    async def record_review(request: Request, episode_id: str) -> Response:
        return await command(request, episode_id, "recordReview")

    return router
