from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from email.message import Message as MimeMessage
from pathlib import Path
from typing import Literal, cast

import yaml
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crew_health.app.errors import to_envelope
from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import SessionContext


def _error(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _error(ErrorCode.CONFIG_INVALID)
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    method: str
    path: str
    security: Literal["sessionToken", "adapterToken"] | None
    roles: frozenset[Role]
    mutation: bool
    ledger: bool
    content_types: frozenset[str]
    body_required: bool
    pattern: re.Pattern[str]


class OperationCatalog:
    def __init__(self, operations: tuple[Operation, ...], *, maximum: int) -> None:
        if not 1 <= len(operations) <= maximum or len({op.operation_id for op in operations}) != len(
            operations
        ):
            raise _error(ErrorCode.CONFIG_INVALID)
        if len({(op.method, op.path) for op in operations}) != len(operations):
            raise _error(ErrorCode.CONFIG_INVALID)
        self.operations = operations

    @classmethod
    def load(
        cls, openapi_path: Path, *, expected_sha256: str | None = None, max_operations: int = 1024
    ) -> OperationCatalog:
        try:
            raw = openapi_path.read_bytes()
            expected = expected_sha256 or cls._guarded_hash(openapi_path)
            if hashlib.sha256(raw).hexdigest() != expected or not 1 <= max_operations <= 1024:
                raise _error(ErrorCode.CONFIG_INVALID)
            source = _mapping(yaml.safe_load(raw))
            components = _mapping(source.get("components"))
            schemes = _mapping(components.get("securitySchemes"))
            for key in ("sessionToken", "adapterToken"):
                scheme = _mapping(schemes.get(key))
                if scheme.get("type") != "http" or scheme.get("scheme") != "bearer":
                    raise _error(ErrorCode.CONFIG_INVALID)
            root_security = source.get("security")
            operations: list[Operation] = []
            for path, item in sorted(_mapping(source.get("paths")).items()):
                for method, raw_operation in sorted(_mapping(item).items()):
                    if method not in ("get", "post", "put", "patch", "delete", "head", "options", "trace"):
                        continue
                    operations.append(cls._operation(path, method, _mapping(raw_operation), root_security))
                    if len(operations) > max_operations:
                        raise _error(ErrorCode.CONFIG_INVALID)
            return cls(tuple(operations), maximum=max_operations)
        except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError) as error:
            raise _error(ErrorCode.CONFIG_INVALID) from error

    @staticmethod
    def _guarded_hash(path: Path) -> str:
        root = path.parent.parent
        relative = path.relative_to(root).as_posix()
        for line in root.joinpath("SHA256SUMS").read_text(encoding="utf-8").splitlines():
            pieces = line.split("  ", 1)
            if len(pieces) == 2 and pieces[1] == relative:
                return pieces[0]
        raise _error(ErrorCode.CONFIG_INVALID)

    @staticmethod
    def _security(value: object) -> Literal["sessionToken", "adapterToken"] | None:
        if value == []:
            return None
        if not isinstance(value, list) or len(value) != 1:
            raise _error(ErrorCode.CONFIG_INVALID)
        item = _mapping(value[0])
        if item == {"sessionToken": []}:
            return "sessionToken"
        if item == {"adapterToken": []}:
            return "adapterToken"
        raise _error(ErrorCode.CONFIG_INVALID)

    @classmethod
    def _operation(cls, path: str, method: str, value: dict[str, object], root: object) -> Operation:
        operation_id = value.get("operationId")
        raw_roles = value.get("x-roles")
        if (
            not isinstance(operation_id, str)
            or not isinstance(raw_roles, list)
            or not path.startswith("/v1/")
        ):
            raise _error(ErrorCode.CONFIG_INVALID)
        roles = frozenset(Role(role) for role in raw_roles if isinstance(role, str))
        if len(roles) != len(raw_roles):
            raise _error(ErrorCode.CONFIG_INVALID)
        security = cls._security(value["security"] if "security" in value else root)
        if security is None and roles:
            raise _error(ErrorCode.CONFIG_INVALID)
        if security is not None and not roles:
            raise _error(ErrorCode.CONFIG_INVALID)
        exceptions = {
            "createSession": ("post", "/v1/sessions"),
            "kitMapWhatIf": ("post", "/v1/kit-map/what-if"),
        }
        if operation_id in exceptions and exceptions[operation_id] != (method, path):
            raise _error(ErrorCode.CONFIG_INVALID)
        mutation = method in ("post", "put", "patch", "delete")
        body = _mapping(value["requestBody"]) if "requestBody" in value else {}
        content = _mapping(body["content"]) if "content" in body else {}
        content_types = frozenset(content)
        if not content_types <= {"application/json", "multipart/form-data"}:
            raise _error(ErrorCode.CONFIG_INVALID)
        if "multipart/form-data" in content_types and operation_id != "uploadReport":
            raise _error(ErrorCode.CONFIG_INVALID)
        pattern = re.escape(path)
        pattern = re.sub(r"\\\{[A-Za-z_][A-Za-z0-9_]*\\\}", "([^/]+)", pattern)
        return Operation(
            operation_id,
            method.upper(),
            path,
            security,
            roles,
            mutation,
            mutation and operation_id not in exceptions,
            content_types,
            body.get("required") is True,
            re.compile("^" + pattern + "$"),
        )

    def match(self, method: str, path: str) -> Operation | None:
        for operation in self.operations:
            if operation.method == method.upper() and operation.pattern.fullmatch(path):
                return operation
        return None

    def requires_ledger(self, operation_id: str) -> bool:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation.ledger
        raise _error(ErrorCode.CONFIG_INVALID)


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> Decimal:
    raise ValueError("nonfinite JSON number")


