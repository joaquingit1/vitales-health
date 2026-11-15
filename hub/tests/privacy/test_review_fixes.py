"""Regression tests for the eight findings of the E02 review (docs/build/E02/T1.report.md, Review fixes).

Each test reproduces the reviewer's sequence and fails on the code the review read. Service tests run on a light
hub (real SQLite, E01 clock guard, real Argon2); the in-flight read and replay tests run over HTTP on the served
station, whose composition is production's own (app/station.py `production`, app/privacy.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from e02_station import (
    MISSION_EPOCH,
    START,
    Light,
    credential,
    light,
    mission_id,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
    station,
)
from httpx import AsyncClient
from test_http import bearer, problem, record, run, sign_in

from crew_health.app.privacy import ConsentAdapter, ReplayVisibility
from crew_health.catchup.application.ports import GROUND_LOG_SCOPE
from crew_health.consent.application.records import RECORD_PURPOSE, ConsentRecordAuthorization
from crew_health.consent.application.service import ConsentBindings, ConsentService, ObservedWall
from crew_health.consent.domain.grants import GrantRequest, require_object_ref
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.service import IdentityService
from crew_health.identity.application.store import CredentialHasher
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.identity.domain.session import SignInRequest
from crew_health.kernel.commands import AcceptedReceipt, StoredResult, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.records.adapters.repository import RecordRepository

LISTENER = "127.0.0.1:8000"
MINUTE_NS = 60 * 1_000_000_000
OBSERVATION = frozenset({"observation"})
OPERATOR_ID = "operator_sim"


def refused(action: Callable[[], object], code: ErrorCode = ErrorCode.UNAUTHORIZED_SCOPE) -> None:
    with pytest.raises(ProblemError) as error:
        action()
    assert error.value.code == code


def signed_in(services: Light, person_id: str, *, secret: str | None = None) -> SessionContext:
    view = services.identity.sign_in(
        SignInRequest("crew", person_id),
        secret or credential(person_id),
        client="client_" + person_id,
        listener=LISTENER,
        correlation_id="c",
    )
    assert view.token is not None
    return services.identity.authenticate(view.token, listener=LISTENER)


def record_grant(
    recipient: str,
    object_ref: str = "obs_1",
    *,
    mission: str | None = None,
    origin: str = "SIM",
    until: timedelta | None = None,
) -> GrantRequest:
    return GrantRequest(
        recipient,
        mission or mission_id(),
        origin,  # type: ignore[arg-type]
        object_ref,
        RECORD_PURPOSE,
        frozenset({"read"}),
        OBSERVATION,
        START,
        None if until is None else START + until,
    )


def released(services: Light, recipient: str = "ilya", **partition: tuple[str, str]) -> frozenset[str]:
    with services.database.snapshot() as view:
        return services.consent.fields(
            view,
            subject_person_id="mara",
            recipient=recipient,
            scope=(RECORD_PURPOSE, "obs_1"),
            action="read",
            requested=OBSERVATION,
            **partition,  # type: ignore[arg-type]
        )


class Counting:
    """The real verifier, counting how often a sign-in reaches it; `during` runs once inside the first call."""

    def __init__(self, inner: CredentialHasher, during: Callable[[], None] | None = None) -> None:
        self.inner, self.during, self.calls = inner, during, 0

    def hash(self, secret: str) -> str:
        return self.inner.hash(secret)

    def verify(self, verifier: str | None, secret: str) -> bool:
        self.calls += 1
        if self.during is not None:
            during, self.during = self.during, None
            during()
        return self.inner.verify(verifier, secret)


def with_hasher(services: Light, hasher: CredentialHasher) -> tuple[IdentityService, IdentityAdmin]:
    identity = IdentityService(replace(services.identity.deps, hasher=hasher))
    return identity, IdentityAdmin(identity.deps, identity)


# Finding 1: authoring a capture is no read grant ----------------------------------------------------------------


@pytest.mark.trace("T-E02-001", "T-E02-004", "E02-01", "V08")
def test_an_officer_capture_is_returned_once_and_never_read_again_without_consent(tmp_path: Path) -> None:
    """Ilya (acting officer) captures Mara's observation: the 201 carries it, then a GET, a replay of the same
    command and a read after a grant was revoked all refuse; Mara reads her own record throughout."""
    running = serve(station(tmp_path))

    async def journey(client: AsyncClient) -> None:
        ilya, mara = await sign_in(client, "ilya"), await sign_in(client, "mara")
        captured = await record(client, ilya, "mara", "ilya_capture_1")
        assert captured.status_code == 201, captured.text
        body = captured.json()
        assert (body["patient_id"], body["operator_id"], body["value"]) == ("mara", "ilya", 50)
        path = "/v1/observations/" + body["observation_id"]
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        assert (await client.get(path, headers=bearer(mara))).status_code == 200
        replayed = await record(client, ilya, "mara", "ilya_capture_1")
        assert replayed.status_code in (403, 409), replayed.text
        assert "observation_id" not in replayed.json()
        owner = running.identity.verify_owner(
            "mara", credential("mara"), client="console", correlation_id="c"
        )
        grant = running.consent.grant(owner, record_grant("ilya", body["observation_id"]), correlation_id="c")
        assert (await client.get(path, headers=bearer(ilya))).status_code == 200
        running.consent.revoke(owner, grant.grant_id, expected_revision=1, correlation_id="c")
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")

    run(running, journey)


@pytest.mark.trace("T-E02-001", "T-E02-019", "E02-01")
def test_a_replayed_capture_reveals_only_its_receipt_and_only_to_its_live_author(tmp_path: Path) -> None:
    """The replay visibility is production's (app/privacy.py ReplayVisibility), not an actor-only stand-in. A
    replay of an accepted capture answers DUPLICATE_COMMAND with the receipt and never the record; after the
    author's session ends, the same visibility refuses even the receipt."""
    running = serve(station(tmp_path))

    async def journey(client: AsyncClient) -> None:
        mara = await sign_in(client, "mara")
        first = await record(client, mara, "mara", "mara_capture_1")
        assert first.status_code == 201, first.text
        again = problem(await record(client, mara, "mara", "mara_capture_1"), 409, "DUPLICATE_COMMAND")
        assert again.current_state is not None
        assert "observation_id" not in (await record(client, mara, "mara", "mara_capture_1")).json()

    run(running, journey)
    visibility = ReplayVisibility(running.identity)
    view_ = running.identity.sign_in(
        SignInRequest("crew", "mara"), credential("mara"), client="c", listener=LISTENER, correlation_id="c"
    )
    assert view_.token is not None
    session = running.identity.authenticate(view_.token, listener=LISTENER)
    identity = command_identity(
        key="mara_capture_1",
        actor_ref=session.actor.id,
        operation="recordObservation",
        target="/v1/observations",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    receipt = AcceptedReceipt("mara_capture_1", "recordObservation", "/v1/observations", "receipt_1", START)
    stored = StoredResult(receipt, 201, "obs_x", "accepted", None)
    with running.identity.deps.snapshots.snapshot() as view:
        visibility.authorize(view, session, identity, stored)
        other = replace(session, actor=Actor("person", "ilya"))
        refused(lambda: visibility.authorize(view, other, identity, stored))
    with running.identity.deps.uows.begin(actor=Actor("engine", "engine_test"), correlation_id="c") as uow:
        running.identity.end_principal_sessions(uow, "mara", "member_removed")
    with running.identity.deps.snapshots.snapshot() as view:
        refused(lambda: visibility.authorize(view, session, identity, stored))


# Finding 2: a proof is bound to the credential generation it verified -------------------------------------------


def _with_operator(tmp_path: Path) -> Light:
    services = light(tmp_path, seed=False)
    principals = (
        sim_station_principal(),
        ProvisionedPrincipal(OPERATOR_ID, "operator", "Console operator"),
        ProvisionedPrincipal("camera_1", "adapter", "Camera"),
    )
    seed_roster(services, sim_bootstrap(principals=principals))
    return services


@pytest.mark.trace("T-E02-015", "T-E02-012", "E02-6")
def test_an_operator_credential_rotated_during_its_proof_authorizes_no_console_command(
    tmp_path: Path,
) -> None:
    """The console proves the operator credential; while Argon2 runs, the operator rotates it. The proof that
    verified the old credential then neither applies a roster nor issues an adapter token."""
    services = _with_operator(tmp_path)

    def rotate() -> None:
        services.admin.set_credential(
            OPERATOR_ID,
            "rotated operator passphrase",
            current_credential=credential(OPERATOR_ID),
            operator=None,
            client="console_b",
            correlation_id="c",
        )

    _, racing = with_hasher(services, Counting(services.identity.deps.hasher, during=rotate))
    proof = racing.console_operator(lambda: credential(OPERATOR_ID), client="console", correlation_id="c")
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    refused(
        lambda: services.admin.apply_roster(
            sim_bootstrap(members=members), expected_revision=1, operator=proof, correlation_id="c"
        )
    )
    refused(
        lambda: services.admin.issue_adapter_token(
            "camera_1", listener=LISTENER, operator=proof, correlation_id="c"
        )
    )
    current = services.admin.console_operator(
        lambda: "rotated operator passphrase", client="console", correlation_id="c"
    )
    assert services.admin.issue_adapter_token(
        "camera_1", listener=LISTENER, operator=current, correlation_id="c"
    ).token


@pytest.mark.trace("T-E02-015", "T-E02-007", "E02-03")
def test_an_owner_proof_dies_with_the_credential_it_verified(services: Light) -> None:
    """Mara proves her credential for a grant, then her credential changes before the grant commits; and a proof
    racing a change inside the verifier is refused as well."""
    proof = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    services.admin.set_credential(
        "mara",
        "mara rotated",
        current_credential=credential("mara"),
        operator=None,
        client="b",
        correlation_id="c",
    )
    refused(lambda: services.consent.grant(proof, record_grant("ilya"), correlation_id="c"))
    refused(lambda: services.consent.own_grants(proof))

    def rotate_again() -> None:
        services.admin.set_credential(
            "mara",
            "mara again",
            current_credential="mara rotated",
            operator=None,
            client="b",
            correlation_id="c",
        )

    racing, _ = with_hasher(services, Counting(services.identity.deps.hasher, during=rotate_again))
    refused(
        lambda: racing.verify_owner("mara", "mara rotated", client="console", correlation_id="c"),
        ErrorCode.IDENTITY_MISMATCH,
    )
    assert released(services) == frozenset()


# Finding 3: a read in flight is decided again before it is released ---------------------------------------------


@pytest.fixture
def pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Callable[[], None]], None]]:
    """Pauses the next record read inside the GET's own snapshot and runs `during` there, committed separately."""
    armed: list[Callable[[], None]] = []
    original = RecordRepository.observation

    def observation(self: RecordRepository, view: object, observation_id: str) -> object:
        found = original(self, view, observation_id)  # type: ignore[arg-type]
        if armed:
            armed.pop()()
        return found

    monkeypatch.setattr(RecordRepository, "observation", observation)
    yield armed.append
    assert not armed, "the paused read never ran"


