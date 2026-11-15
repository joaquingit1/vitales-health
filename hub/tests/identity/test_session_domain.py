"""Session, principal and role rules of E02 3.1 and 3.2 over injected time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.identity.domain.session import (
    PRINCIPAL_KINDS,
    SIGN_IN_KINDS,
    Instant,
    PrincipalKind,
    PrincipalState,
    SessionLimits,
    SessionRecord,
    SignInRequest,
    actor_for,
    is_live,
    oldest_to_end,
    principal_key,
    roles_for,
)
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Role

START = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
FIVE_MINUTES_NS = 300 * 1_000_000_000
LIMITS = SessionLimits(timedelta(minutes=5), timedelta(hours=24), timedelta(hours=1))


def record(
    *,
    kind: PrincipalKind = "crew",
    created_monotonic_ns: int = 10,
    lifetime: timedelta = timedelta(minutes=5),
    session_id: str = "ses_1",
    created_at: datetime = START,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        principal_id="mara",
        kind=kind,
        person_id="mara" if kind == "crew" else None,
        mission_id="demo-mission",
        generation=1,
        listener="127.0.0.1:8000",
        process_epoch="epoch_first",
        created_at=created_at,
        created_monotonic_ns=created_monotonic_ns,
        expires_at=created_at + lifetime,
        ended_at=None,
        end_reason=None,
    )


LIVE_PRINCIPAL = PrincipalState(1, True, True)


@pytest.mark.trace("T-E02-009", "R02")
def test_crew_needs_a_person_and_other_kinds_refuse_one() -> None:
    assert principal_key(SignInRequest("crew", "mara"), ground_allowed=False) == ("crew", "mara")
    for kind in ("station", "operator", "editor"):
        assert principal_key(SignInRequest(kind, None), ground_allowed=False) == (kind, None)
    refused = (
        SignInRequest("crew", None),
        SignInRequest("station", "mara"),
        SignInRequest("crew", "not a valid id"),
    )
    for request in refused:
        with pytest.raises(ProblemError) as error:
            principal_key(request, ground_allowed=False)
        assert error.value.code in (ErrorCode.IDENTITY_MISMATCH, ErrorCode.MALFORMED_REQUEST)


@pytest.mark.trace("T-E02-009", "E02-6")
def test_ground_signs_in_only_where_the_profile_allows_it() -> None:
    with pytest.raises(ProblemError) as error:
        principal_key(SignInRequest("ground", None), ground_allowed=False)
    assert error.value.code == ErrorCode.IDENTITY_MISMATCH
    assert principal_key(SignInRequest("ground", None), ground_allowed=True) == ("ground", None)


@pytest.mark.trace("T-E02-001", "T-E02-009", "T-E02-018")
@given(kind=st.sampled_from(PRINCIPAL_KINDS), rank=st.one_of(st.none(), st.integers(-3, 5)))
def test_roles_follow_only_from_kind_and_current_rank(kind: PrincipalKind, rank: int | None) -> None:
    roles = roles_for(kind, rank)
    if kind == "crew":
        assert Role.CREW in roles
        assert (Role.CREW_MEDICAL_OFFICER in roles) == (rank in (1, 2))
    else:
        assert len(roles) == 1
        assert Role.CREW not in roles and Role.CREW_MEDICAL_OFFICER not in roles


@pytest.mark.trace("T-E02-009")
def test_editor_is_an_operator_actor_and_adapters_are_never_people() -> None:
    assert actor_for("editor", "editor_1").kind == "operator"
    assert actor_for("adapter", "camera_1").kind == "adapter"
    assert actor_for("crew", "mara").kind == "person"
    assert set(SIGN_IN_KINDS) == set(PRINCIPAL_KINDS) - {"adapter"}


@pytest.mark.trace("T-E02-009")
def test_a_live_session_needs_every_fact() -> None:
    now = Instant(START + timedelta(minutes=1), 10 + 60 * 1_000_000_000, "epoch_first")
    assert is_live(record(), LIVE_PRINCIPAL, now=now, listener="127.0.0.1:8000")
    assert not is_live(record(), None, now=now, listener="127.0.0.1:8000")
    assert not is_live(record(), PrincipalState(2, True, True), now=now, listener="127.0.0.1:8000")
    assert not is_live(record(), PrincipalState(1, False, True), now=now, listener="127.0.0.1:8000")
    assert not is_live(record(), PrincipalState(1, True, False), now=now, listener="127.0.0.1:8000")
    assert not is_live(record(), LIVE_PRINCIPAL, now=now, listener="127.0.0.1:9000")


@pytest.mark.trace("T-E02-010", "R-E02-21")
@given(
    wall_back_s=st.integers(0, 7200),
    elapsed_ns=st.integers(FIVE_MINUTES_NS, FIVE_MINUTES_NS * 20),
)
def test_a_backward_wall_clock_never_extends_a_session(wall_back_s: int, elapsed_ns: int) -> None:
    """The wall clock may stand still or step back an hour; elapsed monotonic time still ends it."""
    now = Instant(
        START - timedelta(seconds=wall_back_s) + timedelta(seconds=1), 10 + elapsed_ns, "epoch_first"
    )
    assert not is_live(record(), LIVE_PRINCIPAL, now=now, listener="127.0.0.1:8000")


@pytest.mark.trace("T-E02-010")
@given(offset_ns=st.integers(0, FIVE_MINUTES_NS - 1))
def test_a_restart_ends_every_session_whatever_the_clocks_say(offset_ns: int) -> None:
    now = Instant(START + timedelta(seconds=1), offset_ns, "epoch_second")
    assert not is_live(record(), LIVE_PRINCIPAL, now=now, listener="127.0.0.1:8000")


@pytest.mark.trace("T-E02-010")
def test_the_absolute_expiry_holds_even_if_monotonic_time_stalls() -> None:
    now = Instant(START + timedelta(minutes=5), 11, "epoch_first")
    assert not is_live(record(), LIVE_PRINCIPAL, now=now, listener="127.0.0.1:8000")
    assert not is_live(
        record(), LIVE_PRINCIPAL, now=Instant(START, 9, "epoch_first"), listener="127.0.0.1:8000"
    )


@pytest.mark.trace("T-E02-009", "T-E02-020")
def test_lifetimes_cap_people_at_the_idle_lock() -> None:
    for kind in ("crew", "operator", "editor", "ground"):
        assert LIMITS.lifetime(kind) == timedelta(minutes=5)
    assert LIMITS.lifetime("station") == timedelta(hours=24)
    assert LIMITS.lifetime("adapter") == timedelta(hours=1)
    with pytest.raises(ProblemError):
        SessionLimits(timedelta(0), timedelta(hours=1), timedelta(hours=1))


@pytest.mark.trace("T-E02-009", "T-E02-020")
@given(count=st.integers(0, 12), per_principal=st.integers(1, 8))
def test_admitting_one_more_session_ends_exactly_the_oldest_excess(count: int, per_principal: int) -> None:
    live = tuple(
        record(session_id=f"ses_{index:02d}", created_at=START + timedelta(seconds=index))
        for index in range(count)
    )
    ended = oldest_to_end(tuple(reversed(live)), per_principal=per_principal)
    assert len(ended) == max(0, count - per_principal + 1)
    assert ended == live[: len(ended)]
    assert count - len(ended) < per_principal


@pytest.mark.trace("T-E02-012")
def test_a_session_record_rejects_impossible_facts() -> None:
    with pytest.raises(ProblemError):
        SessionRecord(
            "ses_1", "stn", "station", "mara", "m", 1, "l", "e", START, 0, START + timedelta(1), None, None
        )
    with pytest.raises(ProblemError):
        SessionRecord("ses_1", "mara", "crew", "mara", "m", 1, "l", "e", START, 0, START, None, None)
    with pytest.raises(ProblemError):
        SessionRecord(
            "ses_1", "mara", "crew", "mara", "m", 1, "l", "e", START, 0, START + timedelta(1), START, None
        )
