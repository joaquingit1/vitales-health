"""The identity console over a real SQLite station: first bootstrap, operator proof, credentials, tokens.

The roster document is the package's SIM crew and galley screen plus an operator and an adapter principal.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from e02_station import Light, credential, light, sim_bootstrap, sim_members, sim_station_principal
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.identity.cli import Console, main, parse_roster
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, validate_bootstrap
from crew_health.identity.domain.session import SignInRequest
from crew_health.kernel.errors import ErrorCode, ProblemError

OPERATOR_ID = "operator_sim"
ADAPTER_ID = "adapter_sim_cuff"
PRINCIPALS = (
    sim_station_principal(),
    ProvisionedPrincipal(OPERATOR_ID, "operator", "Console operator"),
    ProvisionedPrincipal(ADAPTER_ID, "adapter", "Cuff adapter"),
)


def document(roster: RosterBootstrap) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mission_id": roster.mission_id,
        "namespace": roster.namespace,
        "members": [asdict(member) for member in roster.members],
        "principals": [asdict(principal) for principal in roster.principals],
    }


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class Run:
    def __init__(self, services: Light) -> None:
        self.services = services
        self.out = ""
        self.err = ""

    def __call__(self, *argv: str, stdin: str = "") -> int:
        out, err = io.StringIO(), io.StringIO()
        console = Console(io.StringIO(stdin), out, err, "cli_test", "console", 1_048_576, 1024)
        status = main(argv, self.services.admin, console)
        self.out, self.err = out.getvalue(), err.getvalue()
        return status

    def failure(self) -> str:
        code = json.loads(self.err)["code"]
        assert isinstance(code, str)
        return code


def signs_in(services: Light, person: str, secret: str) -> bool:
    try:
        services.identity.sign_in(
            SignInRequest("crew", person), secret, client="kiosk", listener="local", correlation_id="c"
        )
    except ProblemError as error:
        assert error.code == ErrorCode.IDENTITY_MISMATCH
        return False
    return True


@pytest.fixture
def fresh(tmp_path: Path) -> tuple[Run, Path]:
    run = Run(light(tmp_path, seed=False))
    roster = write(tmp_path / "roster.json", document(sim_bootstrap(principals=PRINCIPALS)))
    assert run("roster", "apply", str(roster), "--expected-revision", "0") == 0, run.err
    assert json.loads(run.out) == {"revision": 1, "removed": []}
    return run, tmp_path


@pytest.mark.trace("T-E02-009", "T-E02-015", "T-E02-020")
def test_first_bootstrap_then_every_console_command_needs_the_operator_credential(
    fresh: tuple[Run, Path],
) -> None:
    run, directory = fresh
    assert run("credential", "set", OPERATOR_ID, stdin="operator secret\n") == 0, run.err
    assert run("credential", "set", "mara", stdin="wrong operator secret\n" + credential("mara") + "\n") == 1
    assert run.failure() == "IDENTITY_MISMATCH"
    assert not signs_in(run.services, "mara", credential("mara"))
    assert run("credential", "set", "mara", stdin="operator secret\n" + credential("mara") + "\n") == 0
    assert json.loads(run.out) == {"principal_id": "mara", "generation": 2}
    assert signs_in(run.services, "mara", credential("mara"))
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    later = write(directory / "later.json", document(sim_bootstrap(members=members, principals=PRINCIPALS)))
    assert run("roster", "apply", str(later), "--expected-revision", "1") == 2
    assert "usage" in json.loads(run.err)
    assert run("roster", "apply", str(later), "--expected-revision", "1", stdin="operator secret\n") == 0
    assert json.loads(run.out) == {"revision": 2, "removed": ["tomas"]}
    assert run("roster", "apply", str(later), "--expected-revision", "1", stdin="operator secret\n") == 1
    assert run.failure() == "STALE_VERSION"
    for output in (run.out, run.err):
        assert "operator secret" not in output and credential("mara") not in output


@pytest.mark.trace("T-E02-015", "T-E02-009")
def test_an_owner_changes_a_credential_and_an_operator_cannot_reset_it(fresh: tuple[Run, Path]) -> None:
    run, _ = fresh
    assert run("credential", "set", OPERATOR_ID, stdin="operator secret\n") == 0
    assert run("credential", "set", "ilya", stdin="operator secret\n" + credential("ilya") + "\n") == 0
    assert run("credential", "set", "ilya", stdin="operator secret\nnew by operator\n") == 1
    assert run.failure() == "UNAUTHORIZED_SCOPE"
    assert run("credential", "change", "ilya", stdin="not ilya's secret\nilya new secret\n") == 1
    assert run.failure() == "IDENTITY_MISMATCH"
    assert run("credential", "change", "ilya", stdin=credential("ilya") + "\nilya new secret\n") == 0
    assert json.loads(run.out)["generation"] == 3
    assert signs_in(run.services, "ilya", "ilya new secret")
    assert not signs_in(run.services, "ilya", credential("ilya"))
    assert run("credential", "change", "nobody", stdin="a\nb\n") == 1
    assert run.failure() == "NOT_FOUND"


@pytest.mark.trace("T-E02-009", "T-E02-019")
def test_an_adapter_token_is_printed_once_and_works_only_on_its_listener(fresh: tuple[Run, Path]) -> None:
    run, _ = fresh
    assert run("adapter-token", ADAPTER_ID, "--listener", "127.0.0.1:8080") == 0, run.err
    token = json.loads(run.out)["token"]
    session = run.services.identity.authenticate(token, listener="127.0.0.1:8080")
    assert (session.kind, session.actor.id, session.person_id) == ("adapter", ADAPTER_ID, None)
    with pytest.raises(ProblemError) as other:
        run.services.identity.authenticate(token, listener="127.0.0.1:9090")
    assert other.value.code == ErrorCode.UNAUTHENTICATED
    assert run("adapter-token", "mara", "--listener", "127.0.0.1:8080") == 1
    assert run.failure() == "NOT_FOUND"


@pytest.mark.trace("T-E02-020")
def test_unusable_input_changes_nothing(tmp_path: Path) -> None:
    run = Run(light(tmp_path, seed=False))
    good = document(sim_bootstrap(principals=PRINCIPALS))
    members = good["members"]
    assert isinstance(members, list)
    cases: tuple[tuple[bytes, str], ...] = (
        (b'{"schema_version": "1.0", "schema_version": "1.0"}', "MALFORMED_REQUEST"),
        (
            json.dumps({**good, "members": [{**members[0], "medical_officer_rank": float("nan")}]}).encode(),
            "MALFORMED_REQUEST",
        ),
        (json.dumps({**good, "extra": True}).encode(), "MALFORMED_REQUEST"),
        (json.dumps({**good, "mission_id": "bad id"}).encode(), "MALFORMED_REQUEST"),
        (json.dumps({**good, "members": []}).encode(), "MALFORMED_REQUEST"),
        (b"\xff\xfe", "MALFORMED_REQUEST"),
        (json.dumps({**good, "members": [members[0], members[0]]}).encode(), "INVALID_VALUE"),
        (b" " * 1_048_577, "PAYLOAD_TOO_LARGE"),
    )
    for raw, code in cases:
        path = tmp_path / "doc.json"
        path.write_bytes(raw)
        assert run("roster", "apply", str(path), "--expected-revision", "0") == 1
        assert run.failure() == code
    assert run("roster", "apply", str(tmp_path / "absent.json"), "--expected-revision", "0") == 2
    assert run("roster", "apply", "--expected-revision", "0") == 2
    assert run("credential", "rotate", "mara") == 2
    with run.services.database.snapshot() as view:
        assert run.services.identity.members(view, after_index=None, limit=10) == ()


@pytest.mark.trace("T-E02-020", "R02")
def test_schema_errors_name_the_field_and_never_echo_values(tmp_path: Path) -> None:
    good = document(sim_bootstrap(principals=PRINCIPALS))
    members = good["members"]
    assert isinstance(members, list)
    bad = {**good, "members": [{**members[0], "person_id": "márá oko"}]}
    with pytest.raises(ProblemError) as error:
        parse_roster(json.dumps(bad).encode())
    assert [item.field for item in error.value.field_errors] == ["/members/0/person_id"]
    assert all("márá" not in item.message for item in error.value.field_errors)


@pytest.mark.trace("T-E02-020")
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(name=st.text(min_size=1, max_size=100).filter(lambda value: value.strip() != ""))
def test_a_unicode_display_name_is_kept_exactly_or_the_whole_document_is_refused(name: str) -> None:
    good = document(sim_bootstrap(principals=PRINCIPALS))
    members = good["members"]
    assert isinstance(members, list)
    raw = json.dumps({**good, "members": [{**members[0], "display_name": name}]}).encode()
    shown = not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in name) and len(name.encode("utf-8")) <= 256
    try:
        parsed = validate_bootstrap(parse_roster(raw), roster_max=64, day_span_max=366)
    except ProblemError as error:
        assert not shown
        assert error.code in (ErrorCode.MALFORMED_REQUEST, ErrorCode.INVALID_VALUE)
        return
    assert shown
    assert parsed.members[0].display_name == name


@pytest.mark.trace("T-E02-009", "T-E02-015")
def test_a_console_without_an_operator_on_the_roster_refuses_every_command(tmp_path: Path) -> None:
    run = Run(light(tmp_path))
    later = write(tmp_path / "later.json", document(sim_bootstrap()))
    assert run("roster", "apply", str(later), "--expected-revision", "1", stdin="anything\n") == 1
    assert run.failure() == "UNAUTHORIZED_SCOPE"
    assert run("credential", "set", "mara", stdin="anything\n" + credential("mara") + "\n") == 1
    assert run.failure() == "UNAUTHORIZED_SCOPE"


@pytest.mark.trace("T-E02-015", "T-E02-009")
def test_a_credential_that_was_never_set_cannot_be_changed_into_existence(fresh: tuple[Run, Path]) -> None:
    run, _ = fresh
    assert run("credential", "change", OPERATOR_ID, stdin="guess\nchosen by nobody\n") == 1
    assert run.failure() == "UNAUTHORIZED_SCOPE"
    assert run("credential", "set", OPERATOR_ID, stdin="operator secret\n") == 0
