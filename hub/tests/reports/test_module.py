"""E09 registration against the real registry (SYSTEM 5.12): declarations, settings, late binding and the
fail-closed state while the private store, vault or reader is not composed."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from .real_station import OWNER, build, parse_in_process

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import PrivateKeyVault, PrivateRecordCoordinator, PrivateStore
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.tasks import TaskQueue, TaskRegistration, TaskRunner
from crew_health.kernel.testing import MemoryHealth
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.reports.adapters.store import PrivateReader
from crew_health.reports.application.ports import RecordOrigin
from crew_health.reports.module import Registry, register, setting_specs

pytestmark = [pytest.mark.trace("SYSTEM.5.12", "R25")]


def declared() -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=100, max_hooks=1000)
    register(cast(Registry, registry))
    return registry


def test_registration_declares_what_the_module_owns() -> None:
    registry = declared()
    d = registry.declarations
    assert {s.key for s in d["settings"].values() if isinstance(s, SettingSpec)} == {
        s.key for s in setting_specs()
    }
    assert all(s.basis.startswith("team assumption") and s.maximum is not None for s in setting_specs())
    assert "reports.confirmed" in d["audit_policies"] and isinstance(
        d["audit_policies"]["reports.confirmed"], AuditPolicy
    )
    assert "report" in d["private_writers"]
    (task,) = d["tasks"].values()
    assert (
        isinstance(task, TaskRegistration) and task.name == "report_extraction" and task.public_kind is None
    )
    assert "reports.owner" in d["audiences"] and len(d["routers"]) == 1
    assert {"rpt", "rvr", "drf"} == set(d["prefixes"])


def test_the_router_serves_exactly_the_three_contracted_operations() -> None:
    (router,) = [r for r in declared().declarations["routers"].values() if isinstance(r, APIRouter)]
    routes = {(next(iter(r.methods)), r.path, r.operation_id) for r in router.routes}  # type: ignore[attr-defined]
    assert routes == {
        ("POST", "/v1/reports", "uploadReport"),
        ("GET", "/v1/reports/{report_id}", "getReport"),
        ("POST", "/v1/reports/{report_id}/confirmations", "confirmExtraction"),
    }


def test_without_the_private_store_every_report_operation_is_unavailable_and_nothing_is_stored(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    registry = declared()
    for port, value in (
        (HubSettings, rig.settings),
        (IdFactory, rig.db.ids),
        (ReadSnapshotFactory, rig.db),
        (UnitOfWorkFactory, rig.db),
        (ConfigReleaseReader, rig.releases),
        (Clock, rig.clock),
    ):
        registry.provide(cast(type, port), lambda _, value=value: value)  # type: ignore[misc]
    registry.provide(PrivateKeyVault, lambda _: rig.vault)  # type: ignore[type-abstract]
    registry.resolve()  # binding leaves the api unbound: no coordinator, store or reader
    (router,) = [r for r in registry.declarations["routers"].values() if isinstance(r, APIRouter)]
    app = FastAPI()
    from real_station import person
    from starlette.requests import Request

    @app.middleware("http")
    async def session(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.session = person(OWNER)
        return await call_next(request)

    from fastapi.responses import JSONResponse

    from crew_health.app.errors import to_envelope
    from crew_health.kernel.errors import ProblemError

    @app.exception_handler(ProblemError)
    async def problem(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(to_envelope(error, "corr_test").model_dump(mode="json"), status_code=error.status)

    app.include_router(router)
    client = TestClient(app)
    got = client.get("/v1/reports/rpt_x")
    up = client.post(
        "/v1/reports",
        headers={"Idempotency-Key": "k1"},
        files={"file": ("a.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert got.json()["code"] == up.json()["code"] == "CAPABILITY_UNAVAILABLE"
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_report", (), limit=1) == ((0,),)


def test_with_every_provider_the_late_binding_composes_the_workflows(tmp_path: Path) -> None:
    rig = build(tmp_path)
    registry = declared()
    provided: dict[type, object] = {
        HubSettings: rig.settings,
        IdFactory: rig.db.ids,
        ReadSnapshotFactory: rig.db,
        UnitOfWorkFactory: rig.db,
        ConfigReleaseReader: rig.releases,
        Clock: rig.clock,
        UnitRegistry: rig.workflows.p.units,
        IdempotencyStore: rig.workflows.p.idempotency,
        CommandReceipts: rig.workflows.p.receipts,
        PrivateRecordCoordinator: rig.coordinator,
        PrivateStore: rig.store_faults,
        PrivateKeyVault: rig.vault,
        PrivateReader: rig.store.s.reader,
        RecordOrigin: _Routing(),
        TaskRunner: rig.tasks,
        TaskQueue: rig.tasks,
        HealthState: MemoryHealth(maximum=8),
    }
    for port, value in provided.items():
        registry.provide(port, lambda _, value=value: value)  # type: ignore[misc]
    registry.resolve()
    (router,) = [r for r in registry.declarations["routers"].values() if isinstance(r, APIRouter)]
    app = FastAPI()
    from real_station import person
    from starlette.requests import Request

    @app.middleware("http")
    async def session(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.session = person(OWNER)
        return await call_next(request)

    app.include_router(router)
    from . import builders

    created = TestClient(app).post(
        "/v1/reports",
        headers={"Idempotency-Key": "k1"},
        files={"file": ("a.pdf", builders.pdf([["Pulse at rest 72 bpm"]]), "application/pdf")},
    )
    assert created.status_code == 201 and created.json()["patient_id"] == OWNER
    assert parse_in_process  # the composed runner uses the contained parser; covered by test_parsing


class _Routing:
    def record_origin(self, person_id: str) -> str:
        return "SIM"

    def record_for(self, person_id: str, origin: str) -> str:
        return person_id
