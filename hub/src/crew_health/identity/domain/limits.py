"""E02 3.2 sign-in attempt limits: independent principal, client and global windows.

Windows are elapsed intervals anchored on monotonic time inside one process epoch. A restart cannot
measure elapsed time, so it keeps the failure debt and restarts the window: unknown time never unlocks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from crew_health.identity.domain.session import Instant, identity_mismatch
from crew_health.kernel.errors import ErrorCode, ProblemError

type BucketScope = Literal["principal", "client", "global"]

GLOBAL_KEY = "global"
_OVERFLOW = "unknown:"


@dataclass(frozen=True, slots=True)
class AttemptPolicy:
    per_identity: int
    global_attempts: int
    window_ns: int

    def __post_init__(self) -> None:
        if self.per_identity < 1 or self.global_attempts < 1 or self.window_ns < 1:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)

    def limit(self, scope: BucketScope) -> int:
        return self.global_attempts if scope == "global" else self.per_identity


@dataclass(frozen=True, slots=True)
class Bucket:
    scope: BucketScope
    key: str
    anchor_ns: int
    process_epoch: str
    attempts: int

    def __post_init__(self) -> None:
        if not self.key or self.attempts < 0 or self.anchor_ns < 0:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


@dataclass(frozen=True, slots=True)
class BucketKeys:
    principal: str
    client: str


def bucket_keys(*, principal_id: str | None, client: str) -> BucketKeys:
    """Unknown principals share one overflow bucket per client instead of allocating one per guess."""
    if not client:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return BucketKeys(principal_id if principal_id is not None else _OVERFLOW + client, client)


def fresh(scope: BucketScope, key: str, now: Instant) -> Bucket:
    return Bucket(scope, key, now.monotonic_ns, now.process_epoch, 0)


def current(bucket: Bucket, policy: AttemptPolicy, now: Instant) -> Bucket:
    """The bucket as of now: a finished window resets; another epoch keeps the debt in a new window."""
    if bucket.process_epoch != now.process_epoch or now.monotonic_ns < bucket.anchor_ns:
        return replace(bucket, anchor_ns=now.monotonic_ns, process_epoch=now.process_epoch)
    if now.monotonic_ns - bucket.anchor_ns >= policy.window_ns:
        return replace(bucket, anchor_ns=now.monotonic_ns, attempts=0)
    return bucket


def refresh(buckets: tuple[Bucket, ...], policy: AttemptPolicy, now: Instant) -> tuple[Bucket, ...]:
    """The principal, client and global buckets as of now. A refused attempt still stores this, so the
    window a restart opened starts at its first observation instead of never."""
    if {bucket.scope for bucket in buckets} != {"principal", "client", "global"} or len(buckets) != 3:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return tuple(current(bucket, policy, now) for bucket in buckets)


def admits(buckets: tuple[Bucket, ...], policy: AttemptPolicy) -> bool:
    return all(bucket.attempts < policy.limit(bucket.scope) for bucket in buckets)


def reserve(buckets: tuple[Bucket, ...], policy: AttemptPolicy, now: Instant) -> tuple[Bucket, ...]:
    """Reserve one attempt in every bucket at once, before any verifier runs; a full bucket refuses all."""
    refreshed = refresh(buckets, policy, now)
    if not admits(refreshed, policy):
        raise identity_mismatch()
    return tuple(replace(bucket, attempts=bucket.attempts + 1) for bucket in refreshed)


def refund(bucket: Bucket, reserved: Bucket) -> Bucket:
    """A verified sign-in returns only its own reservation, and only inside the window that holds it.

    Failure debt from other attempts stays, and a window that rolled over is left untouched.
    """
    if (
        bucket.scope != reserved.scope
        or bucket.key != reserved.key
        or bucket.process_epoch != reserved.process_epoch
        or bucket.anchor_ns != reserved.anchor_ns
        or bucket.attempts < 1
    ):
        return bucket
    return replace(bucket, attempts=bucket.attempts - 1)


def reclaimable(bucket: Bucket, policy: AttemptPolicy, now: Instant) -> bool:
    """A bucket may be deleted to free capacity only when it holds no debt or its window elapsed in this
    epoch; a locked bucket from an earlier epoch is never evicted."""
    return bucket.attempts == 0 or (
        bucket.process_epoch == now.process_epoch
        and bucket.anchor_ns <= now.monotonic_ns
        and now.monotonic_ns - bucket.anchor_ns >= policy.window_ns
    )