def parse_json(raw: bytes, *, max_bytes: int, max_depth: int) -> dict[str, object]:
    if len(raw) > max_bytes:
        raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
    try:
        value: object = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
        if not isinstance(value, dict):
            raise ValueError("JSON root must be an object")
        queue: list[tuple[object, int]] = [(value, 0)]
        for _ in range(max_bytes + 1):
            if not queue:
                return cast(dict[str, object], value)
            item, depth = queue.pop()
            if depth > max_depth:
                raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
            if isinstance(item, dict):
                queue.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                queue.extend((child, depth + 1) for child in item)
            elif isinstance(item, Decimal) and not item.is_finite():
                raise ValueError("nonfinite JSON number")
        raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _error(ErrorCode.MALFORMED_REQUEST) from error


@dataclass(frozen=True, slots=True)
class _Chunk:
    raw: bytes


def _decimal_token(value: Decimal) -> bytes:
    if not value.is_finite():
        raise _error(ErrorCode.MALFORMED_REQUEST)
    approximated = float(value)
    if math.isfinite(approximated) and Decimal(repr(approximated)) == value:
        return canonical_json(approximated)
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise _error(ErrorCode.MALFORMED_REQUEST)
    coefficient = "".join(str(digit) for digit in digits).rstrip("0")
    if not coefficient:
        return b"0"
    exponent += len(digits) - len(coefficient)
    mantissa = coefficient[0] + ("." + coefficient[1:] if len(coefficient) > 1 else "")
    return (("-" if sign else "") + mantissa + "e" + str(exponent + len(coefficient) - 1)).encode("ascii")


