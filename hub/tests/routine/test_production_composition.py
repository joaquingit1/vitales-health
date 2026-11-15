"""B2: routine is a production module. create_app discovers it through E01's generated static import list (the
module convention of SYSTEM 6), applies its migrations, resolves its providers and runs its scheduler jobs through
the hub's own supervisor; no routine test harness is involved here.

The hub is E01's composition station (hub/tests/app/test_composition.py): the real modules on this checkout plus
E01's stand-ins for epics that are not on main. Routine's own seams for epics not on main (E02 roster and
visibility, E23 mission and location, E24 warnings) are stood in here; records is reached through routine's own
composition adapter over records' real store (adapters/record_facts.py)."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Protocol, get_args

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from crew_health.app.assembly import BuildCatalog
from crew_health.app.installed_modules import REGISTRATIONS
from crew_health.app.registry import ModuleRegistry
from crew_health.clock.application.history import MissionClockService
from crew_health.kernel.met import MissionClock, MissionClockVersion
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform.storage import SQLiteDatabase
from crew_health.routine import module as routine_module
from crew_health.routine.adapters.hub_clock import SharedHubClock
from crew_health.routine.adapters.record_facts import SharedRecordFacts
from crew_health.routine.application import ports
from crew_health.routine.application.ports import RoutineQueries
from crew_health.routine.application.seams import SEAMS
from crew_health.routine.domain import vocabulary

pytestmark = pytest.mark.trace("R-E25-B2", "SYSTEM.6", "T-E25-015")
SOURCE = Path(__file__).resolve().parents[2] / "src/crew_health"
APP_TESTS = Path(__file__).resolve().parents[1] / "app"


def _e01(name: str) -> ModuleType:
    """E01's composition station lives in hub/tests/app, which is not a package under the importlib import mode;
    it is loaded from its file so this test drives exactly the hub E01's own composition test builds."""
    spec = importlib.util.spec_from_file_location("e25_composition_" + name, APP_TESTS / (name + ".py"))
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


COMPOSITION = _e01("test_composition")
station = COMPOSITION.station
correct_hub_clock = COMPOSITION.correct_hub_clock
reopen = COMPOSITION.reopen


@pytest.fixture(scope="module")
def station_resources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    resources: Path = _e01("conftest").copy_station_resources(tmp_path_factory.mktemp("e25") / "resources")
    return resources


@dataclass(frozen=True, slots=True)
class Crew:
    """E02 roster and E23 mission and location stand-ins under routine's copies, over E01's station crew."""

    def crew(self, at: datetime) -> Sequence[ports.RosterMember]:
        return self.members()

    def members(self) -> Sequence[ports.RosterMember]:
        return tuple(
            ports.RosterMember(m.person_id, m.display_name, m.roster_index, None) for m in COMPOSITION.CREW
        )

    def person_ids(self) -> frozenset[str]:
        return frozenset(m.person_id for m in COMPOSITION.CREW)

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> ports.MissionFacts | None:
        return ports.MissionFacts("msn_sim", "SIM", "lunar_surface", 180, "node_base")

    def location_of(self, person_id: str, at: datetime) -> str | None:
        return None


@dataclass(frozen=True, slots=True)
class Seams:
    """E02 Visibility (no care grant) and E24 WarningCommands stand-ins under routine's copies."""

    def can_read(self, session: SessionContext, object_ref: str) -> bool:
        return False

    def attach_routine_overrides(self, uow: UnitOfWork, warning_id: str, override_ids: Sequence[str]) -> None:
        return None


def _constant(value: object, resolver: object) -> object:
    return value


STAND_INS: dict[type[object], object] = {
    ports.RosterPort: Crew(),
    ports.MissionStatePort: Crew(),
    ports.LocationPort: Crew(),
    ports.Visibility: Seams(),
    ports.WarningCommands: Seams(),
}


def test_routine_is_installed_listed_in_the_build_manifest_and_verified() -> None:
    assert routine_module.register in REGISTRATIONS
    build = BuildCatalog.installed(SOURCE)
    assert "crew_health.routine.module" in build.inventory
    manifest = json.loads((SOURCE / "contracts_gen/manifest.json").read_bytes())
    assert manifest["modules"]["crew_health.routine.module"] == build.inventory["crew_health.routine.module"]
    build.verify(128)


