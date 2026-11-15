"""E02 acceptance on the real stack: E01's create_app with its middleware, generated models, ledger, audit,
scheduler and SQLite in a temporary data directory, with E02's identity and consent modules registered.

The crew are the package's SIM roster; an operator principal is added so the console path is exercised.
HTTP goes through the app; owner and operator commands go through the E02 console entry points (R-E02-1,
R-E02-22) against the same running services. No hub service, store, clock service or audience is faked.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response

from crew_health.consent.application.records import RECORD_PURPOSE
from crew_health.consent.cli import Console as OwnerConsole
from crew_health.consent.cli import main as consent_cli
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.session_schema import Session
from crew_health.events.adapters.notifier import BoundedNotifier
from crew_health.events.api.router import stream_events
from crew_health.identity.cli import Console as OperatorConsole
from crew_health.identity.cli import main as identity_cli
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterMember
from crew_health.kernel.uow import Actor

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hub" / "tests" / "privacy"))

from e02_station import (
    OPERATOR,
    START,
    E02Station,
    Running,
    credential,
    mission_id,
    release_id,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
    station,
)

OPERATOR_ID = "operator_sim"
LISTENER = "station:8080"
MINUTE_NS = 60 * 1_000_000_000
PRINCIPALS = (sim_station_principal(), ProvisionedPrincipal(OPERATOR_ID, "operator", "Console operator"))
type Journey = Callable[[AsyncClient], Awaitable[None]]


def stack(directory: Path) -> tuple[E02Station, Running]:
    hub = station(directory)
    running = serve(hub, seed=False)
    seed_roster(running, sim_bootstrap(principals=PRINCIPALS))
    return hub, running


def run(running: Running, journey: Journey) -> None:
    async def main() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=running.app), base_url="http://station:8080"
        ) as client:
            await journey(client)

    asyncio.run(main())


async def sign_in(client: AsyncClient, person: str | None, kind: str = "crew") -> str:
    body: dict[str, object] = {"kind": kind, "credential": credential(person or "stn_galley")}
    if person is not None:
        body["person_id"] = person
    response = await client.post("/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    token = Session.model_validate(response.json()).token
    assert token is not None
    return token


def bearer(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, **headers}


def refused(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert ErrorEnvelope.model_validate(response.json()).code == code


async def capture(client: AsyncClient, token: str, patient: str, key: str) -> Response:
    body = {
        "patient_id": patient,
        "code": "indicator",
        "value": 50,
        "unit": "1",
        "observed_at": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin_mode": "SIM",
        "acquisition_method": "self_report",
    }
    headers = bearer(token, **{"Idempotency-Key": key, "Config-Release": release_id()})
    return await client.post("/v1/observations", headers=headers, json=body)


class Owner:
    """The owner consent console of `crew_health.consent.cli`, run against the hub's own services."""

    def __init__(self, hub: E02Station, running: Running, person: str) -> None:
        self.hub, self.running, self.person = hub, running, person

    def __call__(self, *argv: str) -> list[dict[str, object]]:
        out, err = io.StringIO(), io.StringIO()
        console = OwnerConsole(
            io.StringIO(credential(self.person) + "\n"), out, err, "owner_cli", "console", 1024
        )
        running = self.running
        status = consent_cli(
            (*argv, "--person", self.person),
            running.identity,
            running.consent,
            self.hub.database.clock,
            console,
        )
        assert status == 0, err.getvalue()
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def grant(self, recipient: str, observation_id: str, *extra: str) -> str:
        [line] = self(
            "grant",
            "--recipient",
            recipient,
            "--mission",
            mission_id(),
            "--origin",
            "SIM",
            "--object",
            observation_id,
            "--purpose",
            RECORD_PURPOSE,
            "--action",
            "read",
            "--field",
            "observation",
            *extra,
        )
        return str(line["grant_id"])

    def revoke(self, grant_id: str) -> None:
        [line] = self("revoke", "--grant", grant_id, "--expected-revision", "1")
        assert line["state"] == "revoked"

    def states(self) -> dict[str, object]:
        return {str(line["grant_id"]): line["state"] for line in self("list")}


def operator(running: Running, *argv: str, stdin: str) -> dict[str, object]:
    out, err = io.StringIO(), io.StringIO()
    console = OperatorConsole(io.StringIO(stdin), out, err, "operator_cli", "console", 1_048_576, 1024)
    assert identity_cli(argv, running.admin, console) == 0, err.getvalue()
    value = json.loads(out.getvalue())
    assert isinstance(value, dict)
    return value


