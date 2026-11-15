# mypy: disable-error-code="type-abstract"
"""Reuse the HTTP Hub harness, with real clocks and no fake forecast services.

EpisodeReader and SensorReader are port fakes for the unbuilt E15 and E23 providers.
The records harness supplies its explicit synthetic quality and identity profile.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crew_health.app.registry import DeclarationRegistry  # noqa: E402
from crew_health.kernel.ids import IdFactory  # noqa: E402
from crew_health.kernel.met import MissionClock  # noqa: E402
from crew_health.kernel.settings import HubSettings  # noqa: E402
from crew_health.kernel.tasks import TaskLease, TaskQueue, TaskRegistration  # noqa: E402
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWorkFactory  # noqa: E402
from crew_health.records.api.router import create_router  # noqa: E402
from crew_health.trends.application.ports import (  # noqa: E402
    EnvironmentContextPort,
    EpisodeExclusionReader,
    ForecastCommandsPort,
    ForecastQueriesPort,
    ForecastTicket,
    ForecastView,
    IndicatorSummaryPort,
    ObservationReader,
    OriginMode,
    SensorSeriesReader,
    SeriesKey,
    Unavailable,
)
from crew_health.trends.module import register  # noqa: E402

from hub.tests.trends.support import (  # noqa: E402
    EPOCH,
    NOW,
    EpisodeReader,
    SensorReader,
    person_session,
    settings,
    station,
)
from hub.tests.trends.test_e2e import Hub, authenticate, task_service  # noqa: E402
from hub.tests.trends.test_mission_clock import real_clock  # noqa: E402
from hub.tests.trends.test_summary import edited_catalog  # noqa: E402

__all__ = ["NOW", "SensorReader", "edited_catalog", "person_session"]

KEY = SeriesKey("person", "mission", "SIM", "pulse")
ENGINE = Actor("engine", "forecast_runner")


class ScenarioHub(Hub):
    """Hub's records router and methods, with optional providers bound before resolution."""

    def __init__(
        self,
        path: Path,
        *,
        epoch: datetime = EPOCH,
        observations: bool = True,
        episodes: bool = True,
        queue: bool = True,
        sensor: SensorReader | None = None,
        overrides: dict[str, object] | None = None,
    ) -> None:
        self.station = station(path)
        clock_path = path / "mission_clock"
        clock_path.mkdir()
        self.clock = real_clock(clock_path, epoch)
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=128)
        register(self.registry)
        self.tasks = task_service(self.station, self.registry)
        self.registry.provide(UnitOfWorkFactory, lambda _: self.station.db)
        self.registry.provide(ReadSnapshotFactory, lambda _: self.station.db)
        configured = settings(**(overrides or {}))
        self.registry.provide(HubSettings, lambda _: configured)
        self.registry.provide(IdFactory, lambda _: self.station.db.ids)
        self.registry.provide(MissionClock, lambda _: self.clock)
        if observations:
            self.registry.provide(ObservationReader, lambda _: self.station.reader)
        if episodes:
            self.registry.provide(EpisodeExclusionReader, lambda _: EpisodeReader())
        if sensor is not None:
            self.registry.provide(SensorSeriesReader, lambda _: sensor)
        if queue:
            self.registry.provide(TaskQueue, lambda _: self.tasks)
        self.resolver = self.registry.resolve()
        self.summary = self.resolver.require(IndicatorSummaryPort)
        self.commands = self.resolver.require(ForecastCommandsPort)
        self.forecasts = self.resolver.require(ForecastQueriesPort)
        self.environment = self.resolver.require(EnvironmentContextPort)
        app = FastAPI()
        app.include_router(
            create_router(
                snapshots=self.station.db,
                queries=self.station.queries,
                authenticate=authenticate,
                record=self.record,
            )
        )
        self.client = TestClient(app)

    def capture(
        self,
        at: datetime,
        value: int,
        *,
        context: dict[str, str] | None = None,
        supersedes: str | None = None,
        origin: OriginMode = "SIM",
    ) -> str:
        body: dict[str, object] = {
            "patient_id": "person",
            "code": "pulse",
            "value": value,
            "unit": "/min",
            "observed_at": at.isoformat().replace("+00:00", "Z"),
            "origin_mode": origin,
            "acquisition_method": "self_report",
            "context": context or {},
        }
        if supersedes is not None:
            body["supersedes"] = supersedes
        response = self.client.post("/v1/observations", headers={"X-Person": "person"}, json=body)
        assert response.status_code == 201, response.text
        data: object = response.json()
        assert isinstance(data, dict)
        assert data["quality_status"] == "accepted"
        identity = data["observation_id"]
        assert isinstance(identity, str)
        return identity

    def refresh(self) -> ForecastTicket | Unavailable:
        with self.station.db.begin(actor=person_session().actor, correlation_id="refresh") as uow:
            return self.commands.refresh(uow, person_session(), KEY)

    def latest(self) -> ForecastView | Unavailable:
        with self.station.db.snapshot() as snapshot:
            return self.forecasts.latest(snapshot, person_session(), KEY)

    def claim(self) -> TaskLease:
        with self.station.db.begin(actor=ENGINE, correlation_id="claim") as uow:
            (lease,) = self.tasks.claim(
                uow,
                worker_id="scenario_worker",
                handlers=frozenset({"trends.forecast"}),
                limit=1,
            )
        return lease

    def finish(self) -> None:
        lease = self.claim()
        registration = self.registry.declarations["tasks"]["trends.forecast"]
        assert isinstance(registration, TaskRegistration)
        result = registration.handler.execute(lease)
        try:
            with self.station.db.begin(actor=ENGINE, correlation_id="complete") as uow:
                assert self.tasks.complete(uow, lease, result)
        finally:
            registration.handler.discard(result)


def history(hub: ScenarioHub) -> tuple[str, ...]:
    """Explicit synthetic pulse profile, captured through records HTTP."""
    return tuple(
        hub.post(day, value)
        for day, value in (
            (6, 10),
            (8, 20),
            (10, 30),
            (12, 40),
            (13, 50),
            (14, 45),
        )
    )


def worker_environment() -> dict[str, str]:
    """Isolate optional weights and credentials from the developer's environment."""
    return {
        key: value for key, value in os.environ.items() if not key.startswith("CREW_HEALTH_FORECAST_")
    } | {"UV_FROZEN": "1"}


def worker_command(module: str) -> list[str]:
    return ["uv", "run", "--project", "workers/forecast", "python", "-m", module]


def stop_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
