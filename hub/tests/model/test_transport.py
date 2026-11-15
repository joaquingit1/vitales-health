from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import pytest

from crew_health.model.adapters.llama_client import ClientLimits, LlamaClient
from crew_health.model.application.ports import (
    Deadline,
    DecodingSettings,
    JsonValue,
    PromptBundle,
    StepCancelled,
    StepMalformed,
    StepOk,
    StepRequest,
    StepTimedOut,
    StepUnavailable,
    TranscriptItem,
)
from crew_health.model.application.prompt_builder import request_bytes


class TestClock:
    __test__ = False

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def process_epoch(self) -> str:
        return "test_epoch"


class Cancel:
    def __init__(self) -> None:
        self.event = threading.Event()

    def is_cancelled(self) -> bool:
        return self.event.is_set()


class Tokens:
    """Deterministic external token-counter fake; no assertion claims actual model tokens."""

    def count(self, text: str) -> int:
        return len(text)

    def count_request(self, body: Mapping[str, object]) -> int:
        return len(json.dumps(body))


@dataclass
class Wire:
    frames: tuple[bytes, ...]
    delay: float = 0
    status: int = 200
    content_type: str = "text/event-stream"
    seen: dict[str, object] | None = None
    disconnected: threading.Event | None = None


@contextmanager
def server(wire: Wire) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer key_test"
            wire.seen = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(wire.status)
            self.send_header("Content-Type", wire.content_type)
            self.end_headers()
            try:
                for frame in wire.frames:
                    if wire.delay:
                        time.sleep(wire.delay)
                    self.wfile.write(frame)
                    self.wfile.flush()
                if wire.disconnected is not None:
                    self.connection.settimeout(0.5)
                    if self.connection.recv(1) == b"":
                        wire.disconnected.set()
            except BrokenPipeError, ConnectionResetError:
                if wire.disconnected is not None:
                    wire.disconnected.set()

        def log_message(self, format: str, *args: object) -> None:
            pass

    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    listener.daemon_threads = True
    thread = threading.Thread(target=lambda: listener.serve_forever(poll_interval=0.005), daemon=True)
    thread.start()
    try:
        yield listener.server_port
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=1)


def frame(delta: dict[str, object], finish: str | None = None) -> bytes:
    return (
        "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
    ).encode()


def request(*, tools: bool = False) -> StepRequest:
    catalog = (
        (
            {
                "name": "read_one",
                "description": "Read one public test item",
                "input_schema": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
            },
        )
        if tools
        else None
    )
    prepared = StepRequest(
        "model_test",
        "general",
        PromptBundle(
            (TranscriptItem("user", "public test"),),
            cast(tuple[dict[str, JsonValue], ...] | None, catalog),
            "turn-output",
            DecodingSettings(0.2, 0.95, 20, 0, 17, 64, "deepseek"),
            "a" * 64,
        ),
    )

    return replace(
        prepared,
        bundle=replace(
            prepared.bundle,
            prompt_sha256=hashlib.sha256(request_bytes(prepared.bundle, prepared.release_id)).hexdigest(),
        ),
    )


def run(
    wire: Wire, *, tools: bool = False, first: float = 1, total: float = 2, cancel: Cancel | None = None
) -> object:
    with server(wire) as port:
        client = LlamaClient(
            port=port, api_key="key_test", tokens=Tokens(), limits=ClientLimits(65536, 256, 8, 1, 0.01)
        )
        clock = TestClock()
        now = clock.monotonic_ns()
        return client.run_step(
            request(tools=tools),
            deadline=Deadline(now + int(total * 1e9), now + int(first * 1e9), clock),
            cancel=cancel or Cancel(),
            progress=lambda value: None,
        )


