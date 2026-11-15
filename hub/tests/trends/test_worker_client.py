"""The worker boundary rejects untrusted responses and bounds every read."""

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import monotonic, sleep

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.adapters.worker_client import HttpForecastWorker, WorkerAddressError, worker_address
from crew_health.trends.application.ports import (
    DailyValue,
    ForecastRequest,
    WorkerForecast,
    WorkerUnavailable,
)


def request() -> ForecastRequest:
    return ForecastRequest("request_1", None, (DailyValue(1, Quantity(Decimal("5"), Unit("1"))),))


def forecast_response() -> dict[str, object]:
    return {
        "request_id": "request_1",
        "status": "forecast",
        "unit": "1",
        "model": {"id": "model_1", "revision": "revision_1", "sha256": "a" * 64},
        "policy_version": "policy_1",
        "interval_level": "0.8",
        "context_days": 1,
        "steps": [{"mission_day": 2, "low": "1", "median": "5", "high": "9"}],
    }


@contextmanager
def server(
    body: bytes, *, status: int = 200, delay: float = 0
) -> Iterator[tuple[str, list[tuple[str | None, bytes]]]]:
    received: list[tuple[str | None, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(
                (self.headers.get("Authorization"), self.rfile.read(int(self.headers["Content-Length"])))
            )
            if delay:
                sleep(delay)
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", received
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.mark.trace("E14-03", "R12", "V32")
def test_success_is_decimal_and_authorized_request_contains_exact_series() -> None:
    with server(json.dumps(forecast_response()).encode()) as (url, received):
        result = HttpForecastWorker(url, "secret", 1000, 4096).forecast(request())
    assert isinstance(result, WorkerForecast)
    assert result.steps[0].median == Quantity(Decimal("5"), Unit("1"))
    assert result.interval_level == Decimal("0.8")
    assert received[0][0] == "Bearer secret"
    assert json.loads(received[0][1]) == {
        "request_id": "request_1",
        "policy_version": None,
        "unit": "1",
        "series": [{"mission_day": 1, "value": "5"}],
    }


@pytest.mark.trace("E14-03", "V32")
def test_unavailable_response_preserves_stable_reason() -> None:
    with server(b'{"request_id":"request_1","status":"unavailable","reason":"insufficient_history"}') as (
        url,
        _,
    ):
        result = HttpForecastWorker(url, "secret", 1000, 4096).forecast(request())
    assert result == WorkerUnavailable("insufficient_history")


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b"{}", b'{"request_id":"request_1","status":"unavailable","reason":"private text"}'],
)
def test_malformed_response_is_rejected(body: bytes) -> None:
    with server(body) as (url, _):
        assert HttpForecastWorker(url, "secret", 1000, 4096).forecast(request()) == WorkerUnavailable(
            "worker_response_invalid"
        )


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "wrong"),
        ("interval_level", 0.8),
        ("interval_level", "NaN"),
        ("unit", "kg"),
        ("extra", "value"),
        ("context_days", 2),
        ("context_days", "1"),
    ],
)
def test_wrong_identity_float_nonfinite_or_extra_fields_are_rejected(field: str, value: object) -> None:
    body = forecast_response()
    body[field] = value
    with server(json.dumps(body).encode()) as (url, _):
        assert HttpForecastWorker(url, "secret", 1000, 4096).forecast(request()) == WorkerUnavailable(
            "worker_response_invalid"
        )


@pytest.mark.trace("E14-03", "V32")
def test_oversize_body_and_http_error_are_typed_unavailable() -> None:
    with server(b"x" * 1025) as (url, _):
        assert HttpForecastWorker(url, "secret", 1000, 1024).forecast(request()) == WorkerUnavailable(
            "worker_response_too_large"
        )
    with server(json.dumps(forecast_response()).encode(), status=503) as (url, _):
        assert HttpForecastWorker(url, "secret", 1000, 4096).forecast(request()) == WorkerUnavailable(
            "worker_response_invalid"
        )


@pytest.mark.trace("E14-03", "V32", "V69")
def test_timeout_and_unreachable_worker_are_typed_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    with server(b"{}", delay=0.2) as (url, _):
        assert HttpForecastWorker(url, "secret", 100, 4096).forecast(request()) == WorkerUnavailable(
            "worker_timeout"
        )
    # A closed port can time out on Windows; an explicit refusal proves this distinct transport result.
    fake(monkeypatch, ConnectionRefusedError())
    assert HttpForecastWorker(WORKER, "secret", 100, 4096).forecast(request()) == WorkerUnavailable(
        "worker_unreachable"
    )


