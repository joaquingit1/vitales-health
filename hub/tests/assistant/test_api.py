from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from crew_health.assistant.api.router import AssistantApi, build_router
from crew_health.assistant.application.http_ports import CommandRequest, HttpResult, QueryParameters
from crew_health.kernel.uow import Actor, SessionContext

ROOT = Path(__file__).resolve().parents[3]
SESSION = SessionContext("session_own", "crew", Actor("person", "person_actor"), "person_owner", frozenset())


def fixture(name: str) -> dict[str, object]:
    value: dict[str, object] = json.loads(
        (ROOT / "contracts/api/examples" / name).read_text(encoding="utf-8")
    )
    return cast(dict[str, object], value["value"])


class Fingerprint:
    def fingerprint(self, canonical_body: bytes) -> str:
        return hmac.new(b"component-test-key", canonical_body, hashlib.sha256).hexdigest()


class Facade:
    def __init__(self) -> None:
        self.calls: list[tuple[SessionContext, str, str | None, object]] = []
        self.result = HttpResult(
            fixture("assistant/status-stopped.json"), 200, {"Config-Release": "config_A"}
        )
        self.failure: Exception | None = None

    def query(
        self, session: SessionContext, operation: str, target: str | None, parameters: QueryParameters
    ) -> HttpResult:
        self.calls.append((session, operation, target, parameters))
        if self.failure:
            raise self.failure
        return self.result

    def command(
        self, session: SessionContext, operation: str, target: str | None, request: CommandRequest
    ) -> HttpResult:
        self.calls.append((session, operation, target, request))
        if self.failure:
            raise self.failure
        return self.result

    def stream(self, session: SessionContext, conversation_id: str) -> Iterator[bytes]:
        self.calls.append((session, "streamConversation", conversation_id, None))
        if self.failure:
            raise self.failure
        return iter([b'data: {"component":"verified"}\n\n'])


