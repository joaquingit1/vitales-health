"""E12 resolved by port class from E01's create_app with every installed module (review r1 finding 1): the four
provided ports, the static handler table, the pinned catalog and tokenizer bound at startup, a committed safe
denial, a positive pinned read and fail-closed unavailability."""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_health.app.import_contracts import module_migrations
from crew_health.app.registry import DeclarationRegistry
from crew_health.identity.module import register as register_identity
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ProblemError
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.platform.adapters.migrations import require_expansion
from crew_health.tools.application.ports import (
    ContextBuilderPort,
    EffectApplier,
    OutputPolicyPort,
    ToolGateway,
)
from crew_health.tools.domain.tool_result import ToolCall
from tests.app.test_composition import SOURCE, correct_hub_clock
from tests.tools.e12_station import CREW, E12Hub, e12_hub, install_model, sign_in, tool_scope


def e02_blocker() -> str | None:
    """Why E02 as merged on this branch cannot start main's composed hub, or None once it can. Both faults are E02's
    to fix (its branch predates main's #41): this suite runs on its own as soon as they are gone."""
    for module in ("consent", "identity"):
        for path in module_migrations(SOURCE / module):
            try:
                require_expansion(path.read_text(encoding="utf-8"), module=module)
            except ProblemError:
                return module + " migration fails main's expand-only check"
    registry = DeclarationRegistry(max_ports=64, max_hooks=200)
    register_identity(registry)
    if "identity.roster_applied" not in registry.declarations.get("audit_policies", {}):
        return "identity declares no audit policy for its roster audit"
    return None


BLOCKER = e02_blocker()
pytestmark = pytest.mark.skipif(BLOCKER is not None, reason="E02 blocker: " + str(BLOCKER))

READ = ToolCall("call_sim_a", "read_source_passage", b'{"passage_id":"psg_sim_a","include_parent":false}')


def started(hub: E12Hub) -> tuple[object, object]:
    app = hub.station.app()
    runtime = app.state.runtime
    assert runtime.serving, runtime.fault_code
    correct_hub_clock(runtime)
    return app, runtime


def anchor(runtime: object, hub: E12Hub) -> TimeInput:
    segment = runtime.hub_segment  # type: ignore[attr-defined]
    return TimeInput(
        hub.station.db.clock.now(),
        segment.source_clock_id,
        segment.segment_id,
        segment.capture_offset_version,
        "hub_corrected",
        "SIM",
    )


def audits(hub: E12Hub) -> list[tuple[str, str]]:
    with hub.station.db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT event_type, metadata FROM platform_audit WHERE event_type LIKE 'tools.%' ORDER BY rowid",
            (),
            limit=100,
        )
    return [(str(row[0]), str(row[1])) for row in rows]


def release(hub: E12Hub) -> str:
    with hub.station.db.snapshot() as view:
        return view.release.release_id


@pytest.mark.trace("T-E12-001", "T-E12-004", "T-E12-011", "T-E12-067", "E12-01", "I1", "I7")
def test_create_app_resolves_every_e12_port_and_runs_an_authorized_pinned_read(
    tmp_path: Path, station_resources: Path, store: ManifestStore
) -> None:
    hub = e12_hub(tmp_path, station_resources, installation=install_model(store))
    _, runtime = started(hub)
    resolver = runtime.resolver  # type: ignore[attr-defined]
    for port in (ToolGateway, ContextBuilderPort, OutputPolicyPort, EffectApplier):
        assert resolver.require(port) is not None
    session = sign_in(resolver, CREW[0].person_id)
    scope = tool_scope(session, anchor(runtime, hub), release=release(hub))
    with hub.station.db.begin(actor=session.actor, correlation_id="cor_sim_a") as uow:
        result = resolver.require(ToolGateway).execute(uow, scope, READ)
    assert result.status in {"ok", "no_result"}, result
    assert hub.evidence.reads[0] == "psg_sim_a"
    assert audits(hub) == [
        ("tools.executed", '{"status":"' + result.status + '","tool":"read_source_passage"}')
    ]


@pytest.mark.trace("T-E12-001", "T-E12-004", "R17", "V25", "I5")
def test_a_denial_commits_its_safe_audit_and_never_reaches_the_owner(
    tmp_path: Path, station_resources: Path, store: ManifestStore
) -> None:
    hub = e12_hub(tmp_path, station_resources, installation=install_model(store))
    _, runtime = started(hub)
    resolver = runtime.resolver  # type: ignore[attr-defined]
    session = sign_in(resolver, CREW[0].person_id)
    other = CREW[1].person_id
    calls = (
        ToolCall("call_sim_a", "delete_file", b"{}"),
        ToolCall(
            "call_sim_b",
            "read_source_passage",
            b'{"passage_id":"' + other.encode() + b'","include_parent":false}',
        ),
        ToolCall(
            "call_sim_c",
            "read_source_passage",
            b'{"passage_id":"psg_sim_a","include_parent":false,"person_id":"x"}',
        ),
    )
    scope = tool_scope(session, anchor(runtime, hub), release=release(hub))
    for call in calls:
        with hub.station.db.begin(actor=session.actor, correlation_id="cor_" + call.tool_call_id) as uow:
            assert resolver.require(ToolGateway).execute(uow, scope, call).status == "denied"
    assert hub.evidence.reads == []
    recorded = audits(hub)
    assert [event for event, _ in recorded] == ["tools.denied"] * 3
    assert "delete_file" not in str(recorded) and other not in str(recorded)


@pytest.mark.trace("T-E12-011", "I7")
def test_without_a_model_release_every_tool_is_unavailable_and_the_hub_still_serves(
    tmp_path: Path, station_resources: Path
) -> None:
    hub = e12_hub(tmp_path, station_resources)
    _, runtime = started(hub)
    resolver = runtime.resolver  # type: ignore[attr-defined]
    session = sign_in(resolver, CREW[0].person_id)
    scope = tool_scope(session, anchor(runtime, hub), release=release(hub))
    with hub.station.db.begin(actor=session.actor, correlation_id="cor_sim_a") as uow:
        result = resolver.require(ToolGateway).execute(uow, scope, READ)
    assert (result.status, result.error_code) == ("unavailable", "CAPABILITY_UNAVAILABLE")
    assert hub.evidence.reads == []


@pytest.mark.trace("T-E12-011", "I7")
def test_an_active_release_whose_catalog_e17_refuses_leaves_tools_unavailable_and_the_hub_serving(
    tmp_path: Path, station_resources: Path, store: ManifestStore
) -> None:
    """E17's refusal of a release catalog used to escape the tools binding and stop the hub."""
    hub = e12_hub(tmp_path, station_resources, installation=install_model(store, refused_catalog=True))
    _, runtime = started(hub)
    resolver = runtime.resolver  # type: ignore[attr-defined]
    session = sign_in(resolver, CREW[0].person_id)
    scope = tool_scope(session, anchor(runtime, hub), release=release(hub))
    with hub.station.db.begin(actor=session.actor, correlation_id="cor_sim_a") as uow:
        result = resolver.require(ToolGateway).execute(uow, scope, READ)
    assert (result.status, result.error_code) == ("unavailable", "CAPABILITY_UNAVAILABLE")
    assert hub.evidence.reads == []
