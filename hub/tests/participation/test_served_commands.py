"""E22 commands and configuration over the served hub: the owner's consent route (D10) with its Idempotency-Key and
If-Match rules, rebuild, a parked intent, and a configuration edit that changes content with no code change."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from httpx import AsyncClient

from crew_health.kernel.errors import ProblemError

from .e22_served import Hub, basis, bearer, problem, run, session_of, sign_in, start

pytestmark = [pytest.mark.E22, pytest.mark.trace("D10", "R31", "R32", "INV-E22-13")]
ROOT = Path(__file__).resolve().parents[3]
RULES = json.loads((ROOT / "config/participation-rules.json").read_text("utf-8"))


def consent_client(hub: Hub, person: str) -> TestClient:
    """The consent routes are not in the packaged contract yet, so E01's middleware would not match them: serve the
    module's own router in a bare app with the person's session, which is all the middleware would add."""
    app = FastAPI()
    for router in hub.app.state.runtime.registry.declarations["routers"].values():
        app.include_router(cast(APIRouter, router))

    @app.middleware("http")
    async def session(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.session = session_of(person)
        return await call_next(request)

    @app.exception_handler(ProblemError)
    async def refuse(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse({"code": error.code.value}, status_code=error.status)

    return TestClient(app, raise_server_exceptions=False)


def test_consent_route_rules(tmp_path: Path) -> None:
    hub = start(tmp_path, day=3)
    client = consent_client(hub, "mara")
    first = client.get("/v1/me/crew-goal-consent")
    assert first.status_code == 200 and first.json()["enabled"] is False and first.json()["revision"] == 0
    assert first.json()["effective_at"] is None
    body, key = {"enabled": True}, {"Idempotency-Key": "consent_route_1", "If-Match": '"0"'}
    on = client.put("/v1/me/crew-goal-consent", json=body, headers=key)
    assert on.status_code == 200 and on.json()["enabled"] is True and on.json()["revision"] == 1
    # A retry with the same key and body replays: still revision 1.
    again = client.put("/v1/me/crew-goal-consent", json=body, headers=key)
    assert again.status_code == 200 and again.json()["revision"] == 1
    # The same key with another body is a conflict, never a replay.
    other = client.put("/v1/me/crew-goal-consent", json={"enabled": False}, headers=key)
    assert other.status_code == 409 and other.json()["code"] == "DUPLICATE_COMMAND"
    # A stale revision is refused; a missing If-Match is refused; a malformed body is refused.
    stale = client.put(
        "/v1/me/crew-goal-consent",
        json={"enabled": False},
        headers={"Idempotency-Key": "consent_route_2", "If-Match": '"0"'},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
    missing = client.put(
        "/v1/me/crew-goal-consent", json={"enabled": False}, headers={"Idempotency-Key": "consent_route_3"}
    )
    assert missing.status_code == 428 or missing.json()["code"] == "VERSION_REQUIRED"
    junk = client.put(
        "/v1/me/crew-goal-consent",
        json={"enabled": "yes"},
        headers={"Idempotency-Key": "consent_route_4", "If-Match": '"1"'},
    )
    assert junk.status_code == 400
    # Consent is the owner's only: nobody reads another person's state.
    assert consent_client(hub, "ilya").get("/v1/me/crew-goal-consent").json()["enabled"] is False


def test_parked_intent_makes_reads_unavailable_until_rebuild(tmp_path: Path) -> None:
    hub = start(tmp_path, day=3, staff=True)
    hub.produce(basis("mara", 3))
    with hub.station.database.begin(actor=session_of("mara").actor, correlation_id="corrupt_setup") as uow:
        # A stored intent the projector cannot read: the attempts run out and it is parked.
        uow.connection.execute(
            "INSERT INTO participation_intent (intent_key, kind, payload, payload_sha256, origin_mode, patient_id,"
            " enqueued_at) VALUES (?, 'completion', '{}', ?, 'SIM', 'mara', '2026-11-16T00:00:00.000000Z')",
            ("k" * 64, "0" * 64),
        )
        uow.connection.execute(
            "INSERT INTO participation_projection (intent_seq, state, attempts, last_error_code, updated_at)"
            " SELECT intent_seq, 'pending', 0, NULL, '2026-11-16T00:00:00.000000Z' FROM participation_intent"
            " WHERE intent_key = ?",
            ("k" * 64,),
        )

    async def journey(client: AsyncClient) -> None:
        mara = await sign_in(client, "mara")
        unavailable = await client.get("/v1/me/streak", headers=bearer(mara))
        # The view reports unavailable instead of omitting a completion; other people are unaffected.
        problem(unavailable, 500, "INTERNAL_ERROR")
        ilya = await sign_in(client, "ilya")
        assert (await client.get("/v1/me/streak", headers=bearer(ilya))).status_code == 200
        operator = await sign_in(client, None, kind="operator", secret="sim passphrase for operator_sim")
        headers = bearer(operator, **{"Idempotency-Key": "rebuild_key_1"})
        rebuilt = await client.post("/v1/participation/rebuild", json={"origin_mode": "SIM"}, headers=headers)
        assert rebuilt.status_code == 200, rebuilt.text
        result = rebuilt.json()
        assert result["events_voided"] == 0 and result["origin_mode"] == "SIM"
        assert result["views_rebuilt"] == 1  # the parked intent still fails, mara's good completion rebuilds
        replay = await client.post("/v1/participation/rebuild", json={"origin_mode": "SIM"}, headers=headers)
        assert replay.status_code == 200 and replay.json()["result_sha256"] == result["result_sha256"]
        clash = await client.post("/v1/participation/rebuild", json={"origin_mode": "LIVE"}, headers=headers)
        problem(clash, 409, "DUPLICATE_COMMAND")

    run(hub.running, journey)


def propose(rules: dict[str, object]) -> dict[str, object]:
    return {"documents": [{"name": "participation-rules", "document": rules}]}


def test_config_edit_changes_content_and_a_corrupt_document_keeps_the_last_good(tmp_path: Path) -> None:
    hub = start(tmp_path, day=2, staff=True)
    for day in range(2, 9):
        hub.set_day(day)
        hub.produce(basis("mara", day))

    async def journey(client: AsyncClient) -> None:
        editor = await sign_in(client, None, kind="editor", secret="sim passphrase for editor_sim")

        async def patches() -> list[dict[str, object]]:
            reply = await client.get("/v1/me/patches", headers=bearer(await sign_in(client, "mara")))
            assert reply.status_code == 200, reply.text
            return cast(list[dict[str, object]], reply.json()["items"])

        assert [p["threshold"] for p in await patches()] == [7]
        # Corrupt: thresholds out of order. The loader refuses the release; nothing activates.
        broken = copy.deepcopy(RULES)
        broken["patch_thresholds"] = [14, 7, 30]
        refused = await client.post(
            "/v1/config/releases",
            json=propose(broken),
            headers=bearer(editor, **{"Idempotency-Key": "cfg_bad_1"}),
        )
        assert refused.json()["state"] == "rejected" and refused.json()["validation"]["passed"] is False
        assert refused.json()["validation"]["errors"][0]["document"] == "participation-rules"
        assert [p["threshold"] for p in await patches()] == [7]
        # Valid edit: the first threshold becomes 5. Content changes with no code change.
        edited = copy.deepcopy(RULES)
        edited["patch_thresholds"] = [5, 14, 30]
        designs = cast(list[dict[str, object]], edited["patch_designs"])
        designs[0]["threshold"] = 5
        proposed = await client.post(
            "/v1/config/releases",
            json=propose(edited),
            headers=bearer(editor, **{"Idempotency-Key": "cfg_good_1"}),
        )
        assert proposed.status_code == 201, proposed.text
        activated = await client.post(
            f"/v1/config/releases/{proposed.json()['release_id']}/activate",
            headers=bearer(editor, **{"Idempotency-Key": "cfg_good_2", "If-Match": proposed.headers["ETag"]}),
        )
        assert activated.status_code == 200, activated.text
        # The earned 7 stays (an award is never taken back by a rules edit); 5 is newly owed and earned.
        assert sorted(int(str(p["threshold"])) for p in await patches()) == [5, 7]

    run(hub.running, journey)
