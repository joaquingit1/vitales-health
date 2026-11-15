"""The HTTP transport to the worker: token, bounded reply, typed failure (DEPLOYMENT 7.3, SYSTEM 3). A loopback
server stands in for the worker process; nothing leaves the machine."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from crew_health.camera.adapters.http_transport import (
    AbsentTransport,
    HttpWorkerTransport,
    configured_transport,
)
from crew_health.camera.application.ports import WorkerUnreachable

pytestmark = pytest.mark.trace("A7", "V11", "AGENTS-6.bounds")


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str | None, bytes]] = []

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _Handler.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
        payload = b"x" * 5000 if self.path == "/big" else b'{"ok": true}'
        self.send_response(202 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def worker() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen.clear()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_calls_carry_the_bearer_token_and_return_status_and_body(worker: str) -> None:
    transport = HttpWorkerTransport(worker, token="hub-to-worker", max_reply_bytes=1024)
    answer = transport.call("POST", "/v1/acquisitions", b'{"a": 1}', timeout_s=2.0)
    assert (answer.status, answer.body) == (202, b'{"ok": true}')
    assert _Handler.seen[-1] == ("POST", "/v1/acquisitions", "Bearer hub-to-worker", b'{"a": 1}')


def test_a_reply_over_max_reply_bytes_is_unreachable_not_truncated(worker: str) -> None:
    transport = HttpWorkerTransport(worker, token="t", max_reply_bytes=1024)
    with pytest.raises(WorkerUnreachable, match="too large"):
        transport.call("GET", "/big", None, timeout_s=2.0)


def test_a_closed_port_is_unreachable(worker: str) -> None:
    transport = HttpWorkerTransport("http://127.0.0.1:9", token="t", max_reply_bytes=1024)
    with pytest.raises(WorkerUnreachable):
        transport.call("GET", "/v1/describe", None, timeout_s=0.5)


@pytest.mark.parametrize(
    ("url", "token"), [("ftp://host", "t"), ("http://", "t"), ("http://127.0.0.1:1", "")]
)
def test_a_bad_url_or_empty_token_is_refused(url: str, token: str) -> None:
    with pytest.raises(ValueError):
        HttpWorkerTransport(url, token=token, max_reply_bytes=1024)


def test_configured_transport_reads_the_token_file_and_falls_back_to_absent(
    tmp_path: Path, worker: str
) -> None:
    token = tmp_path / "camera.token"
    token.write_text("secret\n", encoding="utf-8")
    configured = configured_transport(worker, str(token), max_reply_bytes=1024)
    assert isinstance(configured, HttpWorkerTransport)
    configured.call("GET", "/v1/describe", None, timeout_s=2.0)
    assert _Handler.seen[-1][2] == "Bearer secret"
    absent = configured_transport(worker, str(tmp_path / "missing"), max_reply_bytes=1024)
    assert isinstance(absent, AbsentTransport)
    assert isinstance(configured_transport("", "", max_reply_bytes=1024), AbsentTransport)
    with pytest.raises(WorkerUnreachable):
        absent.call("GET", "/v1/describe", None, timeout_s=1.0)