@pytest.mark.trace("T-E02-013", "T-E02-015", "E02-03")
def test_a_revocation_or_credential_change_committed_during_a_read_refuses_that_read(
    tmp_path: Path, pause: Callable[[Callable[[], None]], None]
) -> None:
    running = serve(station(tmp_path))
    owner = running.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")

    async def journey(client: AsyncClient) -> None:
        mara, ilya = await sign_in(client, "mara"), await sign_in(client, "ilya")
        created = await record(client, mara, "mara", "mara_capture_1")
        identifier = created.json()["observation_id"]
        path = "/v1/observations/" + identifier
        grant = running.consent.grant(owner, record_grant("ilya", identifier), correlation_id="c")
        assert (await client.get(path, headers=bearer(ilya))).status_code == 200
        pause(
            lambda: running.consent.revoke(
                owner, grant.grant_id, expected_revision=1, correlation_id="revoke"
            )
        )
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")

        again = running.consent.grant(owner, record_grant("ilya", identifier), correlation_id="c")
        assert again.state == "active" and (await client.get(path, headers=bearer(ilya))).status_code == 200
        pause(
            lambda: running.admin.set_credential(
                "ilya",
                "ilya rotated",
                current_credential=credential("ilya"),
                operator=None,
                client="console",
                correlation_id="rotate",
            )
        )
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")

    run(running, journey)