@pytest.mark.trace(
    "R02", "E02-01", "E02-03", "V08", "R29", "T-E02-001", "T-E02-003", "T-E02-004", "T-E02-007", "T-E02-013"
)
def test_a_record_is_shared_only_by_its_owners_explicit_grant_and_closes_on_revocation(
    tmp_path: Path,
) -> None:
    hub, running = stack(tmp_path)
    mara = Owner(hub, running, "mara")

    async def journey(client: AsyncClient) -> None:
        tokens = {name: await sign_in(client, name) for name in ("mara", "ilya", "tomas")}
        galley = await sign_in(client, None, "station")
        own = await capture(client, tokens["mara"], "mara", "mara_capture_0001")
        assert own.status_code == 201, own.text
        path = "/v1/observations/" + own.json()["observation_id"]
        assert (await client.get(path, headers=bearer(tokens["mara"]))).status_code == 200
        for name in ("ilya", "tomas"):
            refused(await client.get(path, headers=bearer(tokens[name])), 403, "UNAUTHORIZED_SCOPE")
        refused(await client.get(path, headers=bearer(galley)), 403, "UNAUTHORIZED_SCOPE")
        refused(await capture(client, galley, "mara", "galley_capture_0001"), 403, "UNAUTHORIZED_SCOPE")
        manual = await capture(client, tokens["ilya"], "mara", "ilya_capture_0001")
        assert manual.status_code == 201, manual.text
        assert (manual.json()["patient_id"], manual.json()["operator_id"]) == ("mara", "ilya")
        retry = await capture(client, tokens["ilya"], "mara", "ilya_capture_0001")
        refused(retry, 409, "DUPLICATE_COMMAND")
        moved = await capture(client, tokens["ilya"], "tomas", "ilya_capture_0001")
        assert moved.status_code == 409, moved.text
        refused(
            await capture(client, tokens["tomas"], "mara", "tomas_capture_0001"), 403, "UNAUTHORIZED_SCOPE"
        )
        refused(await client.get(path, headers=bearer(tokens["ilya"])), 403, "UNAUTHORIZED_SCOPE")
        grant_id = mara.grant("ilya", own.json()["observation_id"])
        shared = await client.get(path, headers=bearer(tokens["ilya"]))
        assert shared.status_code == 200 and shared.json()["patient_id"] == "mara"
        refused(await client.get(path, headers=bearer(tokens["tomas"])), 403, "UNAUTHORIZED_SCOPE")
        mara.revoke(grant_id)
        refused(await client.get(path, headers=bearer(tokens["ilya"])), 403, "UNAUTHORIZED_SCOPE")
        assert (await client.get(path, headers=bearer(tokens["mara"]))).status_code == 200
        assert mara.states() == {grant_id: "revoked"}

    run(running, journey)


