"""Roles uses help's class and exposes its consumer-owned broadcast port."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import Request

from crew_health.app.registry import DeclarationRegistry
from crew_health.app.workers import ModuleWorkers
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.help.application.ports import BroadcastRolesSource
from crew_health.help.module import register as register_help
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import WorkerLifecycle
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, SessionContext, UnitOfWorkFactory
from crew_health.roles.api.router import RequestAuthenticator
from crew_health.roles.application.ports import (
    DayLabelPort,
    HelpSessionReader,
    MissionStatePort,
    OverlayPort,
    RoleAssignmentReader,
    RosterPort,
)
from crew_health.roles.application.settings import RolesSettings
from crew_health.roles.domain.errors import PortUnavailable
from crew_health.roles.module import register

from .harness import Harness, assignments, authenticate, by_role, help_session, person_of


class Authenticator:
    def session(self, request: Request, operation_id: str) -> SessionContext:
        return authenticate(request, operation_id)


@pytest.mark.trace("R54", "SYSTEM-6.4")
def test_help_registers_the_reader_roles_consumes() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register_help(registry)
    assert HelpSessionReader in registry.providers


@pytest.mark.trace("R54", "V71", "SYSTEM-6.4")
def test_roles_registers_helps_broadcast_source() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    assert BroadcastRolesSource in registry.providers


@pytest.mark.trace("R54", "SYSTEM-5.12")
def test_roles_provider_factories_resolve_before_dependencies_bind() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    # Exercise the real factory resolver without invoking the service bindings.
    registry.bindings.clear()
    resolver = registry.resolve()
    reader = resolver.require(RoleAssignmentReader)  # type: ignore[type-abstract]
    with pytest.raises(ProblemError) as caught:
        reader.get("rol_unknown")
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert caught.value.retryable


@pytest.mark.trace("R54", "SYSTEM-5.12", "V71")
def test_bound_roles_facades_serve_the_resolved_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.open_help(help_session("hlp_bound", subject="mara", flow="not_breathing"))
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    # The test drives recomputation explicitly; the real binding still builds every service and sweeps work.
    monkeypatch.setattr("crew_health.roles.module.run_worker", lambda *_: None)
    providers: tuple[tuple[type[object], object], ...] = (
        (UnitOfWorkFactory, h.deps.uow),
        (ReadSnapshotFactory, h.deps.snapshots),
        (WorkerLifecycle, ModuleWorkers(maximum=8, monotonic_ns=h.clock.monotonic_ns)),
        (Clock, h.clock),
        (IdFactory, h.ids),
        (HubSettings, MemorySettings(RolesSettings.specs(), RolesSettings.defaults())),
        (IdempotencyStore, h.deps.idempotency),
        (RosterPort, h.deps.roster),
        (OverlayPort, h.deps.overlays),
        (MissionStatePort, h.deps.mission),
        (DayLabelPort, h.deps.day_labels),
        (HelpSessionReader, h.deps.help_sessions),
        (ConfigReader, h.deps.config_reader),
        (RequestAuthenticator, Authenticator()),
    )
    for port, value in providers:
        registry.provide(port, lambda _, value=value: value)
    resolver = registry.resolve()
    source = resolver.require(BroadcastRolesSource)  # type: ignore[type-abstract]
    reader = resolver.require(RoleAssignmentReader)  # type: ignore[type-abstract]
    actual = source.roles_for_session("hlp_bound")
    assert actual
    for role in actual:
        record = reader.get(role.role_assignment_id)
        assert record is not None and record.person_id == role.person_id and not record.retired
    with h.uow.begin(actor=Actor("engine", "test"), correlation_id="cor_bound") as uow:
        pending = h.deps.repository(uow.connection).due_recomputes(
            h.clock.now(), h.deps.settings.recompute_batch
        )
    assert any(row.context_id == "hlp_bound" for row in pending)


@pytest.mark.trace("R54", "V71")
def test_broadcast_roles_list_only_current_session_assignments(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.open_help(help_session("hlp_broadcast", subject="mara", flow="not_breathing"))
    h.open_help(help_session("hlp_other", subject="sofia", flow="not_breathing"))
    rows = assignments(h.get("/v1/help-sessions/hlp_broadcast/roles", "mara")[1])
    actual = h.provided.roles_for_session("hlp_broadcast")
    assert {r.role_assignment_id for r in actual} == {r["role_assignment_id"] for r in rows}
    assert actual
    for item in actual:
        row = next(r for r in rows if r["role_assignment_id"] == item.role_assignment_id)
        assert item.role_id == row["role_id"]
        assert item.label == row["label"]
        assert item.state == row["state"]
        member = next(m for m in h.facts.roster if m.person_id == item.person_id)
        assert item.display_name == member.display_name
    assert not h.provided.roles_for_session("hlp_missing")


@pytest.mark.trace("R54", "V71", "AGENTS-6-Safety")
def test_broadcast_roles_fail_closed_without_roster(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.open_help(help_session("hlp_broadcast", subject="mara", flow="not_breathing"))
    h.facts.down.add("roster")
    with pytest.raises(PortUnavailable):
        h.provided.roles_for_session("hlp_broadcast")
    h.facts.down.clear()
    h.facts.roster = [replace(m, display_name=m.display_name + " updated") for m in h.facts.roster]
    assert all(r.display_name.endswith(" updated") for r in h.provided.roles_for_session("hlp_broadcast"))


@pytest.mark.trace("R54", "V71", "EI9")
def test_broadcast_roles_keep_decision_states_and_drop_retired_assignments(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    session_id = "hlp_broadcast"
    h.open_help(help_session(session_id, subject="mara", flow="not_breathing"))
    officer = by_role(h.roles(session_id))["medical_officer"]
    assignment_id = officer["role_assignment_id"]
    status, accepted = h.post(
        f"/v1/help-sessions/{session_id}/roles",
        person_of(officer),
        {"action": "accept", "role_assignment_id": assignment_id, "client_time": "2026-11-13T09:40:00Z"},
        key="broadcast-accept",
        if_match=1,
    )
    assert status == 200, accepted
    assert (
        next(
            r for r in h.provided.roles_for_session(session_id) if r.role_assignment_id == assignment_id
        ).state
        == "accepted"
    )
    status, changed = h.post(
        f"/v1/help-sessions/{session_id}/roles",
        "mara",
        {
            "action": "reassign",
            "role_assignment_id": assignment_id,
            "person_id": "tomas",
            "client_time": "2026-11-13T09:40:00Z",
        },
        key="broadcast-reassign",
        if_match=2,
    )
    assert status == 200, changed
    current = next(
        r for r in h.provided.roles_for_session(session_id) if r.role_assignment_id == assignment_id
    )
    assert current.state == "changed" and current.person_id == "tomas"
    h.facts.walk("tomas")
    h.overlay_event("tomas", "eva", "active")
    assert assignment_id not in {r.role_assignment_id for r in h.provided.roles_for_session(session_id)}


@pytest.mark.trace("R54", "V71", "AGENTS-6-Safety")
def test_broadcast_roles_fail_closed_when_an_assignee_leaves_the_roster(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.open_help(help_session("hlp_broadcast", subject="mara", flow="not_breathing"))
    assignee = h.provided.roles_for_session("hlp_broadcast")[0].person_id
    h.facts.roster = [member for member in h.facts.roster if member.person_id != assignee]
    with pytest.raises(PortUnavailable):
        h.provided.roles_for_session("hlp_broadcast")
