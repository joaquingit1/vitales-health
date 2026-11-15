"""Acquisition records, bounds and typed refusals of the camera worker (the hub to worker protocol's inside)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health_camera.domain.selection import Refusal, Selection

type Kind = Literal["measurement", "reference"]
type State = Literal[
    "accepted",
    "capturing",
    "delivering",
    "delivered",
    "rejected",
    "reading",
    "no_reading",
    "cancelled",
    "expired",
]
TERMINAL: frozenset[str] = frozenset(
    {"delivered", "rejected", "reading", "no_reading", "cancelled", "expired"}
)


@dataclass(frozen=True, slots=True)
class StartSpec:
    """What the hub asks for. A measurement names the E05 request, its subject and the attempt sequence; a reference
    window (E30) names a hub UTC window and the origin the check needs. Instants are hub UTC; clock_offset_ms is
    worker minus hub as the hub measured it, so worker instants map back to hub time."""

    acquisition_id: str
    kind: Kind
    capability_code: str
    request_id: str | None
    patient_id: str | None
    sequence: int | None
    device_ids: tuple[str, ...]
    window_start: datetime | None
    window_end: datetime | None
    required_origin: Literal["LIVE", "REPLAY", "SIM"] | None
    ttl_ms: int
    clock_offset_ms: int
    clock_uncertainty_ms: int
    warmup_max_ms: int
    persist: bool = False

    def identity(self) -> StartSpec:
        """What makes two starts the same start. The clock probe (offset and uncertainty) is measured again before
        every retry, so it is not part of the identity: a retry after a lost reply is the same start, and the worker
        keeps the offset it accepted first (M1, PLAN V16). The warm up bound is a hub setting sent with every try and
        is left out for the same reason."""
        return replace(self, clock_offset_ms=0, clock_uncertainty_ms=0, warmup_max_ms=0)


@dataclass(frozen=True, slots=True)
class WorkerBounds:
    """Operational bounds (SYSTEM 5.11), each with a finite ceiling checked when the worker starts."""

    max_acquisitions: int
    max_frames: int
    max_devices: int
    delivery_backoff_ms: int
    delivery_backoff_max_ms: int
    max_ttl_ms: int

    def delivery_delay_ms(self, tries: int) -> int:
        """Exponential backoff after `tries` failed deliveries, capped: undelivered evidence is retried forever at a
        bounded rate (SYSTEM 5.11 workers row: retries indefinitely at bounded rate), never dropped."""
        return min(self.delivery_backoff_max_ms, self.delivery_backoff_ms * (1 << min(max(tries - 1, 0), 30)))


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one acquisition found: a selection or a refusal, over the captured window in hub UTC."""

    result: Selection | Refusal
    started_at: datetime
    ended_at: datetime
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    camera_names: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class StatusView:
    acquisition_id: str
    receipt_id: str
    kind: Kind
    capability_code: str
    state: State
    outcome: Outcome | None
    delivery_code: str | None
    clock_uncertainty_ms: int


class AcquisitionRefused(Exception):
    """A start the worker refuses before any capture: code is the protocol error code."""

    def __init__(
        self,
        code: Literal[
            "BUSY", "CONFLICT", "UNSUPPORTED", "NO_CAMERA", "INVALID", "NOT_PAIRED", "ORIGIN_MISMATCH"
        ],
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