@pytest.mark.trace("R02", "E02-03", "T-E02-003", "T-E02-010", "T-E02-012", "R-E02-21")
def test_time_ends_finite_grants_and_neither_correction_nor_restart_revives_them(tmp_path: Path) -> None:
    hub, running = stack(tmp_path)
    mara = Owner(hub, running, "mara")
    until = (START + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    observations: list[str] = []

    async def before_restart(client: AsyncClient) -> None:
        owner, officer = await sign_in(client, "mara"), await sign_in(client, "ilya")
        for key in ("mara_capture_0001", "mara_capture_0002", "mara_capture_0003"):
            created = await capture(client, owner, "mara", key)
            assert created.status_code == 201, created.text
            observations.append(created.json()["observation_id"])
        expiring = mara.grant("ilya", observations[0], "--until", until)
        path = "/v1/observations/" + observations[0]
        assert (await client.get(path, headers=bearer(officer))).status_code == 200
        hub.clock.at += timedelta(minutes=10)
        hub.clock.monotonic += 4 * MINUTE_NS
        officer = await sign_in(client, "ilya")
        refused(await client.get(path, headers=bearer(officer)), 403, "UNAUTHORIZED_SCOPE")
        assert mara.states()[expiring] == "active"
        with hub.database.begin(actor=Actor("engine", "engine_scheduler"), correlation_id="sweep") as uow:
            assert hub.database.scheduler.dispatch(uow, limit=10) == 1
        assert mara.states()[expiring] == "expired"
        regressed = mara.grant("ilya", observations[1], "--until", "2026-11-14T10:00:00Z")
        assert (
            await client.get("/v1/observations/" + observations[1], headers=bearer(officer))
        ).status_code == 200
        hub.clock.at -= timedelta(hours=1)
        # E01's write guard checks the durable high water when any write begins: this one detects the
        # regression, freezes clearing and fences finite grants in its own transaction.
        with hub.database.begin(actor=Actor("engine", "engine_clock"), correlation_id="regress"):
            pass
        with hub.database.snapshot() as view:
            frozen = running.clocks.repository.state(view.connection)
        assert frozen is not None and frozen.frozen
        hub.clock.at += timedelta(hours=1)
        refused(
            await client.get("/v1/observations/" + observations[1], headers=bearer(officer)),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        assert mara.states()[regressed] == "fenced"
        # R-E02-21: a frozen clock admits no finite grant, even one issued after the regression.
        frozen_grant = mara.grant("ilya", observations[2], "--until", "2026-11-14T10:00:00Z")
        refused(
            await client.get("/v1/observations/" + observations[2], headers=bearer(officer)),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        # The operator confirms hub time and the sweep lifts the freeze; the confirmation is a correction, so it
        # fences the grant issued while frozen, and only a grant issued afterwards is admitted.
        with hub.database.begin(actor=OPERATOR, correlation_id="time_confirmed") as uow:
            running.clocks.confirm_hub_time(
                uow, correction_id=hub.database.ids.new("correction"), offset_ms=0, uncertainty_ms=0
            )
        running.app.state.runtime.supervisor.run_once()
        assert mara.states()[frozen_grant] == "fenced"
        mara.grant("ilya", observations[2], "--until", "2026-11-14T10:00:00Z")
        assert (
            await client.get("/v1/observations/" + observations[2], headers=bearer(officer))
        ).status_code == 200

    run(running, before_restart)
    restarted = hub.restart(epoch="epoch_second")
    after = serve(restarted, seed=False)

    async def after_restart(client: AsyncClient) -> None:
        officer = await sign_in(client, "ilya")
        for observation_id in observations:
            refused(
                await client.get("/v1/observations/" + observation_id, headers=bearer(officer)),
                403,
                "UNAUTHORIZED_SCOPE",
            )
        assert sorted(map(str, Owner(restarted, after, "mara").states().values())) == [
            "expired",
            "fenced",
            "fenced",
            "fenced",
        ]

    run(after, after_restart)


@pytest.mark.trace("T-E02-008", "T-E02-009", "T-E02-001", "R02")
def test_an_open_event_stream_ends_at_its_next_heartbeat_once_the_member_is_removed(tmp_path: Path) -> None:
    hub, running = stack(tmp_path)
    tokens: dict[str, str] = {}

    async def signed_in(client: AsyncClient) -> None:
        tokens["tomas"] = await sign_in(client, "tomas")
        tokens["galley"] = await sign_in(client, None, "station")
        page = await client.get("/v1/events", headers=bearer(tokens["tomas"], Accept="application/json"))
        assert page.status_code == 200, page.text

    run(running, signed_in)
    session = running.identity.authenticate(tokens["tomas"], listener=LISTENER)
    beats: list[int] = []

    async def stream() -> None:
        async def connected() -> bool:
            return False

        async def quiet(lease: object, seconds: int) -> None:
            beats.append(seconds)
            raise TimeoutError

        notifier = BoundedNotifier(settings=hub.database.settings, ids=hub.database.ids)
        frames = stream_events(
            delivery=running.events,
            lease=notifier.subscribe(),
            settings=hub.database.settings,
            session=session,
            initial=running.events.page(session, after="0"),
            disconnected=connected,
            wait=quiet,
        )
        while await anext(frames) != b": heartbeat\n\n":
            pass
        members = [m for m in sim_members() if m.person_id != "tomas"]
        roster = tmp_path / "roster.json"
        roster.write_text(json.dumps(document(members)), encoding="utf-8")
        removed = operator(
            running,
            "roster",
            "apply",
            str(roster),
            "--expected-revision",
            "1",
            stdin=credential(OPERATOR_ID) + "\n",
        )
        assert removed == {"revision": 2, "removed": ["tomas"]}
        with pytest.raises(StopAsyncIteration):
            await anext(frames)
        assert not notifier.streams

    asyncio.run(stream())
    assert beats == [hub.database.settings.integer("events.heartbeat_seconds")]

    async def after_removal(client: AsyncClient) -> None:
        refused(
            await client.get("/v1/events", headers=bearer(tokens["tomas"], Accept="application/json")),
            401,
            "UNAUTHENTICATED",
        )
        crew = await client.get("/v1/crew", headers=bearer(tokens["galley"]))
        assert [item["person_id"] for item in crew.json()["items"]] == ["mara", "ilya", "sofia"]
        sign_out = await client.delete(
            "/v1/sessions/current", headers=bearer(tokens["galley"], **{"Idempotency-Key": "galley_out_01"})
        )
        assert sign_out.status_code == 204
        refused(
            await client.get("/v1/events", headers=bearer(tokens["galley"], Accept="application/json")),
            401,
            "UNAUTHENTICATED",
        )

    run(running, after_removal)


def document(members: Sequence[RosterMember]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mission_id": mission_id(),
        "namespace": "SIM",
        "members": [asdict(member) for member in members],
        "principals": [asdict(principal) for principal in PRINCIPALS],
    }
