"""HTTP transport preserving the exact journaled request bytes."""

from urllib.parse import quote

import httpx
from pydantic import ValidationError

from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health_device.application.ports import (
    Accepted,
    AcceptedReceipt,
    HubReply,
    Refused,
    TransportFailure,
)


class HttpHubAttempts:
    def __init__(self, client: httpx.Client, *, base_url: str, token: str, timeout_ms: int) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_ms = timeout_ms

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        url = f"{self._base_url}/v1/measurement-requests/{quote(request_id, safe='')}/attempts"
        try:
            response = self._client.post(
                url,
                content=body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout_ms / 1000,
            )
        except httpx.TimeoutException:
            return TransportFailure("timeout")
        except httpx.TransportError:
            return TransportFailure("transport")
        if response.status_code == 200:
            return Accepted(200)
        try:
            envelope = ErrorEnvelope.model_validate_json(response.content)
        except ValidationError:
            return Refused("MALFORMED_RESPONSE", response.status_code >= 500)
        if response.status_code == 409 and envelope.code == "DUPLICATE_COMMAND":
            state = envelope.current_state
            fields = ("command_key", "operation_id", "target", "receipt_id", "accepted_at")
            if state is not None and state.get("kind") == "accepted_command":
                values = tuple(state.get(field) for field in fields)
                if all(isinstance(value, str) for value in values):
                    return AcceptedReceipt(*(str(value) for value in values))
        return Refused(envelope.code, envelope.retryable)