@pytest.mark.trace("T-E02-013", "T-E02-001")
def test_record_authorization_needs_the_session_live_in_the_deciding_view(services: Light) -> None:
    reads = ConsentRecordAuthorization(
        services.consent, services.identity, services.identity, services.database, services.consent.deps.bound
    )
    mara = signed_in(services, "mara")
    with services.database.snapshot() as view:
        reads.require_read(view, mara, object_ref="obs_1", patient_id="mara", fields=OBSERVATION)
    with services.database.begin(actor=Actor("engine", "engine_test"), correlation_id="c") as uow:
        services.identity.end_principal_sessions(uow, "mara", "member_removed")
    with services.database.snapshot() as view:
        refused(
            lambda: reads.require_read(view, mara, object_ref="obs_1", patient_id="mara", fields=OBSERVATION)
        )


# Finding 4: an expiry a read observed never reverses (R-E02-21) -------------------------------------------------


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_read_observed_expiry_survives_a_backward_wall_step_with_zero_snapshot_writes(
    services: Light,
) -> None:
    services.consent.grant(
        services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c"),
        record_grant("ilya", until=timedelta(minutes=10)),
        correlation_id="c",
    )
    services.clock.monotonic += MINUTE_NS
    assert released(services) == OBSERVATION
    before = _changes(services)
    services.clock.at = START + timedelta(minutes=10, seconds=1)
    assert released(services) == frozenset()
    services.clock.at = START + timedelta(minutes=9)
    assert released(services) == frozenset(), "the observed expiry reversed"
    assert _changes(services) == before, "a consent read wrote"


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_regression_below_the_tolerance_never_revives_an_expired_grant(tmp_path: Path) -> None:
    services = light(tmp_path, settings={"clock.regression_tolerance_ms": 5_000})
    owner = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    services.consent.grant(owner, record_grant("ilya", until=timedelta(minutes=10)), correlation_id="c")
    services.clock.monotonic += MINUTE_NS
    services.clock.at = START + timedelta(minutes=10, seconds=1)
    signed_in(services, "sofia")  # a committed write: the durable high water is now past the expiry
    services.clock.at -= timedelta(seconds=2)  # below the 5 s tolerance: E01 does not freeze
    with services.database.snapshot() as view:
        state = services.clocks.repository.state(view.connection)
    assert state is not None and not state.frozen
    # Another reader without this process's observed wall memory: only the durable high water fences it.
    fresh = ConsentService(replace(services.consent.deps, observed=ObservedWall()))
    with services.database.snapshot() as view:
        assert (
            fresh.fields(
                view,
                subject_person_id="mara",
                recipient="ilya",
                scope=(RECORD_PURPOSE, "obs_1"),
                action="read",
                requested=OBSERVATION,
            )
            == frozenset()
        )


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_regression_whose_fence_failed_to_persist_still_admits_no_finite_grant(services: Light) -> None:
    """The wall clock steps back an hour and the write that would persist the freeze fails: nothing frozen is
    stored, yet the snapshot is behind the durable high water, so finite grants deny. With no clock admission
    bound at all, every finite grant denies too."""
    owner = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    services.consent.grant(owner, record_grant("ilya", until=timedelta(minutes=30)), correlation_id="c")
    services.clock.monotonic += MINUTE_NS
    services.clock.at += timedelta(minutes=1)
    signed_in(services, "tomas")
    services.clock.at -= timedelta(hours=1)
    with (
        pytest.raises(RuntimeError),
        services.database.begin(actor=Actor("engine", "engine_test"), correlation_id="c"),
    ):
        raise RuntimeError("storage failed before commit")
    with services.database.snapshot() as view:
        state = services.clocks.repository.state(view.connection)
    assert state is not None and not state.frozen
    assert released(services) == frozenset()
    unbound = ConsentService(
        replace(services.consent.deps, bound=ConsentBindings(None, services.observations))
    )
    services.clock.at += timedelta(hours=1)
    assert released(services) == OBSERVATION
    with services.database.snapshot() as view:
        assert (
            unbound.fields(
                view,
                subject_person_id="mara",
                recipient="ilya",
                scope=(RECORD_PURPOSE, "obs_1"),
                action="read",
                requested=OBSERVATION,
            )
            == frozenset()
        )


