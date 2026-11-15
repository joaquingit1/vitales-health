"""Sign-in attempt limits of E02 3.2: independent principal, client and global windows."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.identity.domain.limits import (
    GLOBAL_KEY,
    AttemptPolicy,
    Bucket,
    bucket_keys,
    current,
    fresh,
    reclaimable,
    refund,
    reserve,
)
from crew_health.identity.domain.session import Instant
from crew_health.kernel.errors import ErrorCode, ProblemError

AT = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
POLICY = AttemptPolicy(per_identity=5, global_attempts=20, window_ns=300_000_000_000)


def instant(monotonic_ns: int, epoch: str = "epoch_first") -> Instant:
    return Instant(AT, monotonic_ns, epoch)


def triple(principal: str, client: str, now: Instant) -> tuple[Bucket, ...]:
    return (
        fresh("principal", principal, now),
        fresh("client", client, now),
        fresh("global", GLOBAL_KEY, now),
    )


@pytest.mark.trace("T-E02-015", "T-E02-020")
@given(steps=st.lists(st.tuples(st.integers(0, 400_000_000_000), st.booleans()), max_size=60))
def test_no_window_ever_holds_more_than_its_limit(steps: list[tuple[int, bool]]) -> None:
    """Any interleaving of reservations, refunds and elapsed time keeps every bucket within its limit."""
    clock = 0
    buckets = triple("mara", "10.0.0.2", instant(clock))
    for advance, verified in steps:
        clock += advance
        now = instant(clock)
        try:
            reserved = reserve(buckets, POLICY, now)
        except ProblemError as error:
            assert error.code == ErrorCode.IDENTITY_MISMATCH
            assert any(current(b, POLICY, now).attempts >= POLICY.limit(b.scope) for b in buckets)
            continue
        assert all(b.attempts <= POLICY.limit(b.scope) for b in reserved)
        buckets = tuple(refund(b, b) for b in reserved) if verified else reserved


@pytest.mark.trace("T-E02-015")
def test_five_failures_lock_the_identity_until_the_window_elapses() -> None:
    buckets = triple("mara", "10.0.0.2", instant(0))
    for attempt in range(5):
        buckets = reserve(buckets, POLICY, instant(attempt))
    with pytest.raises(ProblemError) as error:
        reserve(buckets, POLICY, instant(10))
    assert error.value.code == ErrorCode.IDENTITY_MISMATCH
    assert reserve(buckets, POLICY, instant(POLICY.window_ns))[0].attempts == 1


@pytest.mark.trace("T-E02-015", "T-E02-010")
def test_a_restart_or_backward_monotonic_reading_keeps_the_debt() -> None:
    locked = tuple(
        replace(b, attempts=5) if b.scope == "principal" else b for b in triple("mara", "c", instant(50))
    )
    for now in (instant(0, "epoch_second"), instant(10)):
        moved = tuple(current(b, POLICY, now) for b in locked)
        assert moved[0].attempts == 5
        with pytest.raises(ProblemError):
            reserve(locked, POLICY, now)


@pytest.mark.trace("T-E02-015")
@given(clients=st.lists(st.text(min_size=1, max_size=12), min_size=6, max_size=12, unique=True))
def test_rotating_clients_cannot_get_past_the_principal_limit(clients: list[str]) -> None:
    principal = fresh("principal", "mara", instant(0))
    global_bucket = fresh("global", GLOBAL_KEY, instant(0))
    admitted = 0
    for client in clients:
        try:
            principal, _, global_bucket = reserve(
                (principal, fresh("client", client, instant(1)), global_bucket), POLICY, instant(1)
            )
            admitted += 1
        except ProblemError:
            pass
    assert admitted == POLICY.per_identity


@pytest.mark.trace("T-E02-015")
def test_unknown_principals_share_one_overflow_bucket_per_client() -> None:
    assert bucket_keys(principal_id=None, client="10.0.0.2").principal == "unknown:10.0.0.2"
    assert bucket_keys(principal_id="mara", client="10.0.0.2").principal == "mara"
    with pytest.raises(ProblemError):
        bucket_keys(principal_id=None, client="")


@pytest.mark.trace("T-E02-015")
def test_a_refund_returns_only_its_own_reservation_in_its_own_window() -> None:
    reserved = reserve(triple("mara", "c", instant(0)), POLICY, instant(0))
    later = tuple(replace(b, anchor_ns=b.anchor_ns + 1) for b in reserved)
    assert [refund(b, r).attempts for b, r in zip(later, reserved, strict=True)] == [1, 1, 1]
    assert [refund(b, b).attempts for b in reserved] == [0, 0, 0]


@pytest.mark.trace("T-E02-020", "T-E02-015")
def test_a_locked_bucket_is_never_evicted_to_admit_another() -> None:
    locked = replace(fresh("principal", "mara", instant(0)), attempts=5)
    assert not reclaimable(locked, POLICY, instant(10))
    assert not reclaimable(replace(locked, process_epoch="epoch_zero"), POLICY, instant(POLICY.window_ns * 2))
    assert reclaimable(locked, POLICY, instant(POLICY.window_ns))
    assert reclaimable(fresh("client", "c", instant(0)), POLICY, instant(1))


@pytest.mark.trace("T-E02-020")
def test_policies_and_buckets_refuse_impossible_values() -> None:
    with pytest.raises(ProblemError):
        AttemptPolicy(0, 1, 1)
    with pytest.raises(ProblemError):
        Bucket("client", "", 0, "e", 0)
    with pytest.raises(ProblemError):
        reserve(triple("mara", "c", instant(0))[:2], POLICY, instant(0))
