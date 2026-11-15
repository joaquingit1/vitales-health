"""E02 over real HTTP through E01's composition root, middleware, generated models and SQLite.

Each test is one journey on a fresh SIM station. The only in-process step is the owner consent command,
which E02 offers through its owner CLI path, not HTTP (R-E02-1).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from e02_http import bearer, problem, record, run, sign_in
from e02_station import (
    CONSOLE,
    START,
    E02Station,
    credential,
    mission_id,
    release_id,
    serve,
    sim_bootstrap,
    sim_members,
    station,
)
from httpx import AsyncClient

from crew_health.consent.application.records import RECORD_PURPOSE
from crew_health.consent.domain.grants import GrantRequest
from crew_health.contracts_gen.schemas.crew_member_schema import CrewMember
from crew_health.contracts_gen.schemas.session_schema import Session


@pytest.fixture
def hub(tmp_path: Path) -> E02Station:
    return station(tmp_path)


@pytest.mark.trace("T-E02-019", "T-E02-009", "R02")
def test_the_session_lifecycle_follows_the_contract(hub: E02Station) -> None:
    async def journey(client: AsyncClient) -> None:
        created = await client.post(
            "/v1/sessions", json={"kind": "crew", "person_id": "mara", "credential": credential("mara")}
        )
        assert created.status_code == 201, created.text
        assert created.headers["Location"] == "/v1/sessions/current"
        assert created.headers["Config-Release"] == release_id()
        assert created.headers["X-Correlation-Id"]
        session = Session.model_validate(created.json())
        assert session.token is not None and session.person is not None
        assert (session.actor.kind, session.person.person_id.root, session.mission_id.root) == (
            "person",
            "mara",
            mission_id(),
        )
        assert [role.root for role in session.roles] == ["crew"]
        assert (session.expires_at.root - session.created_at.root) == timedelta(minutes=5)
        current = await client.get("/v1/sessions/current", headers=bearer(session.token))
        assert current.status_code == 200
        assert "token" not in current.json()
        assert Session.model_validate(current.json()).session_id == session.session_id
        problem(
            await client.delete("/v1/sessions/current", headers=bearer(session.token)),
            400,
            "MALFORMED_REQUEST",
        )
        short = bearer(session.token, **{"Idempotency-Key": "short"})
        problem(await client.delete("/v1/sessions/current", headers=short), 400, "MALFORMED_REQUEST")
        key = bearer(session.token, **{"Idempotency-Key": "signout_0001"})
        ended = await client.delete("/v1/sessions/current", headers=key)
        assert (ended.status_code, ended.content) == (204, b"")
        problem(
            await client.get("/v1/sessions/current", headers=bearer(session.token)), 401, "UNAUTHENTICATED"
        )
        problem(await client.delete("/v1/sessions/current", headers=key), 401, "UNAUTHENTICATED")
        problem(await client.get("/v1/crew", headers=bearer(session.token)), 401, "UNAUTHENTICATED")

    run(serve(hub), journey)


@pytest.mark.trace("T-E02-009", "T-E02-020", "T-E02-006")
def test_sign_in_failures_are_generic_and_never_echo_the_secret(hub: E02Station) -> None:
    secret = "a wrong secret 9f3c"

    async def journey(client: AsyncClient) -> None:
        wrong = await client.post(
            "/v1/sessions", json={"kind": "crew", "person_id": "mara", "credential": secret}
        )
        unknown = await client.post(
            "/v1/sessions", json={"kind": "crew", "person_id": "nobody", "credential": secret}
        )
        ground = await client.post("/v1/sessions", json={"kind": "ground", "credential": secret})
        for response in (wrong, unknown, ground):
            error = problem(response, 422, "IDENTITY_MISMATCH")
            assert secret not in response.text and "mara" not in response.text
            assert not error.field_errors
        assert {k: v for k, v in wrong.json().items() if k != "correlation_id"} == {
            k: v for k, v in unknown.json().items() if k != "correlation_id"
        }
        malformed: tuple[object, ...] = (
            {},
            {"kind": "crew", "person_id": "mara"},
            {"kind": "admin", "credential": secret},
            {"kind": "crew", "person_id": "mara", "credential": secret, "role": "crew_medical_officer"},
            {"kind": "crew", "person_id": "márá oko", "credential": secret},
            {"kind": "crew", "person_id": "mara", "credential": 1234},
            {"kind": "crew", "person_id": "mara", "credential": ""},
            {"kind": "crew", "person_id": "mara", "credential": "x" * 1025},
            [],
        )
        for body in malformed:
            response = await client.post("/v1/sessions", json=body)
            problem(response, 400, "MALFORMED_REQUEST")
            assert secret not in response.text
        raw = await client.post(
            "/v1/sessions", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert raw.status_code == 400
        token = await sign_in(client, "mara")
        assert token not in (await client.get("/v1/sessions/current", headers=bearer(token))).text

    run(serve(hub), journey)


@pytest.mark.trace("T-E02-019", "T-E02-001", "T-E02-004")
def test_roles_gate_each_operation_and_the_crew_list_pages(hub: E02Station) -> None:
    async def journey(client: AsyncClient) -> None:
        galley = await sign_in(client, None, kind="station")
        current = Session.model_validate(
            (await client.get("/v1/sessions/current", headers=bearer(galley))).json()
        )
        assert (current.person, [r.root for r in current.roles]) == (None, ["station"])
        assert current.expires_at.root - current.created_at.root == timedelta(hours=24)
        problem(await record(client, galley, "mara", "station_capture_1"), 403, "UNAUTHORIZED_SCOPE")
        first = await client.get("/v1/crew", headers=bearer(galley), params={"limit": 2})
        assert first.status_code == 200, first.text
        page = first.json()
        names = [CrewMember.model_validate(item).display_name for item in page["items"]]
        assert names == [member.display_name for member in sim_members()[:2]]
        assert set(page["items"][0]) == {
            "person_id",
            "display_name",
            "roles",
            "on_roster_from_day",
            "location",
            "active_overlays",
            "helping_session_id",
            "medical_officer_rank",
        }
        rest = await client.get(
            "/v1/crew", headers=bearer(galley), params={"cursor": page["page"]["next_cursor"]}
        )
        assert [item["person_id"] for item in rest.json()["items"]] == [
            m.person_id for m in sim_members()[2:]
        ]
        assert rest.json()["page"]["next_cursor"] is None
        for params in (
            {"limit": 0},
            {"limit": 201},
            {"limit": "many"},
            {"cursor": "bm90LWEtY3Vyc29y"},
            {"cursor": "%%"},
        ):
            problem(
                await client.get("/v1/crew", headers=bearer(galley), params=params), 400, "MALFORMED_REQUEST"
            )
        problem(await client.get("/v1/crew"), 401, "UNAUTHENTICATED")
        problem(await client.get("/v1/crew", headers={"Authorization": "Basic abc"}), 401, "UNAUTHENTICATED")

    run(serve(hub), journey)


@pytest.mark.trace("T-E02-001", "T-E02-003", "T-E02-004", "T-E02-007", "R02", "E02-01", "E02-03", "V08")
def test_a_record_is_private_until_its_owner_grants_and_closes_again_on_revocation(hub: E02Station) -> None:
    running = serve(hub)

    async def journey(client: AsyncClient) -> None:
        mara = await sign_in(client, "mara")
        ilya = await sign_in(client, "ilya")
        tomas = await sign_in(client, "tomas")
        created = await record(client, mara, "mara", "mara_capture_1")
        assert created.status_code == 201, created.text
        identifier = created.json()["observation_id"]
        path = "/v1/observations/" + identifier
        assert (await client.get(path, headers=bearer(mara))).status_code == 200
        problem(await client.get(path, headers=bearer(tomas)), 403, "UNAUTHORIZED_SCOPE")
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        officer_capture = await record(client, ilya, "mara", "ilya_capture_1")
        assert officer_capture.status_code == 201, officer_capture.text
        entered = "/v1/observations/" + officer_capture.json()["observation_id"]
        assert (officer_capture.json()["operator_id"], officer_capture.json()["patient_id"]) == (
            "ilya",
            "mara",
        )
        # The capture command returned the officer's entry once; entering it is no later read grant (E02 3.4).
        problem(await client.get(entered, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        assert (await client.get(entered, headers=bearer(mara))).status_code == 200
        problem(await client.get(entered, headers=bearer(tomas)), 403, "UNAUTHORIZED_SCOPE")
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        replay = await record(client, ilya, "mara", "ilya_capture_1")
        problem(replay, 409, "DUPLICATE_COMMAND")
        problem(await record(client, tomas, "mara", "tomas_capture_1"), 403, "UNAUTHORIZED_SCOPE")
        owner = running.identity.verify_owner(
            "mara", credential("mara"), client="console", correlation_id="c"
        )
        grant = running.consent.grant(
            owner,
            GrantRequest(
                "ilya",
                mission_id(),
                "SIM",
                identifier,
                RECORD_PURPOSE,
                frozenset({"read"}),
                frozenset({"observation"}),
                START,
                None,
            ),
            correlation_id="c",
        )
        shared = await client.get(path, headers=bearer(ilya))
        assert shared.status_code == 200 and shared.json()["observation_id"] == identifier
        problem(await client.get(path, headers=bearer(tomas)), 403, "UNAUTHORIZED_SCOPE")
        running.consent.revoke(owner, grant.grant_id, expected_revision=1, correlation_id="c")
        problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        assert (await client.get(path, headers=bearer(mara))).status_code == 200

    run(running, journey)


@pytest.mark.trace("T-E02-009", "T-E02-001", "T-E02-015")
def test_removal_credential_change_and_expiry_end_a_session_mid_flow(hub: E02Station) -> None:
    running = serve(hub)

    async def journey(client: AsyncClient) -> None:
        tomas, mara, sofia = (
            await sign_in(client, "tomas"),
            await sign_in(client, "mara"),
            await sign_in(client, "sofia"),
        )
        members = tuple(member for member in sim_members() if member.person_id != "tomas")
        running.admin.apply_roster(
            sim_bootstrap(members=members), expected_revision=1, operator=CONSOLE, correlation_id="c"
        )
        problem(await client.get("/v1/sessions/current", headers=bearer(tomas)), 401, "UNAUTHENTICATED")
        crew = await client.get("/v1/crew", headers=bearer(mara))
        assert "tomas" not in {item["person_id"] for item in crew.json()["items"]}
        running.admin.set_credential(
            "mara",
            "mara changed passphrase",
            current_credential=credential("mara"),
            operator=CONSOLE,
            client="console",
            correlation_id="c",
        )
        problem(await client.get("/v1/sessions/current", headers=bearer(mara)), 401, "UNAUTHENTICATED")
        assert await sign_in(client, "mara", secret="mara changed passphrase")
        hub.clock.at += timedelta(minutes=5)
        hub.clock.monotonic += 300 * 1_000_000_000
        problem(await client.get("/v1/sessions/current", headers=bearer(sofia)), 401, "UNAUTHENTICATED")

    run(running, journey)


@pytest.mark.trace("T-E02-006", "T-E02-009", "R20")
def test_no_token_or_credential_is_stored_in_plain_text(hub: E02Station) -> None:
    running = serve(hub)
    tokens: list[str] = []

    async def journey(client: AsyncClient) -> None:
        tokens.append(await sign_in(client, "mara"))
        tokens.append(await sign_in(client, None, kind="station"))
        await client.post(
            "/v1/sessions", json={"kind": "crew", "person_id": "ilya", "credential": "ilya typo secret"}
        )

    run(running, journey)
    secrets = [*tokens, credential("mara"), credential("stn_galley"), "ilya typo secret"]
    stored = b"".join(path.read_bytes() for path in sorted((hub.directory / "state").glob("hub.sqlite*")))
    for secret in secrets:
        assert secret.encode("utf-8") not in stored