@pytest.mark.trace("T-E17-020", "R17", "T-E17-041", "V26")
def test_native_tool_call_fragments_parse_and_hidden_reasoning_never_returns() -> None:
    wire = Wire(
        (
            frame({"reasoning_content": "HIDDEN_CANARY_93"}),
            frame(
                {
                    "tool_calls": [
                        {"index": 0, "id": "call_1", "function": {"name": "read_one", "arguments": '{"co'}}
                    ]
                }
            ),
            frame({"tool_calls": [{"index": 0, "function": {"arguments": 'de":"pulse"}'}}]}, "tool_calls"),
            b"data: [DONE]\n\n",
        )
    )
    result = run(wire, tools=True)
    assert isinstance(result, StepOk)
    assert result.tool_calls[0].arguments == {"code": "pulse"}
    assert result.usage.thinking_tokens == len("HIDDEN_CANARY_93")
    assert "HIDDEN_CANARY_93" not in repr(result)
    assert wire.seen is not None and wire.seen["temperature"] == 0.2
    assert wire.seen["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.trace("T-E17-021", "R17")
def test_final_json_is_parsed_without_text_fallback() -> None:
    result = run(Wire((frame({"content": '{"kind":"stop"}'}, "stop"), b"data: [DONE]\n\n")))
    assert isinstance(result, StepOk) and result.final == {"kind": "stop"}


@pytest.mark.trace("T-E17-020", "R17", "T-E17-041")
@pytest.mark.parametrize(
    "args", ["[]", '{"code":"pulse","scope":"other"}', '{"code":NaN}', '{"code":"a","code":"b"}']
)
def test_malformed_tool_arguments_never_reach_controller(args: str) -> None:
    wire = Wire(
        (
            frame(
                {
                    "tool_calls": [
                        {"index": 0, "id": "call_1", "function": {"name": "read_one", "arguments": args}}
                    ]
                },
                "tool_calls",
            ),
            b"data: [DONE]\n\n",
        )
    )
    assert isinstance(run(wire, tools=True), StepMalformed)


@pytest.mark.trace("T-E17-022", "V27")
def test_first_token_timeout_is_distinct_from_total_timeout() -> None:
    wire = Wire((frame({"content": "{}"}, "stop"), b"data: [DONE]\n\n"), delay=0.15)
    result = run(wire, first=0.03, total=1)
    assert result == StepTimedOut("first_token")
    wire = Wire(
        (frame({"reasoning_content": "first"}), frame({"content": "{}"}, "stop"), b"data: [DONE]\n\n"),
        delay=0.06,
    )
    result = run(wire, first=1, total=0.09)
    assert result == StepTimedOut("total")


@pytest.mark.trace("T-E17-022", "V27")
def test_cancel_closes_real_tcp_stream_promptly() -> None:
    cancel = Cancel()
    closed = threading.Event()
    wire = Wire((b": waiting\n\n",), disconnected=closed)
    timer = threading.Timer(0.04, cancel.event.set)
    timer.start()
    before = time.monotonic()
    try:
        result = run(wire, cancel=cancel)
    finally:
        timer.cancel()
    assert isinstance(result, StepCancelled)
    assert time.monotonic() - before < 0.5
    assert closed.wait(0.5)


@pytest.mark.trace("T-E17-081", "T-E17-082")
@pytest.mark.parametrize(
    "wire",
    [
        Wire((frame({"content": "{}"}, "stop"),)),
        Wire((b"data: garbage\n\n",)),
        Wire((frame({"content": "<think>secret</think>{}"}, "stop"), b"data: [DONE]\n\n")),
        Wire((frame({"content": "{}"}, "length"), b"data: [DONE]\n\n")),
    ],
)
def test_truncated_invalid_or_unseparated_thinking_is_explicit_failure(wire: Wire) -> None:
    assert isinstance(run(wire), StepMalformed)


@pytest.mark.trace("T-E17-081")
def test_http_failure_stays_unavailable() -> None:
    assert isinstance(run(Wire((), status=503)), StepUnavailable)


@pytest.mark.trace("R17", "T-E17-020")
def test_numeric_overflow_in_final_json_is_never_returned_as_infinity() -> None:
    wire = Wire((frame({"content": '{"number":1e309}'}, "stop"), b"data: [DONE]\n\n"))
    assert isinstance(run(wire), StepMalformed)


@pytest.mark.trace("T-E17-021", "R17")
def test_native_final_must_match_owned_output_schema() -> None:
    prepared = request()
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"kind": {"const": "stop"}},
        "required": ["kind"],
        "additionalProperties": False,
    }
    bundle = replace(prepared.bundle, output_validation_schema=schema)
    prepared = replace(
        prepared,
        bundle=replace(
            bundle, prompt_sha256=hashlib.sha256(request_bytes(bundle, prepared.release_id)).hexdigest()
        ),
    )
    with server(Wire((frame({"content": '{"wrong":"shape"}'}, "stop"), b"data: [DONE]\n\n"))) as port:
        client = LlamaClient(
            port=port, api_key="key_test", tokens=Tokens(), limits=ClientLimits(65536, 256, 8, 1, 0.01)
        )
        clock = TestClock()
        now = clock.monotonic_ns()
        result = client.run_step(
            prepared,
            deadline=Deadline(now + 10**9, now + 10**9, clock),
            cancel=Cancel(),
            progress=lambda value: None,
        )
    assert isinstance(result, StepMalformed)


@pytest.mark.trace("V27", "V26", "T-E17-022", "T-E17-041")
def test_chunked_cancel_has_one_response_close_owner_and_no_background_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client
    from collections.abc import Callable

    entered, release = threading.Event(), threading.Event()
    failures: list[BaseException] = []
    original_close = http.client.HTTPConnection.close
    original_response_close = cast(
        Callable[[http.client.HTTPResponse], None], http.client.HTTPResponse.__dict__["_close_conn"]
    )
    original_hook = threading.excepthook

    def response_close(response: http.client.HTTPResponse) -> None:
        if threading.current_thread().name == "model-step" and not entered.is_set():
            entered.set()
            assert release.wait(1)
        original_response_close(response)

    def connection_close(connection: http.client.HTTPConnection) -> None:
        if threading.current_thread() is threading.main_thread():
            assert entered.wait(1)
        original_close(connection)

    def exception_hook(args: threading.ExceptHookArgs) -> None:
        if args.thread is not None and args.thread.name == "model-step":
            if args.exc_value is not None:
                failures.append(args.exc_value)
        else:
            original_hook(args)

    monkeypatch.setattr(http.client.HTTPResponse, "_close_conn", response_close)
    monkeypatch.setattr(http.client.HTTPConnection, "close", connection_close)
    monkeypatch.setattr(threading, "excepthook", exception_hook)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            payload = frame({"reasoning_content": "PRIVATE_CHUNKED_CANARY"})
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()
            self.connection.settimeout(1)
            try:
                self.connection.recv(1)
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.daemon_threads = True
    serving = threading.Thread(target=lambda: endpoint.serve_forever(poll_interval=0.005), daemon=True)
    serving.start()
    cancel = Cancel()

    def progress(value: object) -> None:
        cancel.event.set()

    try:
        clock = TestClock()
        now = clock.monotonic_ns()
        result = LlamaClient(
            port=endpoint.server_port,
            api_key="test_key",
            tokens=Tokens(),
            limits=ClientLimits(65536, 256, 8, 1, 0.01),
        ).run_step(
            request(), deadline=Deadline(now + 10**9, now + 10**9, clock), cancel=cancel, progress=progress
        )
        assert isinstance(result, StepCancelled)
        assert entered.wait(1)
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "model-step":
                thread.join(timeout=1)
        endpoint.shutdown()
        endpoint.server_close()
        serving.join(timeout=1)
    assert failures == []
