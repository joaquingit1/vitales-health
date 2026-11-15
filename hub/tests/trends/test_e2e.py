# mypy: disable-error-code="type-abstract"
"""HTTP-captured observations feed resolved trends and durable internal forecasts."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import TaskInput, TaskQueue, TaskRegistration, TaskStatus
from crew_health.kernel.uow import (
    Actor,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.platform.adapters.tasks import TaskRepository
from crew_health.platform.application.tasks import TaskService
from crew_health.records.api.router import create_router
from crew_health.records.domain.observations import ObservationInput, ObservationRecord
from crew_health.trends.adapters.repository import SqliteForecastRepository
from crew_health.trends.application.forecast import TrendsForecastCommands
from crew_health.trends.application.ports import (
    BaselineSummary,
    EpisodeExclusionReader,
    ForecastCommandsPort,
    ForecastQueriesPort,
    ForecastTicket,
    ForecastView,
    ForecastWorkerPort,
    IndicatorSummary,
    IndicatorSummaryPort,
    InsufficientBaseline,
    ObservationReader,
    OriginMode,
    SeriesKey,
    SummaryRequest,
)
from crew_health.trends.module import register
from crew_health.trends.settings import TrendsSettings

from .support import NOW, ROOT, EpisodeReader, Station, catalog_value, mission_clock, person_session, station
from .test_forecast import Worker


class Hub:
    def __init__(self, path: Path) -> None:
        self.station = station(path)
        self.worker = Worker()
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=128)
        register(self.registry)
        self.tasks = task_service(self.station, self.registry)
        self.registry.provide(UnitOfWorkFactory, lambda _: self.station.db)
        self.registry.provide(ReadSnapshotFactory, lambda _: self.station.db)
        self.registry.provide(HubSettings, lambda _: self.station.db.settings)
        self.registry.provide(IdFactory, lambda _: self.station.db.ids)
        self.registry.provide(MissionClock, lambda _: mission_clock())
        self.registry.provide(ObservationReader, lambda _: self.station.reader)
        self.registry.provide(EpisodeExclusionReader, lambda _: EpisodeReader())
        self.registry.provide(ForecastWorkerPort, lambda _: self.worker)
        self.registry.provide(TaskQueue, lambda _: self.tasks)
        self.resolver = self.registry.resolve()
        self.summary = self.resolver.require(IndicatorSummaryPort)
        self.commands = self.resolver.require(ForecastCommandsPort)
        self.forecasts = self.resolver.require(ForecastQueriesPort)
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

    def record(self, request: Request, session: SessionContext, value: ObservationInput) -> ObservationRecord:
        with self.station.db.begin(actor=session.actor, correlation_id="http_capture") as uow:
            return self.station.commands.record_observation(uow, session, value)

    def post(self, day: int, value: int, *, person: str = "person", origin: OriginMode = "SIM") -> str:
        at = NOW - timedelta(days=14 - day)
        response = self.client.post(
            "/v1/observations",
            headers={"X-Person": person},
            json={
                "patient_id": person,
                "code": "pulse",
                "value": value,
                "unit": "/min",
                "observed_at": at.isoformat().replace("+00:00", "Z"),
                "origin_mode": origin,
                "acquisition_method": "self_report",
            },
        )
        assert response.status_code == 201, response.text
        body: object = response.json()
        assert isinstance(body, dict) and isinstance(body["observation_id"], str)
        assert body["quality_status"] == "accepted"
        return body["observation_id"]

    def read(self, *, person: str = "person", origin: OriginMode = "SIM") -> IndicatorSummary:
        with self.station.db.snapshot() as snapshot:
            (result,) = self.summary.summaries(
                snapshot, person_session(person), SummaryRequest(person, "mission", origin, ("pulse",))
            )
        assert isinstance(result, IndicatorSummary)
        return result


def authenticate(request: Request) -> SessionContext:
    person = request.headers.get("X-Person", "person")
    if person not in ("person", "another"):
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return person_session(person)


def task_service(station: Station, registry: DeclarationRegistry) -> TaskService:
    registration = registry.declarations["tasks"]["trends.forecast"]
    assert isinstance(registration, TaskRegistration)
    return TaskService(
        settings=station.db.settings,
        repository=TaskRepository(),
        ids=station.db.ids,
        registrations=(registration,),
        authorize=lambda view, session, task: session.actor == task.actor,
        authorize_completion=lambda uow, task: task.actor.kind == "person",
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=lambda view, first, second: second.original_at - first.original_at,
    )


@pytest.mark.trace("E14-01", "V09", "V47", "V14", "D09")
def test_http_records_resolved_trends_and_forecast_preserve_person_origin_and_time(tmp_path: Path) -> None:
    hub = Hub(tmp_path)
    db = hub.station.db
    ids = tuple(
        hub.post(day, value) for day, value in ((6, 10), (8, 20), (10, 30), (12, 40), (13, 50), (14, 45))
    )
    sim = hub.read()
    assert isinstance(sim.usual, BaselineSummary) and sim.usual.value.value == 30
    assert sim.usual.included_ids == ids[:-1]
    assert sim.today is not None and sim.today.observation_id == ids[-1]
    assert isinstance(sim.trend, tuple)
    assert tuple(p.mission_day for p in sim.trend) == (8, 9, 10, 11, 12, 13, 14)
    assert tuple(p.mission_day for p in sim.trend if p.value is None) == (9, 11)
    hub.post(13, 90, person="another")
    other_id = hub.post(14, 95, person="another")
    other = hub.read(person="another")
    assert isinstance(other.usual, InsufficientBaseline) and other.usual.sample_count == 1
    assert other.today is not None and other.today.observation_id == other_id
    assert hub.read().usual == sim.usual
    live_id = hub.post(14, 200, origin="LIVE")
    live = hub.read(origin="LIVE")
    assert isinstance(live.usual, InsufficientBaseline) and live.usual.sample_count == 0
    assert live.today is not None and live.today.observation_id == live_id
    assert hub.read().usual == sim.usual
    # A historical observation entered now keeps its original mission day.
    old_live = hub.post(12, 180, person="another", origin="LIVE")
    stale = hub.read(person="another", origin="LIVE")
    assert stale.state == "no_reading" and stale.today is None
    assert (
        stale.latest is not None
        and stale.latest.observation_id == old_live
        and stale.latest.mission_day == 12
    )
    key = SeriesKey("person", "mission", "SIM", "pulse")
    with db.begin(actor=person_session().actor, correlation_id="forecast") as uow:
        ticket = hub.commands.refresh(uow, person_session(), key)
    assert isinstance(ticket, ForecastTicket)
    with db.begin(actor=Actor("engine", "forecast_runner"), correlation_id="claim") as uow:
        (lease,) = hub.tasks.claim(uow, worker_id="worker", handlers=frozenset({"trends.forecast"}), limit=1)
    registration = hub.registry.declarations["tasks"]["trends.forecast"]
    assert isinstance(registration, TaskRegistration)
    result = registration.handler.execute(lease)
    with db.begin(actor=Actor("engine", "forecast_runner"), correlation_id="complete") as uow:
        assert hub.tasks.complete(uow, lease, result)
    registration.handler.discard(result)
    with db.snapshot() as snapshot:
        forecast = hub.forecasts.latest(snapshot, person_session(), key)
        assert isinstance(forecast, ForecastView) and forecast.input_ref == ticket.input_ref
        assert forecast.input_days == (12, 13, 14) and forecast.input_observation_ids == ids[-3:]
        assert forecast.experimental and forecast.not_a_measurement
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_input", (), limit=1) == ((1,),)
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_result", (), limit=1) == ((1,),)
        assert snapshot.connection.rows(
            "SELECT COUNT(*) FROM platform_task WHERE job_id IS NOT NULL", (), limit=1
        ) == ((0,),)
    assert hub.read().usual == sim.usual


@pytest.mark.trace("E14-01", "V75", "AGENTS-1.1")
def test_example_indicator_catalog_validates_against_its_json_schema() -> None:
    schemas = ROOT / "contracts/api/schemas"
    registry = Registry().with_resources(
        (path.name, Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))))
        for path in sorted(schemas.glob("*.json"))
    )
    schema = json.loads((schemas / "config-indicator-catalog.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, registry=registry).validate(catalog_value())


@pytest.mark.trace("E14-03", "AGENTS-1.6")
def test_failed_task_submission_rolls_back_forecast_input_in_real_sqlite(tmp_path: Path) -> None:
    hub = Hub(tmp_path)
    hub.post(14, 40)

    class FailedQueue:
        def submit(self, uow: UnitOfWork, task: TaskInput) -> TaskStatus:
            raise OSError("injected queue storage failure")

        def cancel(self, uow: UnitOfWork, session: SessionContext, task_id: str) -> TaskStatus:
            raise AssertionError("not used")

    commands = TrendsForecastCommands(
        hub.station.reader,
        hub.worker,
        FailedQueue(),
        mission_clock(),
        hub.station.db.ids,
        TrendsSettings.read(hub.station.db.settings),
        repository=SqliteForecastRepository(),
        exclusions=EpisodeReader(),
    )
    with pytest.raises(OSError, match="queue storage failure"):
        with hub.station.db.begin(actor=person_session().actor, correlation_id="forecast") as uow:
            commands.refresh(uow, person_session(), SeriesKey("person", "mission", "SIM", "pulse"))
    with hub.station.db.snapshot() as snapshot:
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_input", (), limit=1) == ((0,),)