def _changes(services: Light) -> tuple[int, ...]:
    with services.database.snapshot() as view:
        return tuple(
            int(str(view.connection.rows(f"SELECT count(*) FROM {table}", (), limit=1)[0][0]))
            for table in ("repl_log", "platform_audit", "consent_grant", "clock_state")
        ) + (hash(view.connection.rows("SELECT last_committed_at FROM clock_state", (), limit=1)),)


# Finding 5: mission and origin are part of every grant match -----------------------------------------------------


@pytest.mark.trace("T-E02-007", "T-E02-003", "E02-03")
def test_a_grant_must_name_its_object_partition_and_releases_only_inside_it(services: Light) -> None:
    owner = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    services.observations.add("obs_live", "mara", origin="LIVE")
    services.observations.add("obs_sofia", "sofia")
    for wrong in (
        record_grant("ilya", mission="another_mission"),
        record_grant("ilya", origin="LIVE"),
        record_grant("ilya", "obs_live"),
        record_grant("ilya", "obs_sofia"),
        record_grant("ilya", "obs_missing"),
    ):
        refused(
            lambda value=wrong: services.consent.grant(owner, value, correlation_id="c"),
            ErrorCode.INVALID_VALUE,
        )
    other = replace(
        record_grant("ilya"), purpose="measurement", object_ref="heart_rate", mission_id="another"
    )
    refused(lambda: services.consent.grant(owner, other, correlation_id="c"), ErrorCode.INVALID_VALUE)
    services.consent.grant(owner, record_grant("ilya"), correlation_id="c")
    assert services.consent.grant(owner, record_grant("ilya", "obs_live", origin="LIVE"), correlation_id="c")
    assert released(services) == OBSERVATION
    assert released(services, partition=(mission_id(), "SIM")) == OBSERVATION
    assert released(services, partition=(mission_id(), "LIVE")) == frozenset()
    assert released(services, partition=("another_mission", "SIM")) == frozenset()


