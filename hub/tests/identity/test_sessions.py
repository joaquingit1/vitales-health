"""Sign-in, per-request authentication, lockout, expiry, restart and roster effects.

Real E01 storage, ledger and audit with the real Argon2 verifier; only time is the SIM clock. Restart and
profile cases run the fully composed hub so its startup phases and module binding are exercised.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from e02_station import (
    OPERATOR,
    E02Station,
    Light,
    Running,
    credential,
    light,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
    station,
)

from crew_health.identity.application.ports import HubProfile
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.identity.domain.session import SignInKind, SignInRequest
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.uow import Actor, Role, SessionContext
from crew_health.platform.storage import SQLiteDatabase

type Services = Running | Light

LISTENER = "127.0.0.1:8000"
CLIENT = "10.0.0.2"
WINDOW_NS = 300_000 * 1_000_000
FIVE_MINUTES = timedelta(minutes=5)


def sign_in(
    running: Services, person: str | None, secret: str, *, kind: SignInKind = "crew", client: str = CLIENT
) -> str:
    view = running.identity.sign_in(
        SignInRequest(kind, person),
        secret,
        client=client,
        listener=LISTENER,
        correlation_id="corr_sign_in",
    )
    assert view.token is not None
    return view.token


def refused(
    running: Services, person: str | None, secret: str, *, kind: SignInKind = "crew", client: str = CLIENT
) -> ProblemError:
    with pytest.raises(ProblemError) as error:
        sign_in(running, person, secret, kind=kind, client=client)
    return error.value


def context(running: Services, token: str, *, listener: str = LISTENER) -> SessionContext:
    return running.identity.authenticate(token, listener=listener)


def dead(running: Services, token: str) -> bool:
    try:
        context(running, token)
    except ProblemError as error:
        assert error.code == ErrorCode.UNAUTHENTICATED
        return True
    return False


def advance(clock: MemoryClock, *, wall: timedelta, monotonic_ns: int) -> None:
    clock.at += wall
    clock.monotonic += monotonic_ns


def end_reasons(db: SQLiteDatabase) -> list[str | None]:
    with db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT end_reason FROM identity_session ORDER BY session_id", (), limit=100
        )
    return [row[0] if isinstance(row[0], str) else None for row in rows]


@pytest.fixture
def hub(tmp_path: Path) -> E02Station:
    return station(tmp_path)


@pytest.fixture
def running(tmp_path: Path) -> Light:
    return light(tmp_path)


@pytest.mark.trace("T-E02-009", "R02")
def test_every_sign_in_kind_gets_roles_from_its_kind_and_rank_only(tmp_path: Path) -> None:
    principals = (
        sim_station_principal(),
        ProvisionedPrincipal("console_operator", "operator", "Console operator"),
        ProvisionedPrincipal("content_editor", "editor", "Content editor"),
    )
    running = light(tmp_path, seed=False)
    seed_roster(running, sim_bootstrap(principals=principals))
    mara = context(running, sign_in(running, "mara", credential("mara")))
    ilya = context(running, sign_in(running, "ilya", credential("ilya")))
    galley = context(running, sign_in(running, None, credential("stn_galley"), kind="station"))
    operator = context(running, sign_in(running, None, credential("console_operator"), kind="operator"))
    editor = context(running, sign_in(running, None, credential("content_editor"), kind="editor"))
    assert (mara.roles, mara.actor, mara.person_id) == (
        frozenset({Role.CREW}),
        Actor("person", "mara"),
        "mara",
    )
    assert ilya.roles == frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})
    assert (galley.roles, galley.person_id, galley.actor.kind) == (frozenset({Role.STATION}), None, "station")
    assert operator.roles == frozenset({Role.OPERATOR})
    assert (editor.roles, editor.actor.kind) == (frozenset({Role.EDITOR}), "operator")


@pytest.mark.trace("T-E02-009", "R02")
def test_wrong_unknown_and_malformed_identities_get_one_generic_refusal(running: Services) -> None:
    failures = (
        refused(running, "mara", "not mara's passphrase"),
        refused(running, "nobody", credential("nobody")),
        refused(running, "mara", credential("ilya")),
        refused(running, None, credential("stn_galley"), kind="crew"),
        refused(running, "mara", credential("stn_galley"), kind="station"),
        refused(running, None, credential("stn_galley"), kind="operator"),
        refused(running, None, credential("stn_galley"), kind="ground"),
    )
    assert {(failure.code, str(failure)) for failure in failures} == {
        (ErrorCode.IDENTITY_MISMATCH, ErrorCode.IDENTITY_MISMATCH.value)
    }
    assert all(not failure.field_errors for failure in failures)


@pytest.mark.trace("T-E02-020")
def test_malformed_credentials_are_refused_before_any_verifier(running: Services) -> None:
    for secret in ("", "x" * 1025, "é" * 513):
        assert refused(running, "mara", secret).code == ErrorCode.MALFORMED_REQUEST
    assert refused(running, "not an id", credential("mara")).code == ErrorCode.MALFORMED_REQUEST


@pytest.mark.trace("T-E02-015", "R02")
def test_five_failures_lock_the_member_until_the_window_elapses(running: Light) -> None:
    for _ in range(5):
        assert refused(running, "mara", "wrong").code == ErrorCode.IDENTITY_MISMATCH
    assert refused(running, "mara", credential("mara"), client="10.0.0.9").code == ErrorCode.IDENTITY_MISMATCH
    advance(running.clock, wall=timedelta(minutes=5), monotonic_ns=WINDOW_NS)
    assert not dead(running, sign_in(running, "mara", credential("mara")))


@pytest.mark.trace("T-E02-015", "T-E02-010")
def test_a_restart_or_a_backward_clock_keeps_the_lockout(hub: E02Station) -> None:
    running = serve(hub)
    for _ in range(5):
        refused(running, "mara", "wrong")
    hub.clock.at -= timedelta(hours=1)
    assert refused(running, "mara", credential("mara")).code == ErrorCode.IDENTITY_MISMATCH
    restarted = hub.restart(epoch="epoch_second")
    after = serve(restarted, seed=False)
    assert refused(after, "mara", credential("mara")).code == ErrorCode.IDENTITY_MISMATCH
    advance(restarted.clock, wall=timedelta(minutes=5), monotonic_ns=WINDOW_NS)
    assert not dead(after, sign_in(after, "mara", credential("mara")))


@pytest.mark.trace("T-E02-015")
def test_rotating_unknown_ids_and_clients_hit_the_global_limit(running: Services) -> None:
    for index in range(20):
        refused(running, f"guess_{index}", "wrong", client=f"10.1.0.{index}")
    assert refused(running, "mara", credential("mara"), client="10.2.0.1").code == ErrorCode.IDENTITY_MISMATCH


@pytest.mark.trace("T-E02-009", "T-E02-020")
def test_a_fifth_session_replaces_the_oldest_one(running: Light) -> None:
    tokens = []
    for _ in range(5):
        tokens.append(sign_in(running, "mara", credential("mara")))
        advance(running.clock, wall=timedelta(seconds=1), monotonic_ns=1_000_000_000)
    assert [dead(running, token) for token in tokens] == [True, False, False, False, False]
    assert end_reasons(running.database).count("replaced") == 1


@pytest.mark.trace("T-E02-020")
def test_the_hub_session_ceiling_refuses_retryably(tmp_path: Path) -> None:
    running = light(tmp_path, settings={"identity.sessions_max": 2})
    sign_in(running, "mara", credential("mara"))
    sign_in(running, "ilya", credential("ilya"))
    failure = refused(running, "sofia", credential("sofia"))
    assert (failure.code, failure.retryable) == (ErrorCode.INTERNAL_ERROR, True)


@pytest.mark.trace("T-E02-010", "T-E02-009")
def test_a_session_ends_at_its_absolute_expiry(running: Light) -> None:
    token = sign_in(running, "mara", credential("mara"))
    advance(running.clock, wall=FIVE_MINUTES - timedelta(seconds=1), monotonic_ns=299 * 1_000_000_000)
    assert not dead(running, token)
    advance(running.clock, wall=timedelta(seconds=1), monotonic_ns=1_000_000_000)
    assert dead(running, token)


@pytest.mark.trace("T-E02-010", "R-E02-21")
def test_a_wall_clock_regressing_an_hour_cannot_extend_a_session(running: Light) -> None:
    token = sign_in(running, "mara", credential("mara"))
    advance(running.clock, wall=-timedelta(hours=1), monotonic_ns=301 * 1_000_000_000)
    assert dead(running, token)


@pytest.mark.trace("T-E02-010", "T-E02-012")
def test_a_restart_ends_every_session_before_serving(hub: E02Station) -> None:
    running = serve(hub)
    tokens = [
        sign_in(running, "mara", credential("mara")),
        sign_in(running, None, credential("stn_galley"), kind="station"),
    ]
    restarted = hub.restart(epoch="epoch_second")
    after = serve(restarted, seed=False)
    assert all(dead(after, token) for token in tokens)
    assert end_reasons(restarted.database).count("restarted") == 2
    assert not dead(after, sign_in(after, "mara", credential("mara")))


@pytest.mark.trace("T-E02-009")
def test_a_token_works_only_on_the_listener_that_issued_it(running: Services) -> None:
    token = sign_in(running, "mara", credential("mara"))
    with pytest.raises(ProblemError) as error:
        context(running, token, listener="0.0.0.0:9000")
    assert error.value.code == ErrorCode.UNAUTHENTICATED
    for bad in ("", "x" * 129, "tökén", token + "x"):
        with pytest.raises(ProblemError):
            context(running, bad)


@pytest.mark.trace("T-E02-015", "T-E02-012")
def test_a_credential_change_ends_sessions_and_retires_the_old_credential(running: Light) -> None:
    token = sign_in(running, "mara", credential("mara"))
    mara = Actor("person", "mara")
    with pytest.raises(ProblemError) as missing:
        running.admin.set_credential(
            "mara",
            "new mara passphrase",
            current_credential=None,
            operator=mara,
            client=CLIENT,
            correlation_id="c",
        )
    assert missing.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as wrong:
        running.admin.set_credential(
            "mara",
            "new mara passphrase",
            current_credential="wrong",
            operator=mara,
            client=CLIENT,
            correlation_id="c",
        )
    assert wrong.value.code == ErrorCode.IDENTITY_MISMATCH
    with running.database.snapshot() as view:
        principal = running.identity.deps.store.principal(view.connection, "mara")
    assert principal is not None
    generation = running.admin.set_credential(
        "mara",
        "new mara passphrase",
        current_credential=credential("mara"),
        operator=mara,
        client=CLIENT,
        correlation_id="c",
    )
    assert generation == principal.generation + 1
    assert dead(running, token)
    assert refused(running, "mara", credential("mara")).code == ErrorCode.IDENTITY_MISMATCH
    assert not dead(running, sign_in(running, "mara", "new mara passphrase"))


@pytest.mark.trace("T-E02-015", "R-E02-16")
def test_an_operator_cannot_reset_an_existing_credential(running: Services) -> None:
    with pytest.raises(ProblemError) as error:
        running.admin.set_credential(
            "mara",
            "operator chosen",
            current_credential=None,
            operator=OPERATOR,
            client=CLIENT,
            correlation_id="c",
        )
    assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T-E02-015")
def test_credential_proof_counts_toward_the_lockout(running: Services) -> None:
    mara = Actor("person", "mara")
    for _ in range(5):
        with pytest.raises(ProblemError):
            running.admin.set_credential(
                "mara",
                "new mara passphrase",
                current_credential="wrong",
                operator=mara,
                client=CLIENT,
                correlation_id="c",
            )
    assert refused(running, "mara", credential("mara")).code == ErrorCode.IDENTITY_MISMATCH


@pytest.mark.trace("T-E02-020")
def test_credentials_are_compared_in_one_unicode_form(running: Services) -> None:
    composed = unicodedata.normalize("NFC", "café crème lunaire")
    running.admin.set_credential(
        "mara",
        composed,
        current_credential=credential("mara"),
        operator=Actor("person", "mara"),
        client=CLIENT,
        correlation_id="c",
    )
    assert not dead(running, sign_in(running, "mara", unicodedata.normalize("NFD", composed)))
    for unsafe in (" leading space", "tab\tinside", "line\nbreak"):
        with pytest.raises(ProblemError) as error:
            running.admin.set_credential(
                "ilya",
                unsafe,
                current_credential=credential("ilya"),
                operator=Actor("person", "ilya"),
                client=CLIENT,
                correlation_id="c",
            )
        assert error.value.code == ErrorCode.INVALID_VALUE


@pytest.mark.trace("T-E02-001", "T-E02-009", "R02")
def test_removing_a_member_ends_their_sessions_and_is_final(running: Light) -> None:
    token = sign_in(running, "tomas", credential("tomas"))
    officer = sign_in(running, "ilya", credential("ilya"))
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    demoted = tuple(replace(m, medical_officer_rank=None) if m.person_id == "ilya" else m for m in members)
    result = running.admin.apply_roster(
        sim_bootstrap(members=demoted), expected_revision=1, operator=OPERATOR, correlation_id="c"
    )
    assert (result.revision, result.removed) == (2, ("tomas",))
    assert dead(running, token)
    assert context(running, officer).roles == frozenset({Role.CREW})
    assert refused(running, "tomas", credential("tomas")).code == ErrorCode.IDENTITY_MISMATCH
    assert "member_removed" in end_reasons(running.database)
    with pytest.raises(ProblemError) as readded:
        running.admin.apply_roster(
            sim_bootstrap(), expected_revision=2, operator=OPERATOR, correlation_id="c"
        )
    assert readded.value.code == ErrorCode.INVALID_TRANSITION
    with pytest.raises(ProblemError) as stale:
        running.admin.apply_roster(
            sim_bootstrap(members=members), expected_revision=1, operator=OPERATOR, correlation_id="c"
        )
    assert stale.value.code == ErrorCode.STALE_VERSION


@pytest.mark.trace("T-E02-001", "T-E02-020")
def test_only_an_operator_applies_a_roster_and_the_mission_is_fixed(running: Services) -> None:
    with pytest.raises(ProblemError) as person:
        running.admin.apply_roster(
            sim_bootstrap(), expected_revision=1, operator=Actor("person", "mara"), correlation_id="c"
        )
    assert person.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as mission:
        running.admin.apply_roster(
            replace(sim_bootstrap(), mission_id="another-mission"),
            expected_revision=1,
            operator=OPERATOR,
            correlation_id="c",
        )
    assert mission.value.code == ErrorCode.INVALID_VALUE


@pytest.mark.trace("T-E02-020", "T-E02-016")
def test_a_saturated_verifier_fails_fast_and_returns_the_attempt(tmp_path: Path) -> None:
    running = light(tmp_path, settings={"identity.auth_wait_ms": 1})
    slots = running.identity.deps.slots.semaphore
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    try:
        for _ in range(7):
            failure = refused(running, "mara", credential("mara"))
            assert (failure.code, failure.retryable) == (ErrorCode.INTERNAL_ERROR, True)
    finally:
        slots.release()
        slots.release()
    assert not dead(running, sign_in(running, "mara", credential("mara")))


@pytest.mark.trace("T-E02-020", "T-E02-015")
def test_the_bucket_ceiling_refuses_without_evicting_a_locked_bucket(tmp_path: Path) -> None:
    running = light(tmp_path, settings={"identity.auth_buckets_max": 4})
    refused(running, "guess", "wrong", client="10.3.0.1")
    assert refused(running, "mara", credential("mara"), client="10.3.0.2").code == ErrorCode.IDENTITY_MISMATCH
    advance(running.clock, wall=timedelta(minutes=5), monotonic_ns=WINDOW_NS)
    assert not dead(running, sign_in(running, "mara", credential("mara"), client="10.3.0.2"))


@pytest.mark.trace("T-E02-009")
def test_adapter_tokens_are_issued_in_process_by_an_operator_only(tmp_path: Path) -> None:
    camera = ProvisionedPrincipal("camera_1", "adapter", "Camera one")
    running = light(tmp_path, seed=False)
    seed_roster(running, sim_bootstrap(principals=(sim_station_principal(), camera)))
    with pytest.raises(ProblemError):
        running.admin.issue_adapter_token(
            "camera_1", listener=LISTENER, operator=Actor("person", "mara"), correlation_id="c"
        )
    view = running.admin.issue_adapter_token(
        "camera_1", listener=LISTENER, operator=OPERATOR, correlation_id="c"
    )
    assert view.token is not None
    adapter = context(running, view.token)
    assert (adapter.kind, adapter.roles, adapter.person_id) == ("adapter", frozenset({Role.ADAPTER}), None)
    assert view.record.expires_at - view.record.created_at == timedelta(hours=1)
    assert refused(running, None, credential("camera_1"), kind="station").code == ErrorCode.IDENTITY_MISMATCH


@pytest.mark.trace("T-E02-013", "T-E02-009")
def test_sign_out_is_idempotent_per_key_and_session(running: Services) -> None:
    first = context(running, sign_in(running, "mara", credential("mara")))
    second = context(running, sign_in(running, "mara", credential("mara")))
    running.identity.sign_out(first, key="signout_key_1", correlation_id="c")
    running.identity.sign_out(first, key="signout_key_1", correlation_id="c")
    with pytest.raises(ProblemError) as conflict:
        running.identity.sign_out(second, key="signout_key_1", correlation_id="c")
    assert conflict.value.code == ErrorCode.DUPLICATE_COMMAND
    with pytest.raises(ProblemError) as ended:
        running.identity.sign_out(first, key="signout_key_2", correlation_id="c")
    assert ended.value.code == ErrorCode.UNAUTHENTICATED
    with running.identity.deps.snapshots.snapshot() as view:
        assert running.identity.is_live(view, second, listener=LISTENER)


@pytest.mark.trace("T-E02-009", "E02-6")
def test_ground_signs_in_only_on_a_ground_or_demo_profile(tmp_path: Path) -> None:
    principals = (sim_station_principal(), ProvisionedPrincipal("ground_team", "ground", "Ground team"))
    flight = serve(station(tmp_path / "flight"), seed=False)
    seed_roster(flight, sim_bootstrap(principals=principals))
    assert refused(flight, None, credential("ground_team"), kind="ground").code == ErrorCode.IDENTITY_MISMATCH
    demo = serve(station(tmp_path / "demo", profile=HubProfile(ground_sign_in=True)), seed=False)
    seed_roster(demo, sim_bootstrap(principals=principals))
    assert context(demo, sign_in(demo, None, credential("ground_team"), kind="ground")).roles == frozenset(
        {Role.GROUND}
    )
