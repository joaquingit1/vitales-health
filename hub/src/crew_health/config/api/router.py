from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.ports import CommandExecution, ConfigReleaseRepository
from crew_health.config.domain.checks import parse_document
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.contracts_gen.schemas.config_document_schema import ConfigDocument
from crew_health.contracts_gen.schemas.config_index_schema import ConfigIndex
from crew_health.contracts_gen.schemas.config_release_schema import ConfigRelease
from crew_health.contracts_gen.schemas.requests_schema import ConfigReleaseCreate
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWork


@dataclass(frozen=True, slots=True)
class ConfigHttpResult:
    body: bytes
    etag: str
    release_id: str
    status: int


class ConfigHttpCommands:
    def __init__(
        self,
        *,
        execute: CommandExecution,
        service: ReleaseActivationService,
        repository: ConfigReleaseRepository,
        ids: IdFactory,
    ) -> None:
        self.execute, self.service, self.repository, self.ids = execute, service, repository, ids

    def propose(
        self, request: Request, session: SessionContext, body: ConfigReleaseCreate
    ) -> ConfigHttpResult:
        def apply(uow: UnitOfWork) -> StoredResult:
            self._release_header(request, uow)
            active = {name: uow.release.document(name) for name in ConfigName}
            names: set[ConfigName] = set()
            for item in body.documents:
                name = ConfigName(item.name.root)
                if name in names:
                    raise ProblemError(ErrorCode.MALFORMED_REQUEST, "duplicate configuration document")
                names.add(name)
                raw = canonical_json(item.document)
                active[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
            release_id = self.ids.new("release")
            self.service.propose(uow, session, CandidateRelease(release_id, active), note=body.note)
            return self._result(identity, uow, release_id, 201)

        identity = self._identity(
            request, session, "createConfigRelease", "/v1/config/releases", body.model_dump(mode="json")
        )
        result = self.execute.execute(session, identity, apply)
        return self._response(result)

    def activate(
        self, request: Request, session: SessionContext, release_id: str, expected_etag: str
    ) -> ConfigHttpResult:
        def apply(uow: UnitOfWork) -> StoredResult:
            self._release_header(request, uow)
            self.service.activate(uow, session, release_id=release_id, expected_etag=expected_etag)
            return self._result(identity, uow, release_id, 200)

        identity = self._identity(
            request,
            session,
            "activateConfigRelease",
            "/v1/config/releases/" + require_id(release_id) + "/activate",
            None,
        )
        result = self.execute.execute(session, identity, apply)
        return self._response(result)

    @staticmethod
    def _release_header(request: Request, uow: UnitOfWork) -> None:
        rendered = request.headers.get("Config-Release")
        if rendered is not None and rendered != uow.release.release_id:
            raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, "configuration release mismatch")

    @staticmethod
    def _identity(
        request: Request, session: SessionContext, operation: str, target: str, body: object
    ) -> CommandIdentity:
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 128:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "idempotency key required")
        return command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation=operation,
            target=target,
            parameters_json=canonical_json(
                {
                    "If-Match": request.headers.get("If-Match"),
                    "Config-Release": request.headers.get("Config-Release"),
                }
            ),
            body_fingerprint=hashlib.sha256(canonical_json(body)).hexdigest(),
            fingerprint_kind="sha256",
        )

    def _result(
        self, identity: CommandIdentity, uow: UnitOfWork, release_id: str, status: int
    ) -> StoredResult:
        """The receipt names the canonical command target of the identity (SYSTEM 5.8), never the raw URL."""
        body = canonical_json(self.repository.wire(uow, release_id))
        ConfigRelease.model_validate_json(body)
        receipt = AcceptedReceipt(
            identity.key, identity.operation, identity.target, self.ids.new("receipt"), uow.now
        )
        # Configuration is P0/P1. The ledger may retain this validated public response.
        return StoredResult(receipt, status, release_id, self.repository.revision(uow, release_id).etag, body)

    def _response(self, result: StoredResult) -> ConfigHttpResult:
        if result.safe_body is None or result.resource_ref is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "configuration response unavailable")
        body = ConfigRelease.model_validate_json(result.safe_body)
        # The replay response remains the accepted body; the current ETag comes from a detail read.
        return ConfigHttpResult(result.safe_body, result.result_code, body.release_id.root, result.status)


