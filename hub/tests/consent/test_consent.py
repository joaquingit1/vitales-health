"""Consent commands, queries and the record authorization binding over a real SQLite station."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from e02_station import (
    OPERATOR,
    START,
    Light,
    credential,
    light,
    mission_id,
    sim_bootstrap,
    sim_members,
    station_settings,
)

from crew_health.consent.application.records import RECORD_PURPOSE, ConsentRecordAuthorization
from crew_health.consent.application.service import ConsentLimits, ConsentService, OwnGrants
from crew_health.consent.application.settings import limits
from crew_health.consent.domain.grants import GrantRequest
from crew_health.identity.domain.session import roles_for
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.domain.observations import RecordContext

SCOPE = (RECORD_PURPOSE, "obs_1")
OBSERVATION = frozenset({"observation"})
MINUTE_NS = 60 * 1_000_000_000


def person(services: Light, person_id: str) -> SessionContext:
    """An owner proven by credential, as the owner CLI does; no session row or token."""
    return services.identity.verify_owner(
        person_id, credential(person_id), client="console", correlation_id="c"
    )


def request(
    recipient: str = "ilya", *, until: timedelta | None = None, fields: frozenset[str] = OBSERVATION
) -> GrantRequest:
    return GrantRequest(
        recipient,
        mission_id(),
        "SIM",
        "obs_1",
        RECORD_PURPOSE,
        frozenset({"read"}),
        fields,
        START,
        None if until is None else START + until,
    )


def released(
    services: Light, recipient: str = "ilya", requested: frozenset[str] = OBSERVATION
) -> frozenset[str]:
    with services.database.snapshot() as view:
        return services.consent.fields(
            view,
            subject_person_id="mara",
            recipient=recipient,
            scope=SCOPE,
            action="read",
            requested=requested,
        )


@pytest.fixture
def services(tmp_path: Path) -> Light:
    return light(tmp_path)


@pytest.mark.trace("T-E02-007", "E02-03", "R02")
def test_the_subject_grants_and_the_recipient_reads_exactly_that(services: Light) -> None:
    created = services.consent.grant(person(services, "mara"), request(), correlation_id="c")
    assert (created.state, created.revision, created.subject_person_id) == ("active", 1, "mara")
    assert released(services) == OBSERVATION
    assert released(services, requested=frozenset({"observation", "note"})) == OBSERVATION
    assert released(services, recipient="sofia") == frozenset()
    with services.database.snapshot() as view:
        assert services.consent.allows(view, subject_person_id="mara", recipient="ilya", scope=SCOPE)
        assert not services.consent.allows(
            view, subject_person_id="mara", recipient="ilya", scope=(RECORD_PURPOSE, "obs_2")
        )
        assert (
            services.consent.fields(
                view,
                subject_person_id="mara",
                recipient="ilya",
                scope=SCOPE,
                action="write",
                requested=OBSERVATION,
            )
            == frozenset()
        )


@pytest.mark.trace("T-E02-007", "T-E02-018")
def test_no_officer_station_operator_or_other_person_consents_for_the_subject(services: Light) -> None:
    others = (
        SessionContext("ses_s", "station", Actor("station", "stn_galley"), None, roles_for("station", None)),
        SessionContext(
            "ses_o", "operator", Actor("operator", "operator_sim"), None, roles_for("operator", None)
        ),
    )
    for session in others:
        with pytest.raises(ProblemError) as error:
            services.consent.grant(session, request(), correlation_id="c")
        assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    created = services.consent.grant(person(services, "mara"), request(), correlation_id="c")
    with pytest.raises(ProblemError) as foreign:
        services.consent.revoke(
            person(services, "ilya"), created.grant_id, expected_revision=1, correlation_id="c"
        )
    assert foreign.value.code == ErrorCode.NOT_FOUND
    assert released(services) == OBSERVATION


@pytest.mark.trace("T-E02-007", "T-E02-020")
def test_self_unknown_and_unbounded_grants_are_refused(services: Light) -> None:
    mara = person(services, "mara")
    for value in (
        request("mara"),
        request("nobody"),
        request(fields=frozenset()),
        request(fields=frozenset({"*"})),
        request(until=timedelta(0)),
    ):
        with pytest.raises(ProblemError) as error:
            services.consent.grant(mara, value, correlation_id="c")
        assert error.value.code in (ErrorCode.INVALID_VALUE, ErrorCode.MALFORMED_REQUEST)


@pytest.mark.trace("T-E02-007", "T-E02-013", "E02-03")
def test_revocation_is_immediate_final_and_versioned(services: Light) -> None:
    mara = person(services, "mara")
    created = services.consent.grant(mara, request(), correlation_id="c")
    revoked = services.consent.revoke(mara, created.grant_id, expected_revision=1, correlation_id="c")
    assert (revoked.state, revoked.revision) == ("revoked", 2)
    assert released(services) == frozenset()
    with pytest.raises(ProblemError) as stale:
        services.consent.revoke(mara, created.grant_id, expected_revision=1, correlation_id="c")
    assert stale.value.code == ErrorCode.STALE_VERSION
    with pytest.raises(ProblemError) as final:
        services.consent.revoke(mara, created.grant_id, expected_revision=2, correlation_id="c")
    assert final.value.code == ErrorCode.INVALID_TRANSITION
    assert [g.state for g in services.consent.own_grants(mara).grants] == ["revoked"]


@pytest.mark.trace("T-E02-003", "T-E02-010")
def test_a_finite_grant_denies_at_its_expiry_before_any_sweep(services: Light) -> None:
    services.consent.grant(person(services, "mara"), request(until=timedelta(minutes=10)), correlation_id="c")
    services.clock.at += timedelta(minutes=9, seconds=59)
    services.clock.monotonic += 9 * MINUTE_NS
    assert released(services) == OBSERVATION
    services.clock.at += timedelta(seconds=1)
    assert released(services) == frozenset()


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_wall_clock_regressing_an_hour_cannot_extend_a_finite_grant(services: Light) -> None:
    services.consent.grant(person(services, "mara"), request(until=timedelta(minutes=10)), correlation_id="c")
    services.clock.at -= timedelta(hours=1)
    services.clock.monotonic += 11 * MINUTE_NS
    assert released(services) == frozenset()


@pytest.mark.trace("T-E02-020", "T-E02-007")
def test_the_active_grant_ceiling_holds_and_revocation_stays_available(tmp_path: Path) -> None:
    services = light(tmp_path, settings={"consent.active_per_subject_max": 2})
    mara = person(services, "mara")
    first = services.consent.grant(mara, request("ilya"), correlation_id="c")
    services.consent.grant(mara, request("sofia"), correlation_id="c")
    with pytest.raises(ProblemError) as full:
        services.consent.grant(mara, request("tomas"), correlation_id="c")
    assert full.value.code == ErrorCode.INVALID_TRANSITION
    services.consent.revoke(mara, first.grant_id, expected_revision=1, correlation_id="c")
    assert services.consent.grant(mara, request("tomas"), correlation_id="c").state == "active"


@pytest.mark.trace("T-E02-001", "T-E02-003")
def test_a_removed_recipient_or_subject_reads_nothing(services: Light) -> None:
    services.consent.grant(person(services, "mara"), request("tomas"), correlation_id="c")
    assert released(services, recipient="tomas") == OBSERVATION
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    services.admin.apply_roster(
        sim_bootstrap(members=members), expected_revision=1, operator=OPERATOR, correlation_id="c"
    )
    assert released(services, recipient="tomas") == frozenset()


def context(patient: str, operator: str) -> RecordContext:
    return RecordContext(
        patient,
        operator,
        mission_id(),
        "SIM",
        TimeInput(START, "sim_source", "sim_segment", 1, "source_raw", "SIM"),
        "c",
    )


@pytest.mark.trace("T-E02-001", "T-E02-003", "R02", "E02-01")
def test_record_reads_need_ownership_or_a_grant_for_every_requested_field(services: Light) -> None:
    authorization = ConsentRecordAuthorization(services.consent, services.identity, RecordRepository())
    sessions = {name: person(services, name) for name in ("mara", "ilya", "sofia")}
    station = SessionContext(
        "ses_s", "station", Actor("station", "stn_galley"), None, roles_for("station", None)
    )

    def read(session: SessionContext, fields: frozenset[str] = OBSERVATION) -> bool:
        with services.database.snapshot() as view:
            try:
                authorization.require_read(
                    view, session, object_ref="obs_1", patient_id="mara", fields=fields
                )
            except ProblemError as error:
                assert error.code == ErrorCode.UNAUTHORIZED_SCOPE
                return False
        return True

    assert read(sessions["mara"])
    assert not read(sessions["ilya"]), "officer status is not a chart grant"
    assert not read(station)
    services.consent.grant(sessions["mara"], request("ilya"), correlation_id="c")
    assert read(sessions["ilya"])
    assert not read(sessions["ilya"], frozenset({"observation", "note"}))
    assert not read(sessions["ilya"], frozenset())
    assert not read(sessions["sofia"])


@pytest.mark.trace("T-E02-004", "R29", "V08")
def test_record_writes_are_self_capture_or_an_officer_entering_for_a_member(services: Light) -> None:
    authorization = ConsentRecordAuthorization(services.consent, services.identity, RecordRepository())
    cases = (
        ("mara", "mara", "mara", True),
        ("ilya", "mara", "ilya", True),
        ("tomas", "mara", "tomas", False),
    )
    for signed_in, patient, operator, allowed in cases:
        session = person(services, signed_in)
        with services.database.begin(actor=session.actor, correlation_id="c") as uow:
            try:
                authorization.require_write(uow, session, context(patient, operator))
                outcome = True
            except ProblemError as error:
                assert error.code == ErrorCode.UNAUTHORIZED_SCOPE
                outcome = False
        assert outcome == allowed, (signed_in, patient)
    station = SessionContext(
        "ses_s", "station", Actor("station", "stn_galley"), None, roles_for("station", None)
    )
    with services.database.begin(actor=station.actor, correlation_id="c") as uow, pytest.raises(ProblemError):
        authorization.require_write(uow, station, context("mara", "mara"))


@pytest.mark.trace("T-E02-001", "T-E02-003", "T-E02-020")
def test_an_unknown_or_blank_subject_recipient_or_scope_releases_nothing(services: Light) -> None:
    services.consent.grant(person(services, "mara"), request("ilya"), correlation_id="c")
    asked: tuple[tuple[str, str, tuple[str, str]], ...] = (
        ("nobody", "ilya", SCOPE),
        ("mara", "nobody", SCOPE),
        ("", "ilya", SCOPE),
        ("mara", "", SCOPE),
        ("mara", "ilya", ("", "obs_1")),
        ("mara", "ilya", (RECORD_PURPOSE, "")),
        ("mara", "ilya", (RECORD_PURPOSE, "obs_2")),
    )
    with services.database.snapshot() as view:
        for subject, recipient, scope in asked:
            given = services.consent.fields(
                view,
                subject_person_id=subject,
                recipient=recipient,
                scope=scope,
                action="read",
                requested=OBSERVATION,
            )
            assert given == frozenset(), (subject, recipient, scope)
    assert released(services) == OBSERVATION


@pytest.mark.trace("T-E02-007", "T-E02-001")
def test_a_subject_removed_after_proving_their_credential_cannot_grant(services: Light) -> None:
    tomas = person(services, "tomas")
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    services.admin.apply_roster(
        sim_bootstrap(members=members), expected_revision=1, operator=OPERATOR, correlation_id="c"
    )
    with pytest.raises(ProblemError) as error:
        services.consent.grant(tomas, request("ilya"), correlation_id="c")
    assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    assert services.consent.own_grants(tomas) == OwnGrants((), older=False)


@pytest.mark.trace("T-E02-020")
def test_consent_limits_outside_their_bounds_are_a_configuration_error() -> None:
    for active, fields in ((0, 1), (257, 1), (1, 0), (1, 257)):
        with pytest.raises(ProblemError) as error:
            ConsentLimits(active, fields)
        assert error.value.code == ErrorCode.CONFIG_INVALID
    assert ConsentLimits(1, 1) == ConsentLimits(1, 1)
    assert ConsentLimits(256, 256).fields_max == 256


@pytest.mark.trace("T-E02-020", "T-E02-007")
def test_the_owner_list_holds_every_active_grant_and_says_when_older_ended_ones_are_left_out(
    tmp_path: Path,
) -> None:
    services = light(tmp_path, settings={"consent.active_per_subject_max": 3})
    mara = person(services, "mara")
    ended = [services.consent.grant(mara, request(), correlation_id="c") for _ in range(3)]
    for grant in ended:
        services.consent.revoke(mara, grant.grant_id, expected_revision=1, correlation_id="c")
    active = {services.consent.grant(mara, request(), correlation_id="c").grant_id for _ in range(3)}
    listed = services.consent.own_grants(mara)
    assert [g.state for g in listed.grants] == ["active"] * 3 + ["revoked"] * 3
    assert {g.grant_id for g in listed.grants[:3]} == active
    assert {g.grant_id for g in listed.grants[3:]} == {g.grant_id for g in ended}
    assert not listed.older
    narrower = ConsentService(replace(services.consent.deps, limits=ConsentLimits(1, 64)))
    shown = narrower.own_grants(mara)
    assert [g.state for g in shown.grants] == ["active"] * 3 + ["revoked"]
    assert {g.grant_id for g in shown.grants[:3]} == active
    assert shown.older


@pytest.mark.trace("T-E02-020")
def test_consent_refuses_to_start_when_storage_cannot_return_every_active_grant() -> None:
    with pytest.raises(ProblemError) as error:
        limits(station_settings(**{"storage.max_rows": 256}))
    assert error.value.code == ErrorCode.CONFIG_INVALID
    assert limits(station_settings(**{"storage.max_rows": 257})).active_per_subject_max == 64
