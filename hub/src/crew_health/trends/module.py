# mypy: disable-error-code="type-abstract"
"""Register bounded trends capabilities with late binding and no import side effects."""

from datetime import datetime
from pathlib import Path

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import TaskLease, TaskQueue, TaskRegistration, TaskResult
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.trends.adapters.catalog_check import IndicatorCatalogCheck
from crew_health.trends.adapters.repository import SqliteForecastRepository
from crew_health.trends.adapters.worker_client import HttpForecastWorker, WorkerAddressError
from crew_health.trends.application import ports
from crew_health.trends.application.environment import TrendsEnvironment
from crew_health.trends.application.forecast import (
    ForecastTaskHandler,
    TrendsForecastCommands,
    TrendsForecastQueries,
)
from crew_health.trends.application.summary import TrendsSummary
from crew_health.trends.settings import TrendsSettings, specs


class _Bound[P]:
    def __init__(self) -> None:
        self.target: P | None = None

    def service(self) -> P:
        if self.target is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)
        return self.target


class _Summary(_Bound[ports.IndicatorSummaryPort]):
    def summaries(
        self, snapshot: ReadSnapshot, session: SessionContext, request: ports.SummaryRequest
    ) -> tuple[ports.IndicatorSummary | ports.IndicatorUnavailable, ...]:
        return self.service().summaries(snapshot, session, request)


class _Environment(_Bound[ports.EnvironmentContextPort]):
    def series(
        self, snapshot: ReadSnapshot, session: SessionContext, channel_id: str, *, until: datetime
    ) -> ports.EnvironmentSeries | ports.Unavailable:
        return self.service().series(snapshot, session, channel_id, until=until)


class _Commands(_Bound[ports.ForecastCommandsPort]):
    def refresh(
        self, uow: UnitOfWork, session: SessionContext, key: ports.SeriesKey
    ) -> ports.ForecastTicket | ports.Unavailable:
        return self.service().refresh(uow, session, key)


class _Queries(_Bound[ports.ForecastQueriesPort]):
    def latest(
        self, snapshot: ReadSnapshot, session: SessionContext, key: ports.SeriesKey
    ) -> ports.ForecastView | ports.Unavailable:
        return self.service().latest(snapshot, session, key)


class _Task(_Bound[ForecastTaskHandler]):
    def execute(self, lease: TaskLease) -> TaskResult:
        return self.service().execute(lease)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        self.service().apply(uow, lease, result)

    def discard(self, result: TaskResult) -> None:
        self.service().discard(result)


TOKEN_MAX_BYTES = 4096  # a bearer token file is a few dozen bytes; anything larger is not a token


def _token(path: str) -> str | None:
    try:
        with Path(path).open("rb") as file:
            raw = file.read(TOKEN_MAX_BYTES + 1)
        token = raw.decode("utf-8").strip()
    except OSError, UnicodeDecodeError:
        return None
    return token if token and len(raw) <= TOKEN_MAX_BYTES else None


def _worker(resolver: Resolver, settings: TrendsSettings) -> tuple[ports.ForecastWorkerPort | None, str]:
    worker = resolver.optional(ports.ForecastWorkerPort)
    if worker is not None or not settings.worker_enabled:
        return worker, "worker_not_configured"
    # An unreadable, empty or oversized token disables only the optional worker; the hub and baselines keep running.
    token = _token(settings.worker_token_path)
    if token is None:
        return None, "worker_token_missing"
    try:
        return HttpForecastWorker(
            settings.worker_base_url, token, settings.worker_timeout_ms, settings.worker_response_max_bytes
        ), "worker_not_configured"
    except WorkerAddressError:
        return None, "worker_address_invalid"


def register(registry: ModuleRegistry) -> None:
    summary, environment, commands, queries, task = (
        _Summary(),
        _Environment(),
        _Commands(),
        _Queries(),
        _Task(),
    )
    for spec in specs():
        registry.setting(spec)
    registry.loader_check(IndicatorCatalogCheck())
    registry.id_prefix(record_type="forecast_input", prefix="fci")
    registry.task(TaskRegistration("trends.forecast", "1", task, None, None))
    registry.provide(ports.IndicatorSummaryPort, lambda _: summary)
    registry.provide(ports.EnvironmentContextPort, lambda _: environment)
    registry.provide(ports.ForecastCommandsPort, lambda _: commands)
    registry.provide(ports.ForecastQueriesPort, lambda _: queries)

    def bind(resolver: Resolver) -> None:
        snapshots = resolver.require(ReadSnapshotFactory)
        settings = TrendsSettings.read(resolver.require(HubSettings))
        ids = resolver.require(IdFactory)
        clock = resolver.require(MissionClock)
        reader = resolver.optional(ports.ObservationReader)
        worker, absent_reason = _worker(resolver, settings)
        repository = SqliteForecastRepository()
        exclusions = resolver.optional(ports.EpisodeExclusionReader)
        summary.target = TrendsSummary(reader, exclusions, clock, settings)
        environment.target = TrendsEnvironment(resolver.optional(ports.SensorSeriesReader), settings)
        commands.target = TrendsForecastCommands(
            reader,
            worker,
            resolver.optional(TaskQueue),
            clock,
            ids,
            settings,
            repository=repository,
            exclusions=exclusions,
            worker_absent_reason=absent_reason,
        )
        queries.target = TrendsForecastQueries(repository, clock)
        task.target = ForecastTaskHandler(
            snapshots, worker, settings, repository=repository, worker_absent_reason=absent_reason
        )

    registry.bind("trends", bind)
