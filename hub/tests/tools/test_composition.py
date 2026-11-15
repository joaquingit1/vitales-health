"""E12 resolved by port class from E01's create_app (review r1 finding 1): the four provided ports, the static
handler table, the pinned catalog and tokenizer bound at startup, a committed safe denial and a positive call."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy"))

from e02_station import credential, seed_roster, serve, sim_members  # noqa: E402
from e12_station import E12Station, Models, e12_station, tool_scope  # noqa: E402

from crew_health.contracts_gen.schemas.session_schema import Session  # noqa: E402
from crew_health.kernel.uow import SessionContext  # noqa: E402
from crew_health.tools.application.ports import (  # noqa: E402
    ContextBuilderPort,
    EffectApplier,
    OutputPolicyPort,
    ToolGateway,
)
from crew_health.tools.domain.tool_result import ToolCall  # noqa: E402


def signed_in(hub: E12Station, person: str) -> tuple[object, SessionContext]:
    running = serve(hub, seed=False)
    seed_roster(running)

    async def post() -> str:
        async with AsyncClient(
            transport=ASGITransport(app=running.app), base_url="http://station:8080"
        ) as client:
            response = await client.post(
                "/v1/sessions", json={"kind": "crew", "person_id": person, "credential": credential(person)}
            )
            assert response.status_code == 201, response.text
            token = Session.model_validate(response.json()).token
            assert token is not None
            return token

    token = asyncio.run(post())
    return running, running.identity.authenticate(token, listener="station:8080")


def audits(hub: E12Station) -> list[tuple[str, str]]:
    with hub.database.snapshot() as view:
        rows = view.connection.rows(
            "SELECT event_type, metadata FROM platform_audit WHERE event_type LIKE 'tools.%' ORDER BY rowid",
            (),
            limit=100,
        )
    return [(str(row[0]), str(row[1])) for row in rows]


@pytest.mark.trace("T-E12-001", "T-E12-004", "T-E12-011", "T-E12-067", "E12-01", "I1", "I7")
def test_create_app_resolves_every_e12_port_and_runs_an_authorized_pinned_read(tmp_path: Path) -> None:
    hub = e12_station(tmp_path)
    person = sim_members()[0].person_id
    running, session = signed_in(hub, person)
    resolver = running.app.state.runtime.resolver
    gateway = resolver.require(ToolGateway)
    for port in (ContextBuilderPort, OutputPolicyPort, EffectApplier):
        assert resolver.require(port) is not None
    with hub.database.snapshot() as view:
        release = view.release.release_id
    scope = tool_scope(session, release=release)
    call = ToolCall("call_sim_a", "read_source_passage", b'{"passage_id":"psg_sim_a","include_parent":false}')
    with hub.database.begin(actor=session.actor, correlation_id="cor_sim_a") as uow:
        result = gateway.execute(uow, scope, call)
    assert result.status == "ok", result
    assert result.provenance_ids == ("psg_sim_a",)
    assert b"Rest and fluids" in (result.data or b"")
    assert audits(hub) == [("tools.executed", '{"status":"ok","tool":"read_source_passage"}')]


@pytest.mark.trace("T-E12-001", "T-E12-004", "R17", "V25", "I5")
def test_a_denial_commits_its_safe_audit_and_never_reaches_the_owner(tmp_path: Path) -> None:
    hub = e12_station(tmp_path)
    running, session = signed_in(hub, sim_members()[0].person_id)
    gateway = running.app.state.runtime.resolver.require(ToolGateway)
    with hub.database.snapshot() as view:
        release = view.release.release_id
    other = sim_members()[1].person_id
    calls = (
        ToolCall("call_sim_a", "delete_file", b"{}"),
        ToolCall(
            "call_sim_b",
            "read_source_passage",
            ('{"passage_id":"' + other + '","include_parent":false}').encode(),
        ),
    )
    for call in calls:
        with hub.database.begin(actor=session.actor, correlation_id="cor_" + call.tool_call_id) as uow:
            assert gateway.execute(uow, tool_scope(session, release=release), call).status == "denied"
    assert hub.evidence.reads == []
    recorded = audits(hub)
    assert [event for event, _ in recorded] == ["tools.denied", "tools.denied"]
    assert "delete_file" not in str(recorded) and other not in str(recorded)


@pytest.mark.trace("T-E12-011", "I7")
def test_without_an_active_model_release_every_tool_is_unavailable(tmp_path: Path) -> None:
    hub = e12_station(tmp_path, models=Models(active_release=None))
    running, session = signed_in(hub, sim_members()[0].person_id)
    gateway = running.app.state.runtime.resolver.require(ToolGateway)
    with hub.database.snapshot() as view:
        release = view.release.release_id
    call = ToolCall("call_sim_a", "read_source_passage", b'{"passage_id":"psg_sim_a","include_parent":false}')
    with hub.database.begin(actor=session.actor, correlation_id="cor_sim_a") as uow:
        result = gateway.execute(uow, tool_scope(session, release=release), call)
    assert (result.status, result.error_code) == ("unavailable", "CAPABILITY_UNAVAILABLE")
    assert hub.evidence.reads == []
