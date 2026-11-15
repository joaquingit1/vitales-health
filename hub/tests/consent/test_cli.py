"""The owner consent console over a real SQLite station: grant, list and revoke, each proven by the owner."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from e02_station import Light, credential, light, mission_id

from crew_health.consent.application.records import RECORD_PURPOSE
from crew_health.consent.cli import Console, main

SCOPE = (RECORD_PURPOSE, "obs_1")


class Run:
    def __init__(self, services: Light) -> None:
        self.services = services
        self.out = ""
        self.err = ""

    def __call__(self, *argv: str, stdin: str = "") -> int:
        out, err = io.StringIO(), io.StringIO()
        console = Console(io.StringIO(stdin), out, err, "cli_test", "console", 1024)
        services = self.services
        status = main(argv, services.identity, services.consent, services.database.clock, console)
        self.out, self.err = out.getvalue(), err.getvalue()
        return status

    def lines(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.out.splitlines()]

    def failure(self) -> str:
        code = json.loads(self.err)["code"]
        assert isinstance(code, str)
        return code


def grant_args(person: str = "mara", recipient: str = "ilya", *extra: str) -> tuple[str, ...]:
    return (
        "grant",
        "--person",
        person,
        "--recipient",
        recipient,
        "--mission",
        mission_id(),
        "--origin",
        "SIM",
        "--object",
        "obs_1",
        "--purpose",
        RECORD_PURPOSE,
        "--action",
        "read",
        "--field",
        "observation",
        *extra,
    )


def released(services: Light, recipient: str = "ilya") -> frozenset[str]:
    with services.database.snapshot() as view:
        return services.consent.fields(
            view,
            subject_person_id="mara",
            recipient=recipient,
            scope=SCOPE,
            action="read",
            requested=frozenset({"observation"}),
        )


@pytest.fixture
def run(tmp_path: Path) -> Run:
    return Run(light(tmp_path))


@pytest.mark.trace("T-E02-007", "E02-03", "R02")
def test_the_owner_grants_lists_and_revokes_and_each_line_says_who_gets_what(run: Run) -> None:
    owner = credential("mara") + "\n"
    assert run(*grant_args(), "--until", "2026-11-14T09:00:00Z", stdin=owner) == 0, run.err
    [granted] = run.lines()
    assert {key: granted[key] for key in ("recipient", "object_ref", "purpose", "actions", "fields")} == {
        "recipient": "ilya",
        "object_ref": "obs_1",
        "purpose": RECORD_PURPOSE,
        "actions": ["read"],
        "fields": ["observation"],
    }
    assert (granted["state"], granted["revision"], granted["valid_until"]) == (
        "active",
        1,
        "2026-11-14T09:00:00+00:00",
    )
    assert released(run.services) == frozenset({"observation"})
    assert run("list", "--person", "mara", stdin=owner) == 0
    assert [line["grant_id"] for line in run.lines()] == [granted["grant_id"]]
    assert run("list", "--person", "ilya", stdin=credential("ilya") + "\n") == 0
    assert run.lines() == []
    grant_id = str(granted["grant_id"])
    assert (
        run("revoke", "--person", "mara", "--grant", grant_id, "--expected-revision", "2", stdin=owner) == 1
    )
    assert run.failure() == "STALE_VERSION"
    assert (
        run("revoke", "--person", "mara", "--grant", grant_id, "--expected-revision", "1", stdin=owner) == 0
    )
    assert run.lines()[0]["state"] == "revoked"
    assert released(run.services) == frozenset()
    assert credential("mara") not in run.out + run.err


@pytest.mark.trace("T-E02-007", "T-E02-001")
def test_nobody_but_the_proven_subject_grants_or_revokes(run: Run) -> None:
    assert run(*grant_args(), stdin="not mara's secret\n") == 1
    assert run.failure() == "IDENTITY_MISMATCH"
    assert run(*grant_args("ilya", "sofia"), stdin=credential("mara") + "\n") == 1
    assert run.failure() == "IDENTITY_MISMATCH"
    assert released(run.services) == frozenset()
    assert run(*grant_args(), stdin=credential("mara") + "\n") == 0
    grant_id = str(run.lines()[0]["grant_id"])
    officer = credential("ilya") + "\n"
    assert (
        run("revoke", "--person", "ilya", "--grant", grant_id, "--expected-revision", "1", stdin=officer) == 1
    )
    assert run.failure() == "NOT_FOUND"
    assert released(run.services) == frozenset({"observation"})


@pytest.mark.trace("T-E02-020", "T-E02-007")
def test_unusable_or_refused_requests_change_nothing(run: Run) -> None:
    owner = credential("mara") + "\n"
    for extra in (("--until", "2026-11-14T09:00:00+01:00"), ("--until", "tomorrow")):
        assert run(*grant_args("mara", "ilya", *extra), stdin=owner) == 2
    assert run(*grant_args()) == 2
    bad_origin = list(grant_args())
    bad_origin[bad_origin.index("SIM")] = "DEMO"
    assert run(*bad_origin, stdin=owner) == 2
    assert run(*grant_args("mara", "mara"), stdin=owner) == 1
    assert run.failure() == "INVALID_VALUE"
    assert run(*grant_args("mara", "nobody"), stdin=owner) == 1
    assert run.failure() == "INVALID_VALUE"
    wildcard = list(grant_args())
    wildcard[wildcard.index("observation")] = "*"
    assert run(*wildcard, stdin=owner) == 1
    assert run("list", "--person", "mara", stdin=owner) == 0
    assert run.lines() == []