def test_routine_ports_stay_domain_free_and_repeat_the_domain_vocabulary() -> None:
    tree = ast.parse((SOURCE / "routine/application/ports.py").read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not {name for name in imported if name.startswith("crew_health.routine")}
    assert get_args(ports.OccurrenceState) == get_args(vocabulary.OccurrenceState)
    assert get_args(ports.SkipReason) == get_args(vocabulary.SkipReason)
    assert get_args(ports.OriginMode) == get_args(vocabulary.OriginMode)


class StationLike(Protocol):
    db: SQLiteDatabase
    sessions: object
    configure: Callable[[ModuleRegistry], None]
    resources: object

    def app(self) -> FastAPI: ...


def _with_routine(hub: StationLike) -> StationLike:
    base = hub.configure

    def configure(registry: ModuleRegistry) -> None:
        base(registry)
        for port, value in STAND_INS.items():
            registry.provide(port, partial(_constant, value))

    with_routine: StationLike = COMPOSITION.Station(hub.db, hub.sessions, configure, hub.resources)
    return with_routine


def _started(
    tmp_path: Path, resources: Path, *, mission_clock: bool = True
) -> tuple[StationLike, FastAPI, StationLike]:
    """First start, then the operator sets the mission clock (MissionClockService.append, the clock module's own
    path; no epoch exists until someone sets it) and confirms the hub time, then a restart: routine's STATE_SWEEP
    startup hook materializes the crew's day."""
    plain: StationLike = station(tmp_path, resources)
    hub = _with_routine(plain)
    first = hub.app().state.runtime
    assert first.serving, first.fault_code
    service = first.resolver.require(MissionClock)
    assert isinstance(service, MissionClockService)
    epoch = hub.db.clock.now()
    if mission_clock:
        with hub.db.begin(actor=Actor("operator", "operator_sim"), correlation_id="e25_mission_clock") as uow:
            service.append(
                uow, MissionClockVersion("clock_sim", 1, epoch, epoch, None, "Day {day}"), ids=hub.db.ids
            )
    correct_hub_clock(first)
    first.shutdown()
    hub = reopen(hub)
    app = hub.app()
    assert app.state.runtime.serving, app.state.runtime.fault_code
    return hub, app, plain


def test_create_app_serves_routine_with_the_real_clock_records_and_scheduler(
    tmp_path: Path, station_resources: Path
) -> None:
    hub, app, _ = _started(tmp_path, station_resources)
    runtime = app.state.runtime
    routine = runtime.resolver.require(RoutineQueries)
    assert isinstance(routine, routine_module.RoutineRuntime)
    deps = routine.engine.deps
    # Providers on main resolve under their real classes or a composition adapter, never a routine copy.
    assert deps.clock_trust is not None and isinstance(deps.hub_clock, SharedHubClock)
    assert isinstance(deps.records, SharedRecordFacts)
    assert getattr(deps.releases, "history", None) is not None
    assert routine.missing == ()
    with hub.db.snapshot() as view:
        materialized = view.connection.rows(
            "SELECT count(*) FROM routine_occurrence WHERE person_id='person_sim'", (), limit=1
        )
    assert materialized != ((0,),)

    async def today() -> dict[str, object]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await client.get("/v1/routine/today", headers={"Authorization": "Bearer crew"})
            assert response.status_code == 200, response.text
            unauthenticated = await client.get("/v1/routine/today")
            assert unauthenticated.status_code == 401, unauthenticated.text
            health = await client.get("/v1/health")
            assert health.json()["status"] == "ok", health.text
            return dict(response.json())

    assert asyncio.run(today())["person_id"] == "person_sim"


def test_misses_count_through_the_supervisor_with_every_optional_provider_absent(
    tmp_path: Path, station_resources: Path
) -> None:
    """AGENTS 1 rule 3: the same store, restarted with none of routine's optional providers (roster, mission,
    location, visibility, records, warnings). The day boundary passes, the hub's own supervisor dispatches the
    stored deadlines and every all-day miss counts; each absent seam is a degraded health component."""
    hub, started, plain = _started(tmp_path, station_resources)
    with hub.db.snapshot() as view:
        due = view.connection.rows(
            "SELECT occurrence_id, due_until FROM routine_occurrence WHERE person_id='person_sim' "
            "AND state IN ('due','not_due','later') AND due_until IS NOT NULL ORDER BY due_until",
            (),
            limit=1000,
        )
    assert due
    boundary = datetime.fromisoformat(str(due[0][1]).replace("Z", "+00:00"))
    started.state.runtime.shutdown()

    hub = reopen(hub)
    app = COMPOSITION.Station(hub.db, hub.sessions, plain.configure, hub.resources).app()
    runtime = app.state.runtime
    assert runtime.serving, runtime.fault_code
    routine = runtime.resolver.require(RoutineQueries)
    assert isinstance(routine, routine_module.RoutineRuntime)
    assert set(routine.missing) == {"roster", "mission", "location", "visibility", "warnings"}
    assert set(routine.missing) <= set(SEAMS)
    hub.db.clock.at = boundary.astimezone(UTC)  # MemoryClock, the station clock
    assert runtime.supervisor is not None
    for _ in range(5):
        runtime.supervisor.run_once()
    ids = tuple(str(row[0]) for row in due if row[1] == due[0][1])
    with hub.db.snapshot() as view:
        ended = view.connection.rows(
            "SELECT state, miss_counted, end_reason FROM routine_occurrence WHERE occurrence_id IN ("
            + ",".join("?" * len(ids))
            + ")",
            ids,
            limit=1000,
        )
    assert set(ended) == {("missed", 1, "deadline")}
    # Missing capabilities are not faults (SYSTEM 5.10): health stays ok, and each use refuses with its reason.
    snap = runtime.resolver.require(HealthState).snapshot()
    assert snap.status == "ok", [(c.component, c.code) for c in snap.components if c.code]
    assert _get(app, "/v1/routine/today") == (422, "CAPABILITY_UNAVAILABLE")


def test_before_the_mission_clock_is_set_routine_refuses_with_context_inapplicable_and_health_stays_ok(
    tmp_path: Path, station_resources: Path
) -> None:
    hub, app, _ = _started(tmp_path, station_resources, mission_clock=False)
    routine = app.state.runtime.resolver.require(RoutineQueries)
    assert isinstance(routine, routine_module.RoutineRuntime)
    with hub.db.snapshot() as view:
        materialized = view.connection.rows("SELECT count(*) FROM routine_occurrence", (), limit=1)
    assert materialized == ((0,),)
    assert _get(app, "/v1/routine/today") == (422, "CONTEXT_INAPPLICABLE")
    assert _get(app, "/v1/health") == (200, "ok")


def _get(app: FastAPI, path: str) -> tuple[int, str]:
    """The status and the envelope code (or the health status) of one authenticated crew GET."""

    async def call() -> tuple[int, str]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await client.get(path, headers={"Authorization": "Bearer crew"})
            body = dict(response.json())
            return response.status_code, str(body.get("code", body.get("status")))

    return asyncio.run(call())
