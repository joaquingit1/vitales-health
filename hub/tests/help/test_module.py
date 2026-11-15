"""module.py against E01's real DeclarationRegistry: settings, prefixes, audiences, provided ports, the router,
late binding with every optional provider absent, and the help worker started from the bind callback."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from help_support import HeaderGate, build, headers, json_of, open_body

from crew_health.app.registry import DeclarationRegistry
from crew_health.help import module
from crew_health.help.api.router import RoleGate
from crew_health.help.application import ports
from crew_health.help.application.settings import help_setting_defaults, help_setting_specs
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory


def _constant(value: object) -> Callable[[object], object]:
    return lambda _: value


@pytest.mark.trace("SYSTEM-4.2", "T-E26-001", "R15")
def test_registration_binds_with_only_kernel_ports_and_serves_help(tmp_path: Path) -> None:
    harness = build(tmp_path / "help.db")
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    module.register(registry)
    kernel: tuple[tuple[type[object], object], ...] = (
        (UnitOfWorkFactory, harness.store),
        (ReadSnapshotFactory, harness.store),
        (ConfigReader, harness.config),
        (HubSettings, MemorySettings(help_setting_specs(), help_setting_defaults())),
        (IdFactory, harness.ids),
        (Clock, harness.clock),
        (IdempotencyStore, harness.ledger),
        (CommandReceipts, harness.ledger),
        (RoleGate, HeaderGate()),
    )
    for port, value in kernel:
        registry.provide(port, _constant(value))
    declared = registry.declarations
    assert set(declared["settings"]) == {s.key for s in help_setting_specs()}
    assert set(declared["prefixes"]) == {"hlp", "brd", "gm", "hlt", "hla", "hlc"}
    assert set(declared["audiences"]) == {
        "help.session_reader",
        "help.broadcast_reader",
        "help.ground_message_reader",
    }
    for provided in (
        ports.HelpSessionReader,
        ports.HelpEntryPort,
        ports.HelpLogSnapshots,
        ports.GroundMessageLifecyclePort,
        ports.HelpAssistantSink,
        ports.HelpTurnGuardPort,
    ):
        assert provided in registry.providers
    resolver = registry.resolve()
    reader = resolver.require(ports.HelpSessionReader)  # type: ignore[type-abstract]
    app = FastAPI()
    for router in declared["routers"].values():
        assert isinstance(router, APIRouter)
        app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/v1/help-sessions", json=open_body(), headers=headers("tomas", "module-press-1"))
    assert response.status_code == 201, response.text
    sid = str(json_of(response)["help_session_id"])
    assert reader.snapshot(sid) is not None
    assert not declared.get("startup")  # a feature module cannot type E01's StartupPhase: no hook
    workers = [t for t in threading.enumerate() if t.name == "help-worker"]
    assert len(workers) == 1 and workers[0].daemon
    reader._runtime.service.worker.stop()  # type: ignore[attr-defined]  # the bound runtime behind the port
    workers[0].join(timeout=5)
    assert not workers[0].is_alive()


@pytest.mark.trace("SYSTEM-4.2", "D5")
def test_unbound_gate_answers_with_the_envelope_not_a_crash(tmp_path: Path) -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    module.register(registry)
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        assert isinstance(router, APIRouter)
        app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/help-sessions", json=open_body(), headers={"Idempotency-Key": "module-press-2"}
    )
    assert response.status_code == 401
    assert json_of(response)["code"] == "UNAUTHENTICATED"
