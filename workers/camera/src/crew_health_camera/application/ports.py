"""Ports of the camera worker's application layer: the capture device, the worker clock and the hub."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health_camera.domain.features import FrameFeatures

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]


@dataclass(frozen=True, slots=True)
class Camera:
    """A configured camera. subject_id is the person the device configuration pairs it with (PLAN Pairing: explicit,
    verified association); a camera paired with nobody is never used for anyone's measurement."""

    device_id: str
    name: str
    kind: Literal["personal", "station"]
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class Capture:
    """Features of every frame captured from the named cameras between started_ns and ended_ns (monotonic, one
    clock segment). Frames themselves were dropped on the device."""

    frames: tuple[FrameFeatures, ...]
    segment: str
    started_ns: int
    ended_ns: int


class CaptureFailed(Exception):
    """The device could not capture (unplugged, permission denied, driver error)."""


class CameraNotSettled(CaptureFailed):
    """The camera's exposure and white balance were still changing at the end of the warm up bound, so no frame was
    analysed (domain/exposure.py)."""


class FrameSource(Protocol):
    """A capture device behind one origin. LIVE is a real camera; REPLAY and SIM sources say so (AGENTS rule 2)."""

    @property
    def origin_mode(self) -> OriginMode: ...
    def cameras(self) -> tuple[Camera, ...]: ...
    def capture(
        self,
        device_ids: tuple[str, ...],
        *,
        duration_ns: int,
        max_frames: int,
        warmup_max_ns: int,
        should_stop: Callable[[], bool],
    ) -> Capture:
        """Frames over duration_ns, counted from the camera's first settled frame; a camera that has not settled
        within warmup_max_ns of opening raises CameraNotSettled."""
        ...


class WorkerClock(Protocol):
    def utc_now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...
    def segment(self) -> str: ...
    def wait_until_ns(self, monotonic_ns: int, should_stop: Callable[[], bool]) -> None: ...


@dataclass(frozen=True, slots=True)
class Delivery:
    kind: Literal["accepted", "rejected", "unreachable"]
    code: str | None


class EvidenceUnreadable(RuntimeError):
    """Saved evidence cannot be loaded (damaged file, unknown shape, more than the table holds). The worker refuses
    to start rather than drop it (AGENTS 6: a storage error fails loudly)."""


class EvidenceStore(Protocol):
    """Durable home of undelivered attempts (AGENTS 6: never discard a real reading). An attempt is saved before its
    first delivery, removed only once the hub accepted it, and kept aside (never deleted) when the hub rejected it;
    a restarted worker loads what is still undelivered and delivers it again with the same key. `pending` raises
    EvidenceUnreadable rather than skip anything it cannot load."""

    def save(self, acquisition_id: str, document: Mapping[str, object]) -> None: ...
    def delivered(self, acquisition_id: str) -> None: ...
    def rejected(self, acquisition_id: str) -> None: ...
    def pending(self) -> tuple[Mapping[str, object], ...]: ...


class HubClient(Protocol):
    """recordAttempt (openapi.yaml recordAttempt) with this worker's adapter token; never raises on transport."""

    def record_attempt(
        self, request_id: str, body: Mapping[str, object], *, idempotency_key: str
    ) -> Delivery: ...
