"""CONTRACTS 12: approved bundles wait without expiry or truncation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.groundlink.domain.errors import BundleConflict


@dataclass(frozen=True, slots=True)
class Bundle:
    bundle_id: str
    ref_kind: Literal["ground_message", "handoff"]
    ref_id: str
    content_sha256: str
    queued_at: datetime
    state: Literal["queued", "sending", "sent", "arrived"]
    attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    arrival_due_at: datetime | None
    arrived_at: datetime | None
    one_way_delay_s: float | None
    simulated: bool
    last_error: str | None


def to_send(bundles: tuple[Bundle, ...], status: str, now: datetime, max_inflight: int) -> tuple[Bundle, ...]:
    if status == "down":
        return ()
    ready: list[Bundle] = []
    for bundle in sorted(bundles, key=lambda b: (b.queued_at, b.bundle_id))[:max_inflight]:
        if bundle.state == "sending" and (bundle.next_attempt_at is None or bundle.next_attempt_at > now):
            break
        if bundle.state in ("queued", "sending"):
            ready.append(bundle)
    return tuple(ready)


def to_arrive(bundles: tuple[Bundle, ...], now: datetime) -> tuple[Bundle, ...]:
    ready = (
        b for b in bundles if b.state == "sent" and b.arrival_due_at is not None and b.arrival_due_at <= now
    )
    return tuple(sorted(ready, key=lambda b: (b.arrival_due_at, b.bundle_id)))


def backoff_ms(attempts: int, base_ms: int, exponent_max: int, cap_ms: int) -> int:
    if attempts < 1 or base_ms < 1 or exponent_max < 0 or cap_ms < 1:
        raise ValueError("positive retry parameters required")
    return min(cap_ms, base_ms * (1 << min(attempts - 1, exponent_max)))


def check_same_content(existing_sha: str, new_sha: str) -> None:
    if existing_sha != new_sha:
        raise BundleConflict("approved content hash differs")
