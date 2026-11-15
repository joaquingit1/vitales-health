"""Health and heartbeat of one adapter process (schema adapters/habitat/schemas/health.schema.json, E23 5.2).

`/health` answers exactly adapter_id, clock and queue. It runs in its own thread, apart from the bounded
transport, so a slow or down hub never stops it. It binds loopback only: an enrolled remote node reaches it through
the hub's mTLS or private compose routing (DEPLOYMENT 5/7), never an unauthenticated LAN port. The loop's heartbeat
is separate: a stalled loop turns /health into 503, which the hub reads as unknown. `/diagnostics` carries the
local, allowlisted fault codes and counts (never reading text or the token).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from crew_health_adapter_habitat.errors import AdapterError
from crew_health_adapter_habitat.ports import Clock, ClockState, Settings

LOOPBACK = "127.0.0.1"


@dataclass
class HealthState:
    adapter_id: str
    clock: Clock
    settings: Settings
    queue_full: bool = False
    last_heartbeat: datetime | None = None
    fault: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def beat(self) -> None:
        with self.lock:
            self.last_heartbeat = self.clock.now()

    def update(
        self, *, queue_full: bool | None = None, fault: str | None = None, clear_fault: bool = False
    ) -> None:
        with self.lock:
            if queue_full is not None:
                self.queue_full = queue_full
            if fault is not None:
                self.fault = fault
            if clear_fault:
                self.fault = None

    def set_counts(self, counts: dict[str, int]) -> None:
        with self.lock:
            self.counts = dict(counts)

    def alive(self) -> bool:
        """True while the loop has beaten within retry_max_backoff_s (a stalled loop is not healthy)."""
        with self.lock:
            beat = self.last_heartbeat
        if beat is None:
            return False
        return (self.clock.now() - beat).total_seconds() <= self.settings.retry_max_backoff_s

    def document(self) -> dict[str, str]:
        clock: ClockState = self.clock.state()
        with self.lock:
            full = self.queue_full
        return {"adapter_id": self.adapter_id, "clock": clock, "queue": "queue_full" if full else "ok"}

    def diagnostics(self) -> dict[str, Any]:
        with self.lock:
            return {"fault": self.fault, "counts": dict(self.counts), "queue_full": self.queue_full}


def _handler(state: HealthState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path == "/health":
                code, body = (
                    (200, state.document()) if state.alive() else (503, {"adapter_id": state.adapter_id})
                )
            elif self.path == "/diagnostics":
                code, body = 200, state.diagnostics()
            else:
                code, body = 404, {"code": "NOT_FOUND"}
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return None

    return Handler


class HealthServer:
    def __init__(self, state: HealthState, port: int, host: str = LOOPBACK) -> None:
        if host != LOOPBACK:
            raise AdapterError("health binds loopback only")
        self.server = ThreadingHTTPServer((host, port), _handler(state))
        self.timeout_s = float(state.settings.http_timeout_s)
        self.server.timeout = self.timeout_s
        self.thread = threading.Thread(target=self.server.serve_forever, name="adapter-health", daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=self.timeout_s)