# Finding 6: roster from_day and until_day bound authority on the mission clock -----------------------------------


@pytest.mark.trace("T-E02-020", "T-E02-009", "T-E02-004")
def test_roster_days_bound_sign_in_sessions_consent_and_capture(tmp_path: Path) -> None:
    """START is mission day 2. Ilya (officer) left the roster after day 1, Sofia joins on day 3 and Tomas leaves
    after day 2. Day 3 starts at MISSION_EPOCH + 2 days."""
    services = light(tmp_path, seed=False)
    by_id = {member.person_id: member for member in sim_members()}
    members = (
        by_id["mara"],
        replace(by_id["ilya"], on_roster_from_day=1, on_roster_until_day=1),
        replace(by_id["sofia"], on_roster_from_day=3),
        replace(by_id["tomas"], on_roster_from_day=1, on_roster_until_day=2),
    )
    seed_roster(services, sim_bootstrap(members=members))
    for person in ("ilya", "sofia"):
        refused(lambda person=person: signed_in(services, person), ErrorCode.IDENTITY_MISMATCH)
    tomas = signed_in(services, "tomas")
    with services.database.snapshot() as view:
        assert services.identity.member(view, "ilya") is None
        assert services.identity.member(view, "sofia") is None
        assert not services.identity.may_record(view, tomas, patient_id="mara", operator_id="ilya")
    owner = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    refused(
        lambda: services.consent.grant(owner, record_grant("ilya"), correlation_id="c"),
        ErrorCode.INVALID_VALUE,
    )
    services.consent.grant(owner, record_grant("tomas"), correlation_id="c")
    assert released(services, "tomas") == OBSERVATION
    services.clock.at = MISSION_EPOCH + timedelta(days=2, minutes=1)
    services.clock.monotonic += 60 * MINUTE_NS
    with services.database.snapshot() as view:
        assert not services.identity.alive(view, tomas), "a session outlived its member's last roster day"
        assert services.identity.member(view, "tomas") is None
    assert released(services, "tomas") == frozenset()
    assert signed_in(services, "sofia").person_id == "sofia"
    refused(lambda: signed_in(services, "tomas"), ErrorCode.IDENTITY_MISMATCH)


