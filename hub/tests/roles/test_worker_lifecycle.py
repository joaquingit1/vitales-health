"""The recompute worker reports to the hub's WorkerLifecycle and stops on shutdown (SYSTEM 5.12); the broadcast read
of help's BroadcastRolesSource runs on a read snapshot, never a write transaction (SYSTEM 5.7)."""

import threading
from pathlib import Path

import pytest
from fastapi import Request

from crew_health.app.registry import DeclarationRegistry
from crew_health.app.workers import ModuleWorkers
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import WorkerLifecycle
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWorkFactory
from crew_health.roles.api.router import RequestAuthenticator
from crew_health.roles.application.ports import (
    DayLabelPort,
    HelpSessionReader,
    MissionStatePort,
    OverlayPort,
    RosterPort,
)
from crew_health.roles.application.settings import RolesSettings
from crew_health.roles.module import WORKER_NAME, register

from .harness import Harness, authenticate, help_session

SHUTDOWN_MS = 5000


class Authenticator:
    def session(self, request: Request, operation_id: str) -> SessionContext:
        return authenticate(request, operation_id)


def _bound(h: Harness, workers: ModuleWorkers) -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    providers: tuple[tuple[type[object], object], ...] = (
        (UnitOfWorkFactory, h.deps.uow),
        (ReadSnapshotFactory, h.deps.snapshots),
        (WorkerLifecycle, workers),
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
    return registry


@pytest.mark.trace("R54", "SYSTEM-5.12")
def test_recompute_worker_registers_and_stops_on_hub_shutdown(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    workers = ModuleWorkers(maximum=8, monotonic_ns=h.clock.monotonic_ns)
    before = set(threading.enumerate())
    _bound(h, workers).resolve()
    started = [t for t in threading.enumerate() if t not in before and t.name == "roles-recompute"]
    assert workers.names() == (WORKER_NAME,)
    assert len(started) == 1 and started[0].is_alive()
    assert workers.shutdown(SHUTDOWN_MS) == ()
    assert not started[0].is_alive()


@pytest.mark.trace("R54", "SYSTEM-5.12")
def test_a_refused_worker_report_starts_no_thread(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    workers = ModuleWorkers(maximum=8, monotonic_ns=h.clock.monotonic_ns)
    assert workers.shutdown(SHUTDOWN_MS) == ()  # a closed table refuses every later start
    before = set(threading.enumerate())
    with pytest.raises(ProblemError) as refused:
        _bound(h, workers).resolve()
    assert refused.value.code is ErrorCode.INVALID_TRANSITION
    assert not [t for t in threading.enumerate() if t not in before and t.name == "roles-recompute"]


@pytest.mark.trace("R54", "V71", "SYSTEM-5.7")
def test_broadcast_roles_read_opens_no_write_transaction(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.open_help(help_session("hlp_read", subject="mara", flow="not_breathing"))
    writes = h.uow.write_transactions
    assert h.provided.roles_for_session("hlp_read")
    assert h.uow.write_transactions == writes
