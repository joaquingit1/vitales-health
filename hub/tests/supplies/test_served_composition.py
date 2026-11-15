"""E32 inside the served hub: create_app with E01's middleware, ledger, hash chained audit and SQLite, not the
module harnesses. The harnesses install audit policies themselves, so only a served call proves that the modules
declare every audit type they append (SYSTEM 5.8: an undeclared type is refused with INVALID_VALUE) and that a served
hub binds E32's E02 seams (sessions and consent) under E32's own port classes (app/privacy.py)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from crew_health.app.privacy import ConsentAdapter, SessionGate
from crew_health.app.registry import ModuleRegistry
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.medicines import module as medicines_module
from crew_health.medicines.application.ports import ConsentQueries
from crew_health.medicines.application.use_cases import audit_policies as medicines_policies
from crew_health.supplies.api.router import RequestAuthenticator
from crew_health.supplies.application.events import audit_policies as supplies_policies
from crew_health.supplies.application.ports import MissionFacts, MissionStatePort

pytestmark = pytest.mark.trace("E32-T-09", "SYSTEM.5.8", "SYSTEM.6")
TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    """hub/tests is not a package under the importlib import mode; E01's and E02's stations load from their files."""
    spec = importlib.util.spec_from_file_location("e32_served_" + name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


COMPOSITION = _load("composition", TESTS / "app/test_composition.py")


@pytest.fixture(scope="module")
def station_resources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    loaded = _load("conftest", TESTS / "app/conftest.py")
    resources: Path = loaded.copy_station_resources(tmp_path_factory.mktemp("e32") / "resources")
    return resources


@dataclass(frozen=True, slots=True)
class Mission:
    """E23 mission stand-in under supplies' copy: the station environment of E01's composition station."""

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return MissionFacts("msn_sim", "SIM", "station_leo", 180, "node_base")

    def day_end(self, instant: datetime) -> datetime:
        return instant.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _constant(value: object, resolver: object) -> object:
    return value


def _app(tmp_path: Path, resources: Path):  # type: ignore[no-untyped-def]
    plain = COMPOSITION.station(tmp_path, resources)
    sessions = plain.sessions

    @dataclass(frozen=True, slots=True)
    class Auth:
        def session(self, request: Request, operation_id: str) -> SessionContext:
            return sessions.authenticate(request)  # type: ignore[no-any-return]

    def configure(registry: ModuleRegistry) -> None:
        plain.configure(registry)
        registry.provide(MissionStatePort, partial(_constant, Mission()))  # type: ignore[type-abstract,arg-type]
        registry.provide(RequestAuthenticator, partial(_constant, Auth()))  # type: ignore[type-abstract,arg-type]

    hub = COMPOSITION.Station(plain.db, plain.sessions, configure, plain.resources)
    app = hub.app()
    runtime = app.state.runtime
    assert runtime.serving, runtime.fault_code
    COMPOSITION.correct_hub_clock(runtime)
    return hub, app


def test_every_e32_audit_type_is_declared_and_a_served_use_appends_it(
    tmp_path: Path, station_resources: Path
) -> None:
    hub, app = _app(tmp_path, station_resources)
    declared = set(app.state.runtime.database.audit_policies)
    assert {p.event_type for p in (*medicines_policies(), *supplies_policies())} <= declared
    with hub.db.snapshot() as view:
        before = view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1)[0][0]
    at = hub.db.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def use() -> tuple[int, str]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await client.post(
                "/v1/supplies/eye_rinse_water/uses",
                headers={"Authorization": "Bearer crew", "Idempotency-Key": "use-key-e32-served"},
                json={"quantity": 1, "client_time": at},
            )
            return response.status_code, response.text

    status, text = asyncio.run(use())
    assert status == 201, text
    with hub.db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT envelope FROM platform_audit WHERE seq > ? ORDER BY seq", (before,), limit=100
        )
    assert any(b"supplies.use_recorded" in bytes(row[0]) for row in rows)


def test_a_served_hub_binds_e32_sessions_and_consent_to_e02(tmp_path: Path) -> None:
    """app/station.py `production` with E02 installed: medicines and supplies authenticate through E02's session
    gate and medicines reads consent from E02's consent service, each under E32's own port class."""
    e02 = _load("e02_station", TESTS / "privacy/e02_station.py")
    running = e02.serve(e02.station(tmp_path))
    resolver = running.app.state.runtime.resolver
    assert isinstance(resolver.require(medicines_module.RequestAuthenticator), SessionGate)
    assert isinstance(resolver.require(RequestAuthenticator), SessionGate)
    assert isinstance(resolver.require(ConsentQueries), ConsentAdapter)
    person = sorted(member.person_id for member in e02.sim_members())[0]

    async def cover() -> tuple[int, int]:
        async with AsyncClient(
            transport=ASGITransport(app=running.app), base_url="http://station:8080"
        ) as client:
            signed = await client.post(
                "/v1/sessions",
                json={"kind": "crew", "credential": e02.credential(person), "person_id": person},
            )
            assert signed.status_code == 201, signed.text
            token = signed.json()["token"]
            signed_in = await client.get("/v1/supplies", headers={"Authorization": "Bearer " + token})
            anonymous = await client.get("/v1/supplies")
            return signed_in.status_code, anonymous.status_code

    signed_in, anonymous = asyncio.run(cover())
    assert anonymous == 401
    assert signed_in != 401