def canonical_body(value: object, *, max_bytes: int, max_depth: int) -> bytes:
    """RFC 8785 for I-JSON plus exact Decimal tokens for finer clinical quantities."""
    queue: list[tuple[object, int]] = [(value, 0)]
    chunks: list[bytes] = []
    size = 0
    for _ in range(max_bytes * 4 + 1):
        if not queue:
            return b"".join(chunks)
        item, depth = queue.pop()
        if depth > max_depth:
            raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
        if isinstance(item, _Chunk):
            raw = item.raw
        elif isinstance(item, Decimal):
            raw = _decimal_token(item)
        elif isinstance(item, list):
            queue.append((_Chunk(b"]"), depth))
            for index in range(len(item) - 1, -1, -1):
                queue.append((item[index], depth + 1))
                if index:
                    queue.append((_Chunk(b","), depth))
            raw = b"["
        elif isinstance(item, dict):
            keys = sorted(item, key=lambda key: str(key).encode("utf-16-be"))
            queue.append((_Chunk(b"}"), depth))
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                if not isinstance(key, str):
                    raise _error(ErrorCode.MALFORMED_REQUEST)
                queue.extend(((item[key], depth + 1), (_Chunk(b":"), depth), (key, depth + 1)))
                if index:
                    queue.append((_Chunk(b","), depth))
            raw = b"{"
        else:
            raw = canonical_json(item)
        size += len(raw)
        if size > max_bytes:
            raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
        chunks.append(raw)
    raise _error(ErrorCode.PAYLOAD_TOO_LARGE)


class HubMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        catalog: OperationCatalog,
        settings: HubSettings,
        ids: IdFactory,
        authenticate: Callable[[Request], SessionContext],
        authorize: Callable[[SessionContext, str], None],
        allow_mutations: Callable[[], bool] = lambda: True,
        fingerprint: Callable[[Operation, bytes], str | None] | None = None,
        allow_request: Callable[[Operation], bool] | None = None,
    ) -> None:
        self.app, self.catalog, self.settings, self.ids = app, catalog, settings, ids
        self.authenticate, self.authorize, self.allow_mutations = authenticate, authorize, allow_mutations
        self.fingerprint, self.allow_request = fingerprint, allow_request
        self.timeout = settings.integer("workers.hub.timeout_ms") / 1000
        if self.timeout <= 0:
            raise _error(ErrorCode.CONFIG_INVALID)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = self.ids.new("correlation")
        started = False

        async def output(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-correlation-id"
                ]
                message = {
                    **message,
                    "headers": headers + [(b"x-correlation-id", correlation_id.encode("ascii"))],
                }
            await asyncio.wait_for(send(message), timeout=self.timeout)

        try:
            headers = self._headers(scope)
            correlation_id = require_id(headers.get("x-correlation-id", correlation_id))
            await self._dispatch(scope, receive, output, correlation_id, headers)
        except Exception as error:
            if started:
                raise _error(ErrorCode.INTERNAL_ERROR) from None
            problem = error if isinstance(error, ProblemError) else _error(ErrorCode.INTERNAL_ERROR)
            raw_error = to_envelope(problem, correlation_id).model_dump_json().encode()
            await output(
                {
                    "type": "http.response.start",
                    "status": problem.status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await output({"type": "http.response.body", "body": raw_error, "more_body": False})

    async def _dispatch(
        self, scope: Scope, receive: Receive, output: Send, correlation_id: str, headers: Mapping[str, str]
    ) -> None:
        operation = self.catalog.match(str(scope["method"]), str(scope["path"]))
        if operation is None:
            raise _error(ErrorCode.NOT_FOUND)
        state = scope.setdefault("state", {})
        state.update(operation_id=operation.operation_id, correlation_id=correlation_id, session=None)
        self._authorize(scope, receive, operation)
        if self.allow_request is not None and not self.allow_request(operation):
            raise _error(ErrorCode.INTERNAL_ERROR)
        if operation.mutation and not self.allow_mutations():
            raise _error(ErrorCode.INTERNAL_ERROR)
        if operation.ledger:
            require_id(headers.get("idempotency-key", ""))
        guarded = self._receive(receive, operation, headers)
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() == "multipart/form-data":
            await self.app(scope, guarded, output)
        else:
            raw = await asyncio.wait_for(self._body(guarded), timeout=self.timeout)
            if raw:
                state["parsed_json"] = parse_json(
                    raw,
                    max_bytes=self.settings.integer("commands.max_bytes"),
                    max_depth=self.settings.integer("commands.max_depth"),
                )
            elif operation.body_required:
                raise _error(ErrorCode.MALFORMED_REQUEST)
            state["canonical_body"] = (
                canonical_body(
                    state["parsed_json"],
                    max_bytes=self.settings.integer("commands.max_bytes"),
                    max_depth=self.settings.integer("commands.max_depth"),
                )
                if raw
                else b""
            )
            state["body_fingerprint"] = (
                None if self.fingerprint is None else self.fingerprint(operation, state["canonical_body"])
            )
            replayed = False

            async def replay() -> Message:
                nonlocal replayed
                if replayed:
                    return await guarded()
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}

            await self.app(scope, replay, output)

    def _authorize(self, scope: Scope, receive: Receive, operation: Operation) -> None:
        if operation.security is None:
            return
        session = self.authenticate(Request(scope, receive=receive))
        if not session.roles.intersection(operation.roles):
            raise _error(ErrorCode.UNAUTHORIZED_SCOPE)
        if operation.security == "adapterToken" and session.kind != "adapter":
            raise _error(ErrorCode.ADAPTER_NOT_TRUSTED)
        self.authorize(session, operation.operation_id)
        scope["state"]["session"] = session

    @staticmethod
    def _headers(scope: Scope) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in scope["headers"]:
            name = key.decode("ascii").lower()
            if name in headers and name in {
                "authorization",
                "content-type",
                "content-length",
                "idempotency-key",
                "x-correlation-id",
                "config-release",
            }:
                raise _error(ErrorCode.MALFORMED_REQUEST)
            headers[name] = value.decode("latin-1")
        return headers

    def _receive(self, receive: Receive, operation: Operation, headers: Mapping[str, str]) -> Receive:
        mime = MimeMessage()
        mime["content-type"] = headers.get("content-type", "")
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in operation.content_types:
            raise _error(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
        if operation.body_required and content_type not in operation.content_types:
            raise _error(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
        limit = self.settings.integer("commands.max_bytes")
        if content_type == "multipart/form-data":
            boundary = mime.get_param("boundary")
            if (
                not isinstance(boundary, str)
                or re.fullmatch(r"[0-9A-Za-z'()+_,./:=?-]{1,70}", boundary) is None
            ):
                raise _error(ErrorCode.MALFORMED_REQUEST)
            limit = self.settings.integer("private.max_report_bytes") + self.settings.integer(
                "commands.multipart_overhead_bytes"
            )
        if "content-length" in headers:
            try:
                declared = int(headers["content-length"])
            except ValueError as error:
                raise _error(ErrorCode.MALFORMED_REQUEST) from error
            if declared < 0:
                raise _error(ErrorCode.MALFORMED_REQUEST)
            if declared > limit:
                raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
        size = 0

        async def bounded() -> Message:
            nonlocal size
            message = await asyncio.wait_for(receive(), timeout=self.timeout)
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if not isinstance(body, bytes):
                    raise _error(ErrorCode.MALFORMED_REQUEST)
                size += len(body)
                if size > limit:
                    raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
                if body and content_type not in operation.content_types:
                    raise _error(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
            return message

        return bounded

    async def _body(self, receive: Receive) -> bytes:
        chunks: list[bytes] = []
        for _ in range(self.settings.integer("commands.max_bytes") + 1):
            message = await receive()
            if message["type"] != "http.request":
                raise _error(ErrorCode.MALFORMED_REQUEST)
            body = message.get("body", b"")
            if body:
                chunks.append(body)
            if not message.get("more_body", False):
                return b"".join(chunks)
        raise _error(ErrorCode.PAYLOAD_TOO_LARGE)