# Finding 7: catchup's declared consent scope ---------------------------------------------------------------------


@pytest.mark.trace("T-E02-007", "R20")
def test_catchups_exact_scope_is_a_valid_consent_object(services: Light) -> None:
    assert GROUND_LOG_SCOPE == ("ground_log", "catchup/approved/1")
    assert require_object_ref("catchup/approved/1") == "catchup/approved/1"
    for bad in ("catchup//1", "/catchup", "catchup/", "a/b/c/d/e/f/g/h/i", "catchup/approved/1 ", "x" * 256):
        refused(lambda bad=bad: require_object_ref(bad), ErrorCode.INVALID_VALUE)
    owner = services.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    purpose, object_ref = GROUND_LOG_SCOPE
    services.consent.grant(
        owner,
        replace(record_grant("ilya"), purpose=purpose, object_ref=object_ref),
        correlation_id="c",
    )
    port = ConsentAdapter(services.consent)
    with services.database.snapshot() as view:
        assert port.allows(view, subject_person_id="mara", recipient="ilya", scope=GROUND_LOG_SCOPE)
        assert not port.allows(view, subject_person_id="mara", recipient="sofia", scope=GROUND_LOG_SCOPE)


# Finding 8: a locked principal costs the same verifier work as an unknown one ------------------------------------


@pytest.mark.trace("T-E02-009", "T-E02-015")
def test_a_locked_principal_and_an_unknown_one_look_the_same_from_another_client(services: Light) -> None:
    for _ in range(5):
        refused(
            lambda: services.identity.sign_in(
                SignInRequest("crew", "mara"),
                "wrong",
                client="client_a",
                listener=LISTENER,
                correlation_id="c",
            ),
            ErrorCode.IDENTITY_MISMATCH,
        )
    outcomes = []
    for person in ("mara", "nobody"):
        counting = Counting(services.identity.deps.hasher)
        identity, _ = with_hasher(services, counting)
        with pytest.raises(ProblemError) as error:
            identity.sign_in(
                SignInRequest("crew", person),
                credential(person),
                client="client_b",
                listener=LISTENER,
                correlation_id="c",
            )
        outcomes.append((counting.calls, error.value.code, error.value.field_errors, error.value.retryable))
    assert outcomes[0] == outcomes[1] == (1, ErrorCode.IDENTITY_MISMATCH, (), False)


@pytest.fixture
def services(tmp_path: Path) -> Light:
    return light(tmp_path)