def _wire_response(body: bytes, *, release_id: str, etag: str | None = None, status: int = 200) -> Response:
    headers = {"Config-Release": release_id}
    if etag:
        headers["ETag"] = etag
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def create_router(
    *,
    snapshots: ReadSnapshotFactory,
    repository: ConfigReleaseRepository,
    authenticate: Callable[[Request], SessionContext],
    commands: ConfigHttpCommands,
    basis_summaries: Mapping[ConfigName, str],
    model_release_id: Callable[[], str | None],
) -> APIRouter:
    if set(basis_summaries) != set(ConfigName) or any(not value for value in basis_summaries.values()):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "configuration metadata incomplete")
    router = APIRouter()

    @router.get("/v1/config/releases", operation_id="listConfigReleases")
    def list_releases(request: Request) -> Response:
        session = authenticate(request)
        if not session.roles & {Role.EDITOR, Role.OPERATOR}:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "release listing role required")
        try:
            limit = int(request.query_params.get("limit", "50"))
            if not 1 <= limit <= 200:
                raise ValueError("invalid page limit")
            cursor = request.query_params.get("cursor")
            after = base64.urlsafe_b64decode(cursor.encode()).decode() if cursor is not None else None
            if after is not None:
                require_id(after)
        except (ValueError, UnicodeError) as exc:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid configuration page") from exc
        with snapshots.snapshot() as view:
            scanned_ids = repository.list_ids(view, after=after, limit=limit + 1)
            ids = scanned_ids[:limit]
            has_more = len(scanned_ids) > limit
            next_cursor = base64.urlsafe_b64encode(ids[-1].encode()).decode() if has_more else None
            body = canonical_json(
                {
                    "items": [repository.wire(view, identifier) for identifier in ids],
                    "page": {"next_cursor": next_cursor, "has_more": has_more},
                }
            )
            return _wire_response(body, release_id=view.release.release_id)

    @router.post("/v1/config/releases", operation_id="createConfigRelease", status_code=201)
    async def propose(request: Request) -> Response:
        session = authenticate(request)
        try:
            body = ConfigReleaseCreate.model_validate_json(await request.body())
        except ValidationError as exc:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid configuration proposal") from exc
        result = commands.propose(request, session, body)
        return _command_response(result, repository, snapshots)

    @router.get("/v1/config/releases/{release_id}", operation_id="getConfigRelease")
    def get_release(request: Request, release_id: str) -> Response:
        session = authenticate(request)
        if not session.roles & {Role.EDITOR, Role.OPERATOR}:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "release detail role required")
        with snapshots.snapshot() as view:
            raw = canonical_json(repository.wire(view, require_id(release_id)))
            ConfigRelease.model_validate_json(raw)
            return _wire_response(
                raw, release_id=view.release.release_id, etag=repository.revision(view, release_id).etag
            )

    @router.post("/v1/config/releases/{release_id}/activate", operation_id="activateConfigRelease")
    def activate(request: Request, release_id: str) -> Response:
        session = authenticate(request)
        result = commands.activate(request, session, release_id, request.headers.get("If-Match", ""))
        return _command_response(result, repository, snapshots)

    @router.get("/v1/config", operation_id="getConfigIndex")
    def get_index(request: Request) -> Response:
        authenticate(request)
        with snapshots.snapshot() as view:
            active_id = repository.active_id(view)
            release = repository.release(view, active_id)
            detail = repository.wire(view, active_id)
            documents = [
                {
                    "name": name.value,
                    "version": release.version_of(name),
                    "schema": f"config-{name.value}.schema.json",
                    "href": f"/v1/config/{name.value}",
                    "basis_summary": basis_summaries[name],
                    "bytes": len(release.document(name).raw),
                }
                for name in ConfigName
            ]
            body = canonical_json(
                {
                    "release_id": active_id,
                    "activated_at": detail["activated_at"],
                    "documents": documents,
                    "model_release_id": model_release_id(),
                }
            )
            ConfigIndex.model_validate_json(body)
            return _wire_response(body, release_id=active_id, etag=repository.revision(view, active_id).etag)

    @router.get("/v1/config/{name}", operation_id="getConfigDocument")
    def get_document(request: Request, name: ConfigName) -> Response:
        authenticate(request)
        with snapshots.snapshot() as view:
            active_id = repository.active_id(view)
            document = repository.release(view, active_id).document(name)
            etag = '"' + document.version + '"'
            if request.headers.get("If-None-Match") in {etag, document.version}:
                return _wire_response(b"", release_id=active_id, etag=etag, status=304)
            body = canonical_json(
                {
                    "name": name.value,
                    "version": document.version,
                    "schema": f"config-{name.value}.schema.json",
                    "release_id": active_id,
                    "loaded_at": repository.loaded_at(view, name, document.version),
                    "document": parse_document(document.raw),
                }
            )
            ConfigDocument.model_validate_json(body)
            return _wire_response(body, release_id=active_id, etag=etag)

    return router


def _command_response(
    result: ConfigHttpResult, repository: ConfigReleaseRepository, snapshots: ReadSnapshotFactory
) -> Response:
    response = _wire_response(
        result.body, release_id=result.release_id, etag=result.etag, status=result.status
    )
    if result.status == 201:
        response.headers["Location"] = "/v1/config/releases/" + result.release_id
    return response