class BoundedResponse:
    """Transport boundary fake for tests that do not need a socket."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status = body, status
        self.read_sizes: list[int] = []
        self.position = 0

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self.body[self.position : self.position + size]
        self.position += len(chunk)
        return chunk


class Connection:
    """Fake HTTPConnection: records what the client sends and serves one canned response."""

    def __init__(self, response: BoundedResponse | Exception) -> None:
        self.response = response
        self.sent: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.address: tuple[str, int, float] | None = None
        self.sock = None

    def __call__(self, host: str, port: int, *, timeout: float) -> Connection:
        self.address = (host, port, timeout)
        return self

    def connect(self) -> None:
        if isinstance(self.response, Exception):
            raise self.response

    def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
        self.sent.append((method, path, body, headers))

    def getresponse(self) -> BoundedResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        pass


def fake(monkeypatch: pytest.MonkeyPatch, response: BoundedResponse | Exception) -> Connection:
    connection = Connection(response)
    monkeypatch.setattr("crew_health.trends.adapters.worker_client.HTTPConnection", connection)
    return connection


WORKER = "http://forecast:8094"


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "body",
    [
        b'{"request_id":"request_1","request_id":"request_1","status":"unavailable","reason":"missing"}',
        b'{"request_id":"request_1","status":"unavailable","reason":null}',
        b'{"request_id":"request_1","status":"unavailable","reason":"private text"}',
        b'{"request_id":"request_1","status":"forecast","unit":"1","model":null,"policy_version":"1","interval_level":"0.8","steps":[]}',
        b'{"request_id":"request_1","status":"forecast","unit":"1","model":{"id":"id","revision":"rev","sha256":"bad"},"policy_version":"1","interval_level":"0.8","steps":[]}',
        b'{"request_id":"request_1","status":"forecast","unit":"1","model":{"id":"id","revision":"rev","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"policy_version":"1","interval_level":NaN,"steps":[]}',
    ],
)
def test_untrusted_json_is_rejected_without_interpreting_its_values(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    response = BoundedResponse(body)
    fake(monkeypatch, response)
    assert HttpForecastWorker(WORKER, "token", 100, 4096).forecast(request()) == WorkerUnavailable(
        "worker_response_invalid"
    )
    assert response.read_sizes[0] == 4097


@pytest.mark.trace("E14-03", "V32")
def test_transport_success_reads_only_the_response_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    response = BoundedResponse(json.dumps(forecast_response()).encode())
    connection = fake(monkeypatch, response)
    result = HttpForecastWorker(WORKER + "/", "token", 100, 4096).forecast(request())
    assert isinstance(result, WorkerForecast)
    assert result.steps[0].median.value == Decimal("5")
    assert result.interval_level == Decimal("0.8")
    assert response.read_sizes[0] == 4097 and sum(response.read_sizes) <= 4097 + 4097
    assert connection.address == ("forecast", 8094, 0.1)
    method, path, _, headers = connection.sent[0]
    assert (method, path, headers["Authorization"]) == ("POST", "/v1/forecast", "Bearer token")


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "field,value", [("mission_day", True), ("low", "NaN"), ("median", 5), ("high", "Infinity")]
)
def test_step_fields_reject_noninteger_days_and_nondecimal_values(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    body = forecast_response()
    body["steps"] = [{"mission_day": 2, "low": "1", "median": "5", "high": "9", field: value}]
    fake(monkeypatch, BoundedResponse(json.dumps(body).encode()))
    assert HttpForecastWorker(WORKER, "token", 100, 4096).forecast(request()) == WorkerUnavailable(
        "worker_response_invalid"
    )


@pytest.mark.trace("E14-03", "R12", "V32")
@given(st.decimals(min_value="-1000", max_value="1000", allow_nan=False, allow_infinity=False, places=3))
def test_decimal_text_round_trip_preserves_precision_and_unit(value: Decimal) -> None:
    body = forecast_response()
    body["steps"] = [{"mission_day": 2, "low": str(value), "median": str(value), "high": str(value)}]
    with pytest.MonkeyPatch.context() as patch:
        fake(patch, BoundedResponse(json.dumps(body).encode()))
        result = HttpForecastWorker(WORKER, "token", 100, 4096).forecast(request())
    assert isinstance(result, WorkerForecast)
    assert result.steps[0].median == Quantity(value, Unit("1"))


@pytest.mark.trace("E14-03", "V32", "AGENTS-1.7")
@pytest.mark.parametrize(
    "url",
    [
        "invalid endpoint",
        "http://forecast",
        "https://127.0.0.1:8094",
        "http://example.com:8094",
        "http://10.0.0.5:8094",
        "http://user:pw@127.0.0.1:8094",
        "http://127.0.0.1:8094/other",
    ],
)
def test_worker_address_outside_loopback_or_private_service_name_is_refused(url: str) -> None:
    with pytest.raises(WorkerAddressError):
        HttpForecastWorker(url, "token", 100, 4096)


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8094", "http://[::1]:8094", "http://localhost:8094", "http://forecast:8094/"]
)
def test_loopback_and_compose_service_name_are_accepted(url: str) -> None:
    assert worker_address(url)[1] == 8094


@pytest.mark.trace("E14-03", "V32", "V69")
@pytest.mark.parametrize(
    "failure,reason",
    [
        (TimeoutError(), "worker_timeout"),
        (OSError(), "worker_unreachable"),
    ],
)
def test_transport_errors_are_typed_without_exposing_input(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, reason: str
) -> None:
    fake(monkeypatch, failure)
    assert HttpForecastWorker(WORKER, "token", 100, 4096).forecast(request()) == WorkerUnavailable(reason)


@pytest.mark.trace("E14-03", "V32")
def test_large_transport_response_is_stopped_at_limit_plus_one(monkeypatch: pytest.MonkeyPatch) -> None:
    response = BoundedResponse(b"x" * 10000)
    fake(monkeypatch, response)
    assert HttpForecastWorker(WORKER, "token", 100, 1024).forecast(request()) == WorkerUnavailable(
        "worker_response_too_large"
    )
    assert response.read_sizes == [1025]


@pytest.mark.trace("E14-03", "V32")
def test_incomplete_http_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from http.client import IncompleteRead

    class IncompleteResponse(BoundedResponse):
        def read1(self, size: int) -> bytes:
            raise IncompleteRead(b"invalid chunk")

    fake(monkeypatch, IncompleteResponse(b""))
    assert HttpForecastWorker(WORKER, "token", 100, 4096).forecast(request()) == WorkerUnavailable(
        "worker_response_invalid"
    )


@pytest.mark.trace("E14-03", "AGENTS-1.7")
def test_proxy_environment_never_receives_the_bearer_token_or_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """m2 regression: with http_proxy set, the call still goes straight to the worker and nothing reaches the proxy."""
    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(1)
    proxy.settimeout(0.5)
    seen: list[bytes] = []

    def serve() -> None:
        try:
            connection, _ = proxy.accept()
        except TimeoutError:
            return
        with connection:
            connection.settimeout(1)
            seen.append(connection.recv(4096))

    thread = Thread(target=serve, daemon=True)
    thread.start()
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.getsockname()[1]}")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.getsockname()[1]}")
    with server(json.dumps(forecast_response()).encode()) as (url, received):
        result = HttpForecastWorker(url, "s3cret-token", 1000, 4096).forecast(request())
    thread.join(2)
    proxy.close()
    assert isinstance(result, WorkerForecast)
    assert result.steps[0].median == Quantity(Decimal("5"), Unit("1"))
    assert received == [
        (
            "Bearer s3cret-token",
            b'{"policy_version":null,"request_id":"request_1","series":[{"mission_day":1,"value":"5"}],"unit":"1"}',
        )
    ]
    assert seen == []


@pytest.mark.trace("E14-03", "V69", "AGENTS-3.2")
def test_a_worker_that_trickles_bytes_is_cut_at_the_total_deadline() -> None:
    """m6 regression: per read timeouts alone let a trickling worker hold the task; the call has one deadline."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = False

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
            for _ in range(200):
                if stop:
                    return
                try:
                    connection.sendall(b" ")
                except OSError:
                    return
                sleep(0.02)

    thread = Thread(target=serve, daemon=True)
    thread.start()
    started = monotonic()
    result = HttpForecastWorker(
        f"http://127.0.0.1:{listener.getsockname()[1]}", "token", 300, 1000000
    ).forecast(request())
    elapsed = monotonic() - started
    stop = True
    thread.join(5)
    listener.close()
    assert result == WorkerUnavailable("worker_timeout")
    assert elapsed < 1.5
