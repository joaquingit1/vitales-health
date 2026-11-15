"""recordAttempt over HTTP with the worker's adapter token (openapi.yaml recordAttempt, security adapterToken).

2xx is accepted, 4xx is a rejection carrying the contract envelope's code (D5), and a timeout, a connection error or a
5xx is unreachable: the service retries later with the same Idempotency-Key, so the hub replays instead of recording
twice. Nothing here raises across the port.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from crew_health_camera.application.ports import Delivery


class HttpHubClient:
    def __init__(self, client: httpx.Client, *, token: str, timeout_s: float) -> None:
        if not token:
            raise ValueError("adapter token missing")
        self._client, self._token, self._timeout = client, token, timeout_s

    def record_attempt(
        self, request_id: str, body: Mapping[str, object], *, idempotency_key: str
    ) -> Delivery:
        headers = {"Authorization": "Bearer " + self._token, "Idempotency-Key": idempotency_key}
        try:
            response = self._client.post(
                f"/v1/measurement-requests/{request_id}/attempts",
                json=dict(body),
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            return Delivery("unreachable", "unreachable")
        if 200 <= response.status_code < 300:
            return Delivery("accepted", None)
        if response.status_code >= 500:
            return Delivery("unreachable", "hub_" + str(response.status_code))
        try:
            reply = response.json()
        except ValueError:
            reply = None
        code = reply.get("code") if isinstance(reply, dict) else None
        return Delivery("rejected", code if isinstance(code, str) else "hub_" + str(response.status_code))
