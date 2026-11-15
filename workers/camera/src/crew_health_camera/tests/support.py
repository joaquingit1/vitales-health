"""Test doubles for the camera worker: a controllable clock, a recording hub and a SIM camera rig."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crew_health_camera.adapters.settings import DEFAULT_POLICY, load_policy
from crew_health_camera.adapters.sim_source import Scene, SimSource
from crew_health_camera.application.model import StartSpec, WorkerBounds
from crew_health_camera.application.ports import Camera, Delivery
from crew_health_camera.application.service import AcquisitionService
from crew_health_camera.domain.policy import QualityPolicy

START = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
BOUNDS = WorkerBounds(
    max_acquisitions=4,
    max_frames=4000,
    max_devices=4,
    delivery_backoff_ms=1000,
    delivery_backoff_max_ms=8000,
    max_ttl_ms=600_000,
)
SUBJECT = "person_a"
WARMUP_MS = 5_000  # inside the hub setting workers.camera.warmup_max_ms bounds


@dataclass
class FakeClock:
    """Monotonic and UTC move together; waiting jumps time instead of sleeping."""

    ns: int = 1_000_000_000
    utc: datetime = START
    seg: str = "seg_1"

    def utc_now(self) -> datetime:
        return self.utc + timedelta(microseconds=(self.ns - 1_000_000_000) // 1000)

    def monotonic_ns(self) -> int:
        return self.ns

    def segment(self) -> str:
        return self.seg

    def wait_until_ns(self, monotonic_ns: int, should_stop: Callable[[], bool]) -> None:
        if not should_stop():
            self.ns = max(self.ns, monotonic_ns)

    def advance(self, seconds: float) -> None:
        self.ns += round(seconds * 1e9)


@dataclass
class FakeHub:
    """recordAttempt double: answers from a script, records every call."""

    answers: list[Delivery] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)

    def record_attempt(
        self, request_id: str, body: Mapping[str, object], *, idempotency_key: str
    ) -> Delivery:
        self.calls.append((request_id, dict(body), idempotency_key))
        return self.answers.pop(0) if self.answers else Delivery("accepted", None)


@dataclass
class MemoryEvidence:
    """EvidenceStore double that outlives a service, as the state directory outlives a worker process."""

    saved: dict[str, dict[str, object]] = field(default_factory=dict)
    kept_rejected: dict[str, dict[str, object]] = field(default_factory=dict)

    def save(self, acquisition_id: str, document: Mapping[str, object]) -> None:
        self.saved[acquisition_id] = json.loads(json.dumps(dict(document)))

    def delivered(self, acquisition_id: str) -> None:
        self.saved.pop(acquisition_id, None)

    def rejected(self, acquisition_id: str) -> None:
        if acquisition_id in self.saved:
            self.kept_rejected[acquisition_id] = self.saved.pop(acquisition_id)

    def pending(self) -> tuple[Mapping[str, object], ...]:
        return tuple(self.saved[k] for k in sorted(self.saved))


def policy(path: Path = DEFAULT_POLICY) -> QualityPolicy:
    return load_policy(path)


def policy_document() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    return value


def cameras(*ids: str, subject: str | None = SUBJECT) -> tuple[Camera, ...]:
    return tuple(Camera(d, "Camera " + d.rsplit("_", 1)[-1], "station", subject) for d in ids)


@dataclass
class Rig:
    clock: FakeClock
    source: SimSource
    hub: FakeHub
    service: AcquisitionService
    evidence: MemoryEvidence

    def restart(self) -> AcquisitionService:
        """A new worker process over the same state directory (the evidence store) and the same cameras."""
        counter = iter(range(100, 10_000))
        self.service = AcquisitionService(
            policy=self.service.policy,
            source=self.source,
            clock=self.clock,
            hub=self.hub,
            bounds=self.service.bounds,
            receipt_ids=lambda: f"rcp_{next(counter)}",
            evidence=self.evidence,
        )
        return self.service


def rig(
    scenes: Mapping[str, Scene],
    *,
    quality: QualityPolicy | None = None,
    bounds: WorkerBounds = BOUNDS,
    rate_hz: float = 30.0,
    failing: bool = False,
) -> Rig:
    clock = FakeClock()
    source = SimSource(
        clock=clock, cameras=cameras(*sorted(scenes)), scenes=scenes, rate_hz=rate_hz, seed=7, failing=failing
    )
    hub = FakeHub()
    counter = iter(range(1, 10_000))
    evidence = MemoryEvidence()
    service = AcquisitionService(
        policy=quality or policy(),
        source=source,
        clock=clock,
        hub=hub,
        bounds=bounds,
        receipt_ids=lambda: f"rcp_{next(counter)}",
        evidence=evidence,
    )
    return Rig(clock, source, hub, service, evidence)


def measurement(
    acquisition_id: str = "acq_1",
    *,
    code: str = "pulse",
    devices: tuple[str, ...] = ("camera_1",),
    sequence: int = 1,
    origin: str = "SIM",
) -> StartSpec:
    return StartSpec(
        acquisition_id=acquisition_id,
        kind="measurement",
        capability_code=code,
        request_id="mrq_1",
        patient_id=SUBJECT,
        sequence=sequence,
        device_ids=devices,
        window_start=None,
        window_end=None,
        required_origin=origin,  # type: ignore[arg-type]
        ttl_ms=120_000,
        warmup_max_ms=WARMUP_MS,
        clock_offset_ms=0,
        clock_uncertainty_ms=0,
    )


def reference(
    acquisition_id: str = "ref_1",
    *,
    start_in_s: float = 5.0,
    length_s: float = 30.0,
    origin: str | None = "SIM",
    offset_ms: int = 0,
    persist: bool = False,
    devices: tuple[str, ...] = ("camera_1",),
) -> StartSpec:
    begin = START + timedelta(seconds=start_in_s)
    return StartSpec(
        acquisition_id=acquisition_id,
        kind="reference",
        capability_code="pulse",
        request_id=None,
        patient_id=SUBJECT,
        sequence=None,
        device_ids=devices,
        window_start=begin,
        window_end=begin + timedelta(seconds=length_s),
        required_origin=origin,  # type: ignore[arg-type]
        ttl_ms=120_000,
        warmup_max_ms=WARMUP_MS,
        clock_offset_ms=offset_ms,
        clock_uncertainty_ms=12,
        persist=persist,
    )


STEADY = Scene(pulse_hz=1.6, breath_hz=0.25)
"""A still seated subject: pulse 1.6 Hz (96 per minute) and breathing 0.25 Hz (15 per minute) by construction."""
