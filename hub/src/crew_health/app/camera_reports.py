"""F1 composition of the camera's hub side (E06 2, 14): the ports E06 consumes, bound to the providers that exist.

- `AttemptReports` is `HubReports`: E05's real recordAttempt route over the hub's own listener under the camera
  adapter's token, the same identity the worker records with (E06 2, eval/scenarios/E06/test_camera.py
  `HubReports`). Outcome failed, no observation, the reason code as the reason.
- E05's `AdapterReadinessPort` and `AdapterAssignmentPort` are the camera's own providers under E05's port classes;
  the assignment dataclass is nominal, so the binding converts it.
- E01's `TaskQueue` and `TaskRunner` are the platform `TaskService`.

The adapter token is read on each call: the demo issues it once the hub serves, and a missing or empty token is an
unreachable report that the camera task retries, never a recorded attempt.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from crew_health.camera.application import ports as camera_ports
from crew_health.measurement.application import ports as measurement_ports

MAX_TOKEN_BYTES = 4096
MAX_REPLY_BYTES = 65536


@dataclass(frozen=True, slots=True)
class HubReports:
    base_url: str
    token_file: Path
    timeout_s: float

    def _token(self) -> str | None:
        try:
            with self.token_file.open("rb") as handle:
                raw = handle.read(MAX_TOKEN_BYTES + 1)
        except OSError:
            return None
        token = raw.decode("utf-8", "replace").strip()
        return token if token and len(raw) <= MAX_TOKEN_BYTES else None

    def failed(
        self, request_id: str, *, sequence: int, at: datetime, reason: str, idempotency_key: str
    ) -> camera_ports.ReportResult:
        token = self._token()
        if token is None:
            return "unreachable"
        stamp = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        body = {
            "sequence": sequence,
            "started_at": stamp,
            "ended_at": stamp,
            "outcome": "failed",
            "failure_reason": reason,
            "observations": [],
            "quality_notes": [reason],
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/measurement-requests/{request_id}/attempts",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read(MAX_REPLY_BYTES)
                return "recorded"
        except urllib.error.HTTPError as error:
            return "unreachable" if error.code >= 500 else "refused"
        except OSError, ValueError:
            return "unreachable"


@dataclass(frozen=True, slots=True)
class MeasurementReadiness:
    camera: camera_ports.AdapterReadinessPort

    def ready_methods(self, view: measurement_ports.View) -> frozenset[str] | None:
        return self.camera.ready_methods(view)


@dataclass(frozen=True, slots=True)
class MeasurementAssignments:
    camera: camera_ports.AdapterAssignmentPort

    def assign(
        self, view: measurement_ports.View, *, capability_code: str, methods: tuple[str, ...]
    ) -> measurement_ports.AdapterAssignment | None:
        value = self.camera.assign(view, capability_code=capability_code, methods=methods)
        if value is None:
            return None
        return measurement_ports.AdapterAssignment(value.adapter_id, value.method, value.device_id)
