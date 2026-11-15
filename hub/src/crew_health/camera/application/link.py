"""The hub's side of the hub to camera worker protocol v1 (workers/camera crew_health_camera adapters/protocol.py,
published as protocol.schema.json). Every call is bounded by `camera.timeout_ms`, runs outside any unit of
work, and never raises: an unreachable worker is a typed answer, never an assumed state.

The describe probe also estimates the worker's clock offset the NTP way: offset = worker UTC minus the midpoint of
the hub's send and receive times, uncertainty = half the round trip (SYSTEM 5.2 provenance for E30's
clock_uncertainty_ms).
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.camera.application.ports import WorkerTransport, WorkerUnreachable
from crew_health.kernel.clock import Clock

PROTOCOL_VERSION = "1"


@dataclass(frozen=True, slots=True)
class Probe:
    at: datetime
    adapter_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    ready_methods: frozenset[str]
    capability_codes: frozenset[str]
    cameras: tuple[
        tuple[str, str | None], ...
    ]  # (device_id, subject_id paired in the worker's device configuration)
    max_ttl_ms: int
    offset_ms: int
    uncertainty_ms: int

    @property
    def camera_count(self) -> int:
        return len(self.cameras)

    def paired_with(self, subject_id: str) -> tuple[str, ...]:
        """The cameras the device configuration pairs with this person (B1); never every camera by default."""
        return tuple(sorted(device for device, subject in self.cameras if subject == subject_id))


@dataclass(frozen=True, slots=True)
class Answer:
    """A worker reply: kind ok carries the parsed body; refused carries the protocol error code."""

    kind: Literal["ok", "refused", "not_found", "unreachable"]
    body: Mapping[str, object]
    code: str | None


def _instant(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WorkerLink:
    def __init__(self, transport: WorkerTransport, clock: Clock, *, timeout_ms: int, stale_ms: int) -> None:
        self._transport, self._clock = transport, clock
        self._timeout_ms, self._stale = timeout_ms, timedelta(milliseconds=stale_ms)
        self._lock = threading.Lock()
        self._probe: Probe | None = None

    def _call(
        self, method: Literal["GET", "POST"], path: str, body: Mapping[str, object] | None, timeout_ms: int
    ) -> Answer:
        raw = None if body is None else json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        try:
            reply = self._transport.call(
                method, path, raw, timeout_s=min(timeout_ms, self._timeout_ms) / 1000
            )
            parsed = json.loads(reply.body) if reply.body else {}
        except WorkerUnreachable, TimeoutError, ConnectionError, OSError, ValueError:
            return Answer("unreachable", {}, None)
        if not isinstance(parsed, dict):
            return Answer("unreachable", {}, None)
        if 200 <= reply.status < 300:
            return Answer("ok", parsed, None)
        code = parsed.get("code")
        if reply.status == 404:
            return Answer("not_found", parsed, "NOT_FOUND")
        if reply.status >= 500 or not isinstance(code, str):
            return Answer("unreachable", parsed, None)
        return Answer("refused", parsed, code)

    # ------------------------------------------------------------------------------------------- probe

    def probe(self) -> Probe | None:
        """Ask the worker to describe itself and cache the answer; a failed probe clears readiness."""
        sent = self._clock.now()
        answer = self._call("GET", "/v1/describe", None, self._timeout_ms)
        received = self._clock.now()
        probe = _probe(answer, sent, received)
        with self._lock:
            self._probe = probe
        return probe

    def fresh(self, now: datetime) -> Probe | None:
        """The last probe if it is recent enough; a stale or failed probe is unknown (never ready)."""
        with self._lock:
            probe = self._probe
        if probe is None or now - probe.at > self._stale or now < probe.at:
            return None
        return probe

    # ------------------------------------------------------------------------------------------- acquisitions

    def start(self, body: Mapping[str, object], *, timeout_ms: int) -> Answer:
        return self._call("POST", "/v1/acquisitions", body, timeout_ms)

    def status(self, acquisition_id: str, *, timeout_ms: int) -> Answer:
        return self._call("GET", "/v1/acquisitions/" + acquisition_id, None, timeout_ms)

    def cancel(
        self, acquisition_id: str, *, timeout_ms: int, recorded_observation_id: str | None = None
    ) -> Answer:
        """Stop an acquisition; a persisted reference reading is released only when the cancel names the observation
        the hub recorded it as (protocol CancelRequest)."""
        body = {} if recorded_observation_id is None else {"recorded_observation_id": recorded_observation_id}
        return self._call("POST", "/v1/acquisitions/" + acquisition_id + "/cancel", body, timeout_ms)

    @staticmethod
    def window(start: datetime, end: datetime) -> dict[str, str]:
        return {"window_start": _instant(start), "window_end": _instant(end)}


def _probe(answer: Answer, sent: datetime, received: datetime) -> Probe | None:
    body = answer.body
    if answer.kind != "ok" or body.get("protocol_version") != PROTOCOL_VERSION:
        return None
    adapter, origin, utc = body.get("adapter_id"), body.get("origin_mode"), body.get("worker_utc")
    methods, codes, cameras = body.get("ready_methods"), body.get("capability_codes"), body.get("cameras")
    max_ttl = body.get("max_ttl_ms")
    if not isinstance(adapter, str) or origin not in ("LIVE", "REPLAY", "SIM") or not isinstance(utc, str):
        return None
    if not isinstance(methods, list) or not isinstance(codes, list) or not isinstance(cameras, list):
        return None
    paired = _cameras(cameras)
    if paired is None or isinstance(max_ttl, bool) or not isinstance(max_ttl, int) or max_ttl < 1:
        return None
    try:
        worker = datetime.fromisoformat(utc)
    except ValueError:
        return None
    if worker.tzinfo is None:
        return None
    middle = sent + (received - sent) / 2
    offset_ms = round((worker - middle) / timedelta(milliseconds=1))
    uncertainty_ms = math.ceil((received - sent) / timedelta(milliseconds=1) / 2)
    return Probe(
        at=received,
        adapter_id=adapter,
        origin_mode=origin,
        ready_methods=frozenset(str(m) for m in methods),
        capability_codes=frozenset(str(c) for c in codes),
        cameras=paired,
        max_ttl_ms=max_ttl,
        offset_ms=offset_ms,
        uncertainty_ms=uncertainty_ms,
    )


def _cameras(values: list[object]) -> tuple[tuple[str, str | None], ...] | None:
    found: list[tuple[str, str | None]] = []
    for value in values:
        if not isinstance(value, Mapping):
            return None
        device, subject = value.get("device_id"), value.get("subject_id")
        if not isinstance(device, str) or not (subject is None or isinstance(subject, str)):
            return None
        found.append((device, subject))
    return tuple(found)