def client(facade: Facade | None, *, authenticated: bool = True, fingerprint: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def session_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if authenticated:
            request.state.session = SESSION
        return await call_next(request)

    app.include_router(build_router(AssistantApi(facade, None, Fingerprint() if fingerprint else None, 4096)))
    return TestClient(app)


CASES = [
    ("GET", "/v1/assistant/status", "getAssistantStatus", None, None, "assistant/status-stopped.json", 200),
    (
        "POST",
        "/v1/assistant/runtime",
        "setAssistantRuntime",
        "assistant.runtime",
        {"action": "stop"},
        "assistant/status-stopped.json",
        200,
    ),
    (
        "POST",
        "/v1/assistant/conversations",
        "openConversation",
        "assistant.conversations",
        {"context": {"kind": "general"}},
        "assistant/conversation-new.json",
        201,
    ),
    (
        "GET",
        "/v1/assistant/conversations/conversation_A",
        "getConversation",
        "conversation_A",
        None,
        "assistant/conversation.json",
        200,
    ),
    (
        "POST",
        "/v1/assistant/conversations/conversation_A/messages",
        "sendMessage",
        "conversation_A",
        {"text": "boundary canary"},
        "assistant/message.json",
        202,
    ),
    (
        "POST",
        "/v1/assistant/conversations/conversation_A/cancel",
        "cancelTurn",
        "conversation_A",
        None,
        "assistant/conversation.json",
        200,
    ),
    (
        "POST",
        "/v1/episodes/episode_A/assessments",
        "startAssessment",
        "episode_A",
        {"state_snapshot_version": 3},
        "clinical/job-started.json",
        202,
    ),
    (
        "GET",
        "/v1/assessments/assessment_A",
        "getAssessment",
        "assessment_A",
        None,
        "clinical/assessment.json",
        200,
    ),
    (
        "GET",
        "/v1/insights?cursor=cursor_A&limit=7",
        "listInsights",
        None,
        None,
        "assistant/insights.json",
        200,
    ),
    ("GET", "/v1/insights/insight_A", "getInsight", "insight_A", None, "assistant/insight-sleep.json", 200),
    (
        "POST",
        "/v1/insights/insight_A/actions",
        "actOnInsight",
        "insight_A",
        {"action": "dismiss"},
        "assistant/insight-dismissed.json",
        200,
    ),
]


@pytest.mark.trace("R17", "R28", "V27")
@pytest.mark.parametrize("method,path,operation,target,body,example,status", CASES)
def test_route_dispatch(
    method: str,
    path: str,
    operation: str,
    target: str | None,
    body: dict[str, object] | None,
    example: str,
    status: int,
) -> None:
    facade = Facade()
    facade.result = HttpResult(fixture(example), status, {"ETag": '"3"', "Config-Release": "config_A"})
    response = client(facade).request(
        method,
        path,
        json=body,
        headers={"Idempotency-Key": "key_A", "Config-Release": "config_A", "If-Match": '"3"'},
    )
    assert response.status_code == status, response.text
    assert response.json() == fixture(example)
    assert response.headers["etag"] == '"3"'
    session, actual, resource, data = facade.calls[-1]
    assert session is SESSION and actual == operation and resource == target
    if method == "POST":
        assert isinstance(data, CommandRequest)
        assert data.config_release_id == "config_A" and data.expected_revision == 3
        assert data.identity.actor_ref == "person_actor" and data.identity.target == target
        assert data.identity.fingerprint_kind == "hmac_sha256"
        assert data.body == (body or {})
    elif operation == "listInsights":
        assert data == QueryParameters("cursor_A", 7)


@pytest.mark.trace("R17", "R28", "V27")
@pytest.mark.parametrize(
    "raw",
    [
        b'{"text":"a","text":"b"}',
        b'{"text":"a","extra":1}',
        b'{"text":NaN}',
        b'{"text":2}',
        b"{}",
        b'{"text":"a","reply_to_action":{"message_id":"message_A","choice":"yes"}}',
    ],
)
def test_invalid_message_never_dispatches(raw: bytes) -> None:
    facade = Facade()
    response = client(facade).post(
        "/v1/assistant/conversations/conversation_A/messages",
        content=raw,
        headers={"Content-Type": "application/json", "Idempotency-Key": "key_A"},
    )
    assert response.status_code == 400 and not facade.calls


@pytest.mark.trace("R17", "R28", "V27")
def test_boundary_failures_and_sse() -> None:
    facade = Facade()
    assert client(facade, authenticated=False).get("/v1/assistant/status").status_code == 401
    assert client(None).get("/v1/assistant/status").status_code == 422
    assert (
        client(facade, fingerprint=False)
        .post("/v1/assistant/runtime", json={"action": "stop"}, headers={"Idempotency-Key": "key_A"})
        .status_code
        == 422
    )
    response = client(facade).get("/v1/assistant/conversations/conversation_A/stream")
    assert response.status_code == 200 and response.content == b'data: {"component":"verified"}\n\n'
    assert response.headers["cache-control"] == "no-store"
    facade.failure = RuntimeError("PRIVATE secret canary")
    response = client(facade).get("/v1/assistant/status")
    assert response.status_code == 500 and "PRIVATE" not in response.text
    facade.failure = None
    facade.result = HttpResult({}, 200)
    assert client(facade).get("/v1/assistant/status").status_code == 500


@pytest.mark.trace("R17", "R28", "V27")
def test_retry_identity_preserves_original_pin_and_target() -> None:
    facade = Facade()
    facade.result = HttpResult(fixture("assistant/message.json"), 202)
    http = client(facade)
    for target, pin, text in [
        ("conversation_A", "config_A", "one"),
        ("conversation_A", "config_A", "one"),
        ("conversation_B", "config_A", "one"),
        ("conversation_A", "config_B", "one"),
        ("conversation_A", "config_A", "two"),
    ]:
        assert (
            http.post(
                f"/v1/assistant/conversations/{target}/messages",
                json={"text": text},
                headers={"Idempotency-Key": "key_A", "Config-Release": pin},
            ).status_code
            == 202
        )
    commands = [call[3] for call in facade.calls]
    assert all(isinstance(item, CommandRequest) for item in commands)
    a, b, c, d, e = commands
    assert (
        isinstance(a, CommandRequest)
        and isinstance(b, CommandRequest)
        and isinstance(c, CommandRequest)
        and isinstance(d, CommandRequest)
        and isinstance(e, CommandRequest)
    )
    assert a.identity == b.identity
    assert a.identity.target != c.identity.target
    assert a.identity.parameters_sha256 != d.identity.parameters_sha256
    assert a.identity.body_fingerprint != e.identity.body_fingerprint


@pytest.mark.trace("R17", "R28", "V27")
@pytest.mark.parametrize(
    "headers",
    [
        {"If-Match": 'W/"3"'},
        {"If-Match": "-1"},
        {"If-Match": '"3'},
        {"If-Match": "9999999999999999"},
        {"Config-Release": "bad pin"},
        {"Idempotency-Key": ""},
    ],
)
def test_invalid_identity_headers_refused(headers: dict[str, str]) -> None:
    facade = Facade()
    response = client(facade).post(
        "/v1/assistant/runtime", json={"action": "stop"}, headers={"Idempotency-Key": "key_A", **headers}
    )
    assert response.status_code == 400 and not facade.calls


@pytest.mark.trace("R17", "R28", "V27")
def test_cancel_body_duplicate_headers_size_depth_and_content_type() -> None:
    facade = Facade()
    http = client(facade)
    assert (
        http.post(
            "/v1/assistant/conversations/conversation_A/cancel", json={}, headers={"Idempotency-Key": "key_A"}
        ).status_code
        == 400
    )
    assert (
        http.post(
            "/v1/assistant/runtime",
            content=b'{"action":"stop"}',
            headers={"Content-Type": "text/plain", "Idempotency-Key": "key_A"},
        ).status_code
        == 415
    )
    assert (
        http.post(
            "/v1/assistant/runtime",
            json={"action": "stop"},
            headers=[
                ("Idempotency-Key", "key_A"),
                ("Config-Release", "config_A"),
                ("Config-Release", "config_B"),
            ],
        ).status_code
        == 400
    )
    assert http.post("/v1/assistant/runtime", content=b"x" * 4097).status_code == 413
    assert (
        http.post(
            "/v1/assistant/runtime",
            content=b'{"action":' + b"[" * 70 + b"0" + b"]" * 70 + b"}",
            headers={"Content-Type": "application/json", "Idempotency-Key": "key_A"},
        ).status_code
        == 400
    )
    assert not facade.calls


@pytest.mark.trace("R17", "R28", "V27")
def test_facade_value_error_is_sanitized_internal_not_bad_request() -> None:
    facade = Facade()
    facade.failure = ValueError("PRIVATE provider details")
    response = client(facade).get("/v1/assistant/status")
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "PRIVATE" not in response.text


@pytest.mark.trace("R17", "R28", "V27")
def test_response_status_and_unknown_fields_refused_without_rewriting() -> None:
    facade = Facade()
    facade.result = HttpResult(fixture("assistant/status-stopped.json"), 201)
    assert client(facade).get("/v1/assistant/status").status_code == 500
    facade.result = HttpResult({**fixture("assistant/status-stopped.json"), "extra": "PRIVATE"}, 200)
    response = client(facade).get("/v1/assistant/status")
    assert response.status_code == 500 and "PRIVATE" not in response.text


@pytest.mark.trace("R17", "V27")
def test_synchronous_facade_never_runs_on_asgi_event_loop() -> None:
    import asyncio

    class ThreadFacade(Facade):
        def query(
            self, session: SessionContext, operation: str, target: str | None, parameters: QueryParameters
        ) -> HttpResult:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return super().query(session, operation, target, parameters)
            raise AssertionError("sync facade is blocking the ASGI loop")

    assert client(ThreadFacade()).get("/v1/assistant/status").status_code == 200
