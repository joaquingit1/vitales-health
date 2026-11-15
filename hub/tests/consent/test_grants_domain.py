"""E02 3.1 and 3.3 grant rules: explicit, owner-made, exact, finite by absolute instants, terminal once ended."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.consent.domain.grants import (
    POLICY_VERSION,
    Grant,
    GrantRequest,
    GrantState,
    Issue,
    Reading,
    allowed_fields,
    ended,
    grants_now,
    owner,
    validate_request,
)
from crew_health.identity.domain.session import roles_for
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, SessionContext

START = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
MINUTE_NS = 60 * 1_000_000_000
MARA = SessionContext("ses_1", "crew", Actor("person", "mara"), "mara", roles_for("crew", None))
NAMES = st.from_regex(r"[a-z][a-z0-9_]{0,6}", fullmatch=True)


BASE = GrantRequest(
    "ilya",
    "demo-mission",
    "SIM",
    "obs_1",
    "records.read",
    frozenset({"read"}),
    frozenset({"observation"}),
    START,
    START + timedelta(minutes=10),
)


def grant(value: GrantRequest | None = None, *, state: GrantState = "active") -> Grant:
    issued = Grant(
        "cns_1",
        1,
        "mara",
        "mara",
        value or BASE,
        POLICY_VERSION,
        Issue(START, "epoch_first", 0),
        1,
        "active",
        None,
    )
    return issued if state == "active" else ended(issued, state, START)


def reading(minutes: float, *, monotonic_minutes: float | None = None, epoch: str = "epoch_first") -> Reading:
    elapsed = minutes if monotonic_minutes is None else monotonic_minutes
    return Reading(START + timedelta(minutes=minutes), epoch, int(elapsed * MINUTE_NS))


@pytest.mark.trace("T-E02-007", "E02-03")
def test_only_the_authenticated_subject_is_an_owner() -> None:
    assert owner(MARA) == "mara"
    others = (
        SessionContext("ses_2", "station", Actor("station", "stn"), None, roles_for("station", None)),
        SessionContext("ses_3", "operator", Actor("operator", "op"), None, roles_for("operator", None)),
        SessionContext("ses_4", "crew", Actor("person", "ilya"), "mara", roles_for("crew", 1)),
    )
    for session in others:
        with pytest.raises(ProblemError) as error:
            owner(session)
        assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T-E02-007", "T-E02-020")
def test_a_request_must_be_exact_and_prospective() -> None:
    now = START + timedelta(minutes=1)
    accepted = validate_request(MARA, BASE, now=now, fields_max=64)
    assert accepted.valid_from == now
    refused = (
        replace(BASE, recipient="mara"),
        replace(BASE, fields=frozenset()),
        replace(BASE, fields=frozenset({"*"})),
        replace(BASE, actions=frozenset({"Read"})),
        replace(BASE, purpose="records read"),
        replace(BASE, valid_until=now),
        replace(BASE, valid_until=START),
        replace(BASE, fields=frozenset(f"f{i}" for i in range(65))),
    )
    for value in refused:
        with pytest.raises(ProblemError) as error:
            validate_request(MARA, value, now=now, fields_max=64)
        assert error.value.code in (ErrorCode.INVALID_VALUE, ErrorCode.MALFORMED_REQUEST)


@pytest.mark.trace("T-E02-003", "R02", "E02-01")
@given(
    granted=st.frozensets(NAMES, min_size=1, max_size=5),
    requested=st.frozensets(NAMES, max_size=5),
    action=st.sampled_from(("read", "write", "export")),
    minutes=st.floats(-5, 15, allow_nan=False),
)
def test_a_grant_releases_at_most_the_intersection(
    granted: frozenset[str], requested: frozenset[str], action: str, minutes: float
) -> None:
    released = allowed_fields(
        grant(replace(BASE, fields=granted)), reading(max(minutes, 0)), action=action, requested=requested
    )
    assert released <= granted & requested
    if action != "read" or minutes >= 10 or not requested:
        assert released == frozenset()


@pytest.mark.trace("T-E02-003", "T-E02-007")
@given(state=st.sampled_from(("revoked", "expired", "fenced")))
def test_a_terminal_grant_releases_nothing_and_never_changes_again(state: GrantState) -> None:
    terminal = grant(state=state)
    assert terminal.revision == 2
    assert (
        allowed_fields(terminal, reading(1), action="read", requested=frozenset({"observation"}))
        == frozenset()
    )
    targets: tuple[GrantState, ...] = ("active", "revoked")
    for target in targets:
        with pytest.raises(ProblemError) as error:
            ended(terminal, target, START)
        assert error.value.code == ErrorCode.INVALID_TRANSITION


@pytest.mark.trace("T-E02-003")
def test_a_future_grant_and_an_old_policy_version_deny() -> None:
    future = grant(replace(BASE, valid_from=START + timedelta(minutes=5)))
    assert not grants_now(future, reading(1))
    assert grants_now(future, reading(6))
    assert not grants_now(replace(grant(), policy_version="e02.consent.0"), reading(1))


@pytest.mark.trace("T-E02-010", "R-E02-21")
@given(wall_back=st.integers(0, 120), elapsed=st.integers(10, 600))
def test_a_backward_wall_clock_never_extends_a_finite_grant(wall_back: int, elapsed: int) -> None:
    assert not grants_now(grant(), reading(1 - wall_back, monotonic_minutes=elapsed))


@pytest.mark.trace("T-E02-010")
@given(minutes=st.floats(0, 9.9, allow_nan=False))
def test_a_finite_grant_never_survives_its_issuing_process(minutes: float) -> None:
    assert grants_now(grant(), reading(minutes))
    assert not grants_now(grant(), reading(minutes, epoch="epoch_second"))


@pytest.mark.trace("T-E02-003")
def test_an_open_ended_grant_holds_across_processes_until_revoked() -> None:
    standing = grant(replace(BASE, valid_until=None))
    assert grants_now(standing, reading(10_000, epoch="epoch_second"))
    assert not grants_now(ended(standing, "revoked", START), reading(1))
