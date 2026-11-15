"""Bounded authenticated transport for the isolated experimental forecast worker."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from decimal import Decimal
from http.client import HTTPConnection, HTTPException
from threading import Timer
from typing import cast
from urllib.parse import urlsplit

from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.application.ports import (
    ForecastRequest,
    ForecastStep,
    ForecastWorkerPort,
    WorkerForecast,
    WorkerUnavailable,
)


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("object fields")
    return cast(dict[str, object], value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate member")
        result[key] = value
    return result


def _invalid_number(value: str) -> object:
    raise ValueError("non integer JSON number")


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("text required")
    return value


def _decimal(value: object) -> Decimal:
    result = Decimal(_text(value))
    if not result.is_finite():
        raise ValueError("finite decimal required")
    return result


def _step(value: object, unit: Unit) -> ForecastStep:
    data = _object(value, {"mission_day", "low", "median", "high"})
    day = data["mission_day"]
    if type(day) is not int:
        raise ValueError("integer mission day required")
    return ForecastStep(
        day,
        Quantity(_decimal(data["low"]), unit),
        Quantity(_decimal(data["median"]), unit),
        Quantity(_decimal(data["high"]), unit),
    )


def _parse(raw: bytes, request: ForecastRequest) -> WorkerForecast | WorkerUnavailable:
    value: object = json.loads(
        raw, object_pairs_hook=_pairs, parse_float=_invalid_number, parse_constant=_invalid_number
    )
    if not isinstance(value, dict) or value.get("request_id") != request.request_id:
        raise ValueError("request identity")
    if value.get("status") == "unavailable":
        data = _object(value, {"request_id", "status", "reason"})
        reason = _text(data["reason"])
        if re.fullmatch(r"[a-z][a-z0-9_]*", reason) is None:
            raise ValueError("stable reason required")
        return WorkerUnavailable(reason)
    data = _object(
        value,
        {
            "request_id",
            "status",
            "unit",
            "model",
            "policy_version",
            "interval_level",
            "context_days",
            "steps",
        },
    )
    if data["status"] != "forecast" or data["unit"] != request.series[0].value.unit.code:
        raise ValueError("forecast unit")
    context_days = data["context_days"]
    if type(context_days) is not int or not 1 <= context_days <= len(request.series):
        raise ValueError("context days")
    model = _object(data["model"], {"id", "revision", "sha256"})
    digest = _text(model["sha256"])
    if re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None:
        raise ValueError("model digest")
    steps = data["steps"]
    if not isinstance(steps, list):
        raise ValueError("steps array")
    return WorkerForecast(
        _text(model["id"]),
        _text(model["revision"]),
        digest,
        _text(data["policy_version"]),
        _decimal(data["interval_level"]),
        tuple(_step(step, request.series[0].value.unit) for step in steps),
    )


def request_body(request: ForecastRequest) -> bytes:
    """The worker request (E14 R3 proposal): decimal text values with their UCUM unit, no person identifiers."""
    return json.dumps(
        {
            "request_id": request.request_id,
            "policy_version": request.policy_version,
            "unit": request.series[0].value.unit.code,
            "series": [
                {"mission_day": point.mission_day, "value": str(point.value.value)}
                for point in request.series
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


_SERVICE_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
_READ_CHUNK = 65536


class WorkerAddressError(ValueError):
    """The configured worker address is not a private worker address."""


def worker_address(base_url: str) -> tuple[str, int]:
    """Loopback (native) or a single label service name on the isolated compose network (DEPLOYMENT 4).

    Anything else, including any dotted host name or routable address, is refused: patient values and the bearer
    token never leave the private worker path.
    """
    parts = urlsplit(base_url)
    host, port = parts.hostname, parts.port
    if parts.scheme != "http" or host is None or port is None or parts.path not in ("", "/"):
        raise WorkerAddressError("worker_address_invalid")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise WorkerAddressError("worker_address_invalid")
    if host == "localhost" or _SERVICE_NAME.fullmatch(host):
        return host, port
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise WorkerAddressError("worker_address_invalid")
    return host, port


class HttpForecastWorker(ForecastWorkerPort):
    """Direct HTTP to the worker: no proxy is ever consulted, and the whole call has one deadline."""

    def __init__(self, base_url: str, token: str, timeout_ms: int, response_max_bytes: int) -> None:
        self._host, self._port = worker_address(base_url)
        self._token = token
        self._timeout_ms = timeout_ms
        self._response_max_bytes = response_max_bytes

    def forecast(self, request: ForecastRequest) -> WorkerForecast | WorkerUnavailable:
        if not request.series or any(
            point.value.unit != request.series[0].value.unit for point in request.series
        ):
            return WorkerUnavailable("worker_response_invalid")
        deadline = time.monotonic() + self._timeout_ms / 1000
        connection = HTTPConnection(self._host, self._port, timeout=self._timeout_ms / 1000)
        try:
            connection.connect()
            # The watchdog shuts the socket at the deadline, so a worker that trickles bytes cannot hold the task.
            watchdog = Timer(max(deadline - time.monotonic(), 0.0), _shutdown, (connection,))
            watchdog.daemon = True
            watchdog.start()
            try:
                raw = self._exchange(connection, request_body(request), deadline)
            finally:
                watchdog.cancel()
            if raw is None or time.monotonic() > deadline:
                return WorkerUnavailable("worker_timeout")
            if isinstance(raw, WorkerUnavailable):
                return raw
            return _parse(raw, request)
        except TimeoutError:
            return WorkerUnavailable("worker_timeout")
        except OSError:
            return (
                WorkerUnavailable("worker_timeout")
                if time.monotonic() > deadline
                else WorkerUnavailable("worker_unreachable")
            )
        except ValueError, TypeError, ArithmeticError, UnicodeError, RecursionError, HTTPException:
            if time.monotonic() > deadline:
                return WorkerUnavailable("worker_timeout")
            return WorkerUnavailable("worker_response_invalid")
        finally:
            connection.close()

    def _exchange(
        self, connection: HTTPConnection, body: bytes, deadline: float
    ) -> bytes | WorkerUnavailable | None:
        connection.request(
            "POST",
            "/v1/forecast",
            body=body,
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            return WorkerUnavailable("worker_response_invalid")
        raw = bytearray()
        # Bounded even when the worker sends one byte per read; the deadline usually ends it far sooner.
        for _ in range(self._response_max_bytes + 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(_READ_CHUNK, self._response_max_bytes + 1 - len(raw)))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
            if len(raw) > self._response_max_bytes:
                return WorkerUnavailable("worker_response_too_large")
        return WorkerUnavailable("worker_response_too_large")


def _shutdown(connection: HTTPConnection) -> None:
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            return
