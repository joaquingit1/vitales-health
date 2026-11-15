"""WorkerTransport over HTTP with the hub to worker token (DEPLOYMENT 7.3: loopback or private network, per service
token). Standard library only: the hub's runtime carries no HTTP client package."""

from __future__ import annotations

import http.client
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from crew_health.camera.application.ports import WorkerReply, WorkerUnreachable


class HttpWorkerTransport:
    def __init__(self, base_url: str, *, token: str, max_reply_bytes: int) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not token:
            raise ValueError("camera worker url or token missing")
        self._scheme, self._host, self._port = parts.scheme, parts.hostname, parts.port
        self._token, self._max = token, max_reply_bytes

    def call(
        self, method: Literal["GET", "POST"], path: str, body: bytes | None, *, timeout_s: float
    ) -> WorkerReply:
        factory = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        connection = factory(self._host, self._port, timeout=timeout_s)
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(self._max + 1)
        except (OSError, http.client.HTTPException) as error:
            raise WorkerUnreachable(type(error).__name__) from error
        finally:
            connection.close()
        if len(data) > self._max:
            raise WorkerUnreachable("reply too large")
        return WorkerReply(response.status, data)


class AbsentTransport:
    """The camera worker is not configured or not enabled: every call is unreachable, so readiness stays unknown."""

    def call(
        self, method: Literal["GET", "POST"], path: str, body: bytes | None, *, timeout_s: float
    ) -> WorkerReply:
        raise WorkerUnreachable("camera worker not configured")


def configured_transport(
    url: str, token_file: str, *, max_reply_bytes: int
) -> HttpWorkerTransport | AbsentTransport:
    """The HTTP transport from deployment values; a missing token file keeps the camera off (never the hub)."""
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
        return HttpWorkerTransport(url, token=token, max_reply_bytes=max_reply_bytes)
    except OSError, ValueError:
        return AbsentTransport()


def pause(seconds: float) -> None:
    time.sleep(seconds)
