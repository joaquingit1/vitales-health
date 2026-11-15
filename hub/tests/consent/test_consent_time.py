"""Consent across time on the fully composed hub: the scheduler expiry, the clock bus and restart.

A clock correction (real PersistentClocks.apply) or regression fences every finite grant inside the clock's
own transaction; a restart fences finite grants issued by an earlier process before the hub serves.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from e02_station import OPERATOR, START, E02Station, Running, credential, mission_id, serve, station

from crew_health.consent.application.records import RECORD_PURPOSE
from crew_health.consent.domain.grants import Grant, GrantRequest
from crew_health.identity.domain.session import SignInRequest
from crew_health.kernel.clock import ClockCorrection
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import Actor, SessionContext

MINUTE_NS = 60 * 1_000_000_000
LISTENER = "127.0.0.1:8000"
SCHEDULER = Actor("engine", "engine_scheduler")


def owner(running: Running, person_id: str = "mara") -> SessionContext:
    return running.identity.verify_owner(
        person_id, credential(person_id), client="console", correlation_id="c"
    )


def grant(running: Running, recipient: str, *, minutes: int | None) -> Grant:
    value = GrantRequest(
        recipient,
        mission_id(),
        "SIM",
        "obs_1",
        RECORD_PURPOSE,
        frozenset({"read"}),
        frozenset({"observation"}),
        START,
        None if minutes is None else START + timedelta(minutes=minutes),
    )
    return running.consent.grant(owner(running), value, correlation_id="c")


def states(running: Running) -> dict[str, str]:
    return {g.request.recipient: g.state for g in running.consent.own_grants(owner(running)).grants}


def audited(hub: E02Station, event_type: str) -> int:
    with hub.database.snapshot() as view:
        rows = view.connection.rows(
            "SELECT count(*) FROM platform_audit WHERE CAST(envelope AS TEXT) LIKE ?",
            (f'%"{event_type}"%',),
            limit=1,
        )
    value = rows[0][0]
    assert isinstance(value, int)
    return value


@pytest.fixture
def hub(tmp_path: Path) -> E02Station:
    return station(tmp_path)


@pytest.mark.trace("T-E02-010", "R-E02-21", "E02-03")
def test_a_clock_correction_fences_finite_grants_in_its_own_transaction(hub: E02Station) -> None:
    running = serve(hub)
    grant(running, "ilya", minutes=60)
    grant(running, "sofia", minutes=None)
    with hub.database.begin(actor=OPERATOR, correlation_id="sim_correction") as uow:
        running.clocks.apply(
            uow,
            ClockCorrection(
                "sim_correction_2",
                "sim_source",
                "sim_segment",
                2,
                START,
                None,
                -3_600_000,
                0,
                uow.now,
                "operator",
            ),
        )
    assert states(running) == {"ilya": "fenced", "sofia": "active"}
    assert audited(hub, "consent.fenced") == 1
    assert audited(hub, "consent.granted") == 2


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_detected_regression_fences_finite_grants(hub: E02Station) -> None:
    running = serve(hub)
    grant(running, "ilya", minutes=60)
    hub.clock.at -= timedelta(hours=1)
    with hub.database.begin(actor=Actor("engine", "engine_clock"), correlation_id="sim_regression") as uow:
        assert running.clocks.check_regression(uow) is not None
    assert states(running) == {"ilya": "fenced"}


@pytest.mark.trace("T-E02-010", "T-E02-012")
def test_a_restart_fences_earlier_finite_grants_and_reauthentication_cannot_revive_them(
    hub: E02Station,
) -> None:
    running = serve(hub)
    grant(running, "ilya", minutes=60)
    grant(running, "sofia", minutes=None)
    restarted = hub.restart(epoch="epoch_second")
    after = serve(restarted, seed=False)
    assert states(after) == {"ilya": "fenced", "sofia": "active"}
    renewed = grant(after, "ilya", minutes=60)
    by_id = {g.grant_id: g.state for g in after.consent.own_grants(owner(after)).grants}
    assert by_id[renewed.grant_id] == "active"
    assert sorted(by_id.values()) == ["active", "active", "fenced"]


@pytest.mark.trace("T-E02-003", "T-E02-010", "T-E02-013")
def test_the_expiry_job_ends_a_finite_grant_once_and_a_revoked_one_never_fires(hub: E02Station) -> None:
    running = serve(hub)
    grant(running, "ilya", minutes=10)
    cancelled = grant(running, "sofia", minutes=10)
    running.consent.revoke(owner(running), cancelled.grant_id, expected_revision=1, correlation_id="c")
    hub.clock.at += timedelta(minutes=10)
    hub.clock.monotonic += 10 * MINUTE_NS
    with hub.database.begin(actor=SCHEDULER, correlation_id="sim_scheduler") as uow:
        assert hub.database.scheduler.dispatch(uow, limit=10) == 1
    with hub.database.begin(actor=SCHEDULER, correlation_id="sim_scheduler") as uow:
        assert hub.database.scheduler.dispatch(uow, limit=10) == 0
    assert states(running) == {"ilya": "expired", "sofia": "revoked"}


@pytest.mark.trace("T-E02-010")
def test_an_absolute_expiry_is_not_moved_by_a_correction(hub: E02Station) -> None:
    running = serve(hub)
    created = grant(running, "sofia", minutes=None)
    finite = grant(running, "ilya", minutes=30)
    assert finite.request.valid_until == START + timedelta(minutes=30)
    with hub.database.begin(actor=OPERATOR, correlation_id="sim_correction") as uow:
        running.clocks.apply(
            uow,
            ClockCorrection(
                "sim_correction_2",
                "sim_source",
                "sim_segment",
                2,
                START,
                None,
                120_000,
                0,
                uow.now,
                "operator",
            ),
        )
    stored = {g.grant_id: g for g in running.consent.own_grants(owner(running)).grants}
    assert stored[finite.grant_id].request.valid_until == START + timedelta(minutes=30)
    assert stored[created.grant_id].state == "active"
    assert stored[finite.grant_id].state == "fenced"


@pytest.mark.trace("T-E02-010", "T-E02-013")
def test_an_expiry_callback_that_is_malformed_stale_duplicate_or_early_changes_nothing(
    hub: E02Station,
) -> None:
    running = serve(hub)
    finite = grant(running, "ilya", minutes=10)
    open_ended = grant(running, "sofia", minutes=None)
    payloads: tuple[dict[str, str], ...] = (
        {},
        {"grant_id": finite.grant_id},
        {"grant_id": finite.grant_id, "revision": "one"},
        {"grant_id": "cns_absent", "revision": "1"},
        {"grant_id": finite.grant_id, "revision": "2"},
        {"grant_id": open_ended.grant_id, "revision": "1"},
        {"grant_id": finite.grant_id, "revision": "1"},
    )
    for payload in payloads:
        job = ScheduledJob("consent.expiry.probe", "consent.expire", AbsoluteDeadline(START), payload, None)
        with hub.database.begin(actor=SCHEDULER, correlation_id="sim_scheduler") as uow:
            running.consent.expire(uow, job)
    assert states(running) == {"ilya": "active", "sofia": "active"}
    hub.clock.at += timedelta(minutes=10)
    hub.clock.monotonic += 10 * MINUTE_NS
    on_time = ScheduledJob(
        "consent.expiry.probe",
        "consent.expire",
        AbsoluteDeadline(START),
        {"grant_id": finite.grant_id, "revision": "1"},
        None,
    )
    for _ in range(2):
        with hub.database.begin(actor=SCHEDULER, correlation_id="sim_scheduler") as uow:
            running.consent.expire(uow, on_time)
    assert states(running) == {"ilya": "expired", "sofia": "active"}


@pytest.mark.trace("T-E02-021", "T-E02-010", "T-E02-009")
def test_settings_changed_at_restart_neither_extend_a_session_nor_revive_a_grant(hub: E02Station) -> None:
    def sign_in(running: Running) -> tuple[str, timedelta]:
        view = running.identity.sign_in(
            SignInRequest("crew", "mara"),
            credential("mara"),
            client="10.0.0.2",
            listener=LISTENER,
            correlation_id="c",
        )
        assert view.token is not None
        return view.token, view.record.expires_at - view.record.created_at

    running = serve(hub)
    token, life = sign_in(running)
    assert life == timedelta(minutes=5)
    grant(running, "ilya", minutes=60)
    ended = grant(running, "sofia", minutes=None)
    running.consent.revoke(owner(running), ended.grant_id, expected_revision=1, correlation_id="c")
    longer = {"security.idle_lock_minutes": 30, "consent.active_per_subject_max": 256}
    restarted = hub.restart(epoch="epoch_second", settings=longer)
    after = serve(restarted, seed=False)
    with pytest.raises(ProblemError) as error:
        after.identity.authenticate(token, listener=LISTENER)
    assert error.value.code == ErrorCode.UNAUTHENTICATED
    assert states(after) == {"ilya": "fenced", "sofia": "revoked"}
    assert sign_in(after)[1] == timedelta(minutes=30)
