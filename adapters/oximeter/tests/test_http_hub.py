"""V16 transport contract tested without sockets."""

from collections.abc import Mapping

import httpx
import pytest

from crew_health_device.adapters.http_hub import HttpHubAttempts
from crew_health_device.application.ports import Accepted, AcceptedReceipt, Refused, TransportFailure


def envelope(code: str = "DUPLICATE_COMMAND", state: object = None) -> dict[str, object]:
    return {
        "code": code,
        "message": "code only",
        "retryable": False,
        "field_errors": [],
        "correlation_id": "corr-1",
        "current_state": state,
        "supported_alternative": None,
    }


@pytest.mark.trace("V16")
def test_http_sends_exact_body_authorization_idempotency_and_timeout() -> None:
    body = b'{"observations":[{"value":97.5}]}'

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://hub.test/v1/measurement-requests/req-1/attempts"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["idempotency-key"] == "e07:acq-1:1"
        assert request.headers["content-type"] == "application/json"
        assert request.content == body
        assert request.extensions["timeout"] == {"connect": 1.0, "read": 1.0, "write": 1.0, "pool": 1.0}
        return httpx.Response(200, content=b"private unparsed result")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        hub = HttpHubAttempts(client, base_url="https://hub.test/", token="secret", timeout_ms=1000)
        assert hub.post_attempt("req-1", "e07:acq-1:1", body) == Accepted(200)


@pytest.mark.trace("V16")
@pytest.mark.parametrize(
    ("status", "document", "expected"),
    [
        (
            409,
            envelope(
                state={
                    "kind": "accepted_command",
                    "command_key": "key",
                    "operation_id": "recordAttempt",
                    "target": "req-1",
                    "receipt_id": "receipt-1",
                    "accepted_at": "2026-11-14T00:00:00Z",
                }
            ),
            AcceptedReceipt("key", "recordAttempt", "req-1", "receipt-1", "2026-11-14T00:00:00Z"),
        ),
        (
            409,
            envelope(state={"kind": "accepted_command", "command_key": 1}),
            Refused("DUPLICATE_COMMAND", False),
        ),
        (409, envelope(state={"kind": "other"}), Refused("DUPLICATE_COMMAND", False)),
        (409, envelope(), Refused("DUPLICATE_COMMAND", False)),
        (403, envelope("ADAPTER_NOT_TRUSTED"), Refused("ADAPTER_NOT_TRUSTED", False)),
        (500, None, Refused("MALFORMED_RESPONSE", True)),
        (400, None, Refused("MALFORMED_RESPONSE", False)),
    ],
)
def test_http_parses_only_generated_envelopes_and_accepted_command_receipts(
    status: int, document: Mapping[str, object] | None, expected: object
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(status, json=document)
            if document is not None
            else httpx.Response(status, content=b"bad json")
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            HttpHubAttempts(client, base_url="http://hub.test", token="token", timeout_ms=10).post_attempt(
                "req", "key", b"{}"
            )
            == expected
        )


@pytest.mark.trace("V16")
@pytest.mark.parametrize(
    ("exception", "reason"),
    [(httpx.ReadTimeout("timeout"), "timeout"), (httpx.ConnectError("offline"), "transport")],
)
def test_http_transport_failures_remain_retryable(exception: httpx.TransportError, reason: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exception

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert HttpHubAttempts(client, base_url="http://hub.test", token="token", timeout_ms=10).post_attempt(
            "req", "key", b"{}"
        ) == TransportFailure(reason)
