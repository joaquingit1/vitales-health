"""Experimental tasks reconnect, reject invalid output and suppress late generations."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.tasks import TaskLease, TaskRegistration, TaskResult
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryIds,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryTasks,
    MemoryUowFactory,
)
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.trends.application import ports
from crew_health.trends.application.forecast import (
    ForecastInputRecord,
    ForecastOutcomeRecord,
    ForecastTaskHandler,
    TrendsForecastCommands,
    TrendsForecastQueries,
)
from crew_health.trends.application.ports import (
    BaselineSummary,
    ExclusionPeriod,
    ForecastRequest,
    ForecastStep,
    ForecastTicket,
    ForecastView,
    SeriesKey,
    Unavailable,
    WorkerForecast,
    WorkerUnavailable,
)
from crew_health.trends.settings import TrendsSettings

from .support import catalog_release, mission_clock, person_session, settings
from .test_summary import NOW, Periods, Rows, baseline_rows, get_summary, observation, summary_service

KEY = SeriesKey("person", "mission", "SIM", "pulse")


ENGINE = Actor("engine", "forecast_runner")


class Repository:
    def __init__(self) -> None:
        self.inputs: dict[str, ForecastInputRecord] = {}
        self.outcomes: dict[str, ForecastOutcomeRecord] = {}

    def latest(self, view: ReadSnapshot | UnitOfWork, key: SeriesKey) -> ForecastInputRecord | None:
        rows = [r for r in self.inputs.values() if r.key == key]
        return max(rows, key=lambda row: row.generation) if rows else None

    def input(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastInputRecord | None:
        return self.inputs.get(input_ref)

    def insert(self, uow: UnitOfWork, row: ForecastInputRecord) -> None:
        assert row.input_ref not in self.inputs
        self.inputs[row.input_ref] = row

    def result(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastOutcomeRecord | None:
        return self.outcomes.get(input_ref)

    def save_result(self, uow: UnitOfWork, row: ForecastOutcomeRecord) -> None:
        assert row.input_ref not in self.outcomes
        self.outcomes[row.input_ref] = row


class Worker:
    def __init__(self, factory: MemoryUowFactory | None = None) -> None:
        self.factory = factory
        self.requests: list[ForecastRequest] = []
        self.response: WorkerForecast | WorkerUnavailable | None = None

    def forecast(self, request: ForecastRequest) -> WorkerForecast | WorkerUnavailable:
        if self.factory is not None:
            assert not self.factory.writing, "Worker execution must be outside write transactions"
        self.requests.append(request)
        if self.response is not None:
            return self.response
        last = request.series[-1]
        quantity = last.value
        return WorkerForecast(
            "model",
            "revision",
            "a" * 64,
            "policy.v1",
            Decimal("0.8"),
            (ForecastStep(last.mission_day + 1, quantity, quantity, quantity),),
        )


def memory_settings(*, staging: int, history: int = 1000) -> MemorySettings:
    values: dict[str, object] = {
        "tasks.max_pending": 20,
        "tasks.claim_batch": 10,
        "tasks.max_attempts": 3,
        "tasks.lease_ms": 1000,
        "tasks.max_input_bytes": 1048576,
        "tasks.max_result_bytes": 1048576,
    }
    original = settings(**{"trends.forecast_staging_max": staging, "trends.history_max_rows": history})
    specs = tuple(original.specs.values()) + tuple(
        SettingSpec(key, "integer", None, Decimal(1), Decimal(value), None, "Bounded task test profile")
        for key, value in values.items()
        if isinstance(value, int)
    )
    return MemorySettings(specs, original.values | values)


def capture(at: datetime) -> TimeInput:
    return TimeInput(at, "source", "segment", 1, "source_raw", "SIM")


class ForecastHarness:
    def __init__(self, *, staging: int = 8, history: int = 1000) -> None:
        self.settings = memory_settings(staging=staging, history=history)
        self.bounds = TrendsSettings.read(self.settings)
        self.ids = MemoryIds(identifiers=tuple(str(i) for i in range(1000)))
        self.clock = MemoryClock(NOW, 0, "process")
        self.factory = MemoryUowFactory(
            clock=self.clock,
            ids=self.ids,
            release=catalog_release(),
            settings=self.settings,
            connection=MemoryStorage(settings=self.settings, responses={}, results={}),
            node_id="node",
            incarnation="incarnation",
        )
        self.repository = Repository()
        self.reader = Rows(baseline_rows())
        self.periods = Periods()
        self.worker = Worker(self.factory)
        self.handler = ForecastTaskHandler(self.factory, self.worker, self.bounds, repository=self.repository)
        self.tasks = MemoryTasks(
            settings=self.settings,
            registrations=(TaskRegistration("trends.forecast", "1", self.handler, None, None),),
            authorize=lambda view, session, task: session.actor == task.actor,
            capture=capture,
            elapsed=lambda view, start, end: end.original_at - start.original_at,
        )
        self.commands = TrendsForecastCommands(
            self.reader,
            self.worker,
            self.tasks,
            mission_clock(),
            self.ids,
            self.bounds,
            repository=self.repository,
            exclusions=self.periods,
        )
        self.queries = TrendsForecastQueries(self.repository, mission_clock())
        self.session = person_session()

    def refresh(self) -> ForecastTicket:
        with self.factory.begin(actor=self.session.actor, correlation_id="refresh") as uow:
            ticket = self.commands.refresh(uow, self.session, KEY)
        assert isinstance(ticket, ForecastTicket)
        return ticket

    def latest(self) -> ForecastView | Unavailable:
        with self.factory.snapshot() as snapshot:
            return self.queries.latest(snapshot, self.session, KEY)

    def claim(self) -> TaskLease:
        with self.factory.begin(actor=ENGINE, correlation_id="claim") as uow:
            leases = self.tasks.claim(
                uow, worker_id="worker", handlers=frozenset({"trends.forecast"}), limit=1
            )
        assert len(leases) == 1
        return leases[0]

    def finish(self, lease: TaskLease, result: TaskResult | None = None) -> TaskResult:
        completed = result or self.handler.execute(lease)
        with self.factory.begin(actor=ENGINE, correlation_id="complete") as uow:
            assert self.tasks.complete(uow, lease, completed)
        self.handler.discard(completed)
        return completed


@pytest.mark.trace("E14-03", "R12", "V32", "D17")
def test_refresh_memory_task_execute_apply_and_query_preserve_experimental_provenance() -> None:
    h = ForecastHarness()
    assert h.latest() == Unavailable("trends", "no_forecast_requested")
    ticket = h.refresh()
    assert not ticket.reconnected and ticket.generation == 1
    assert h.latest() == Unavailable("trends", "forecast_pending")
    lease = h.claim()
    assert lease.task.public_kind is None and lease.task.model_release_id is None
    assert lease.task.actor == h.session.actor
    assert lease.task.audience.rule == "trends.owner"
    assert lease.task.command.operation == "trends.forecast.refresh"
    assert lease.task.release_id == catalog_release().release_id
    h.finish(lease)
    view = h.latest()
    assert isinstance(view, ForecastView)
    assert view.kind == "experimental_forecast" and view.experimental and view.not_a_measurement
    assert view.input_ref == ticket.input_ref
    assert view.input_days == (12, 13, 14)
    assert view.input_observation_ids == ("obs_12", "obs_13", "obs_14")
    assert view.model_sha256 == "a" * 64 and view.interval_level == Decimal("0.8")
    assert view.steps[0].mission_day == 15 and view.steps[0].median.unit == Unit("/min")


@pytest.mark.trace("E14-03", "V32", "D17")
def test_identical_refresh_reconnects_to_one_durable_input_and_task() -> None:
    h = ForecastHarness()
    first = h.refresh()
    second = h.refresh()
    assert second == replace(first, reconnected=True)
    assert len(h.repository.inputs) == len(h.factory.state.tasks) == 1
    h.finish(h.claim())
    assert h.refresh() == replace(first, reconnected=True)
    assert len(h.worker.requests) == 1


@pytest.mark.trace("E14-03", "V32", "V69")
def test_new_reading_supersedes_generation_and_late_old_result_publishes_nothing() -> None:
    h = ForecastHarness()
    old = h.refresh()
    lease = h.claim()
    staged = h.handler.execute(lease)
    h.reader.values += (observation(14, "48", identity="obs_new"),)
    new = h.refresh()
    assert new.generation == old.generation + 1 and new.input_ref != old.input_ref
    h.finish(lease, staged)
    assert not h.repository.outcomes
    assert h.latest() == Unavailable("trends", "forecast_pending")
    h.finish(h.claim())
    view = h.latest()
    assert isinstance(view, ForecastView) and view.input_ref == new.input_ref
    assert view.input_observation_ids[-1] == "obs_new"


def worker_result(
    *,
    day: int = 15,
    low: str = "10",
    median: str = "20",
    high: str = "30",
    unit: str = "/min",
    level: str = "0.8",
) -> WorkerForecast:
    return WorkerForecast(
        "model",
        "revision",
        "a" * 64,
        "policy",
        Decimal(level),
        (
            ForecastStep(
                day,
                Quantity(Decimal(low), Unit(unit)),
                Quantity(Decimal(median), Unit(unit)),
                Quantity(Decimal(high), Unit(unit)),
            ),
        ),
    )


@pytest.mark.trace("E14-03", "R12", "V32")
@pytest.mark.parametrize(
    "response",
    [
        worker_result(low="21"),
        worker_result(high="19"),
        worker_result(unit="h"),
        worker_result(day=14),
        worker_result(day=16),
        worker_result(level="0"),
        worker_result(level="1"),
        worker_result(level="NaN"),
        replace(worker_result(), steps=()),
        replace(worker_result(), steps=worker_result().steps * 21),
        replace(worker_result(), steps=worker_result().steps * 2),
    ],
)
def test_invalid_worker_output_is_stored_only_as_unavailable(response: WorkerForecast) -> None:
    h = ForecastHarness()
    h.worker.response = response
    h.refresh()
    h.finish(h.claim())
    assert h.latest() == Unavailable("forecast_worker", "worker_response_invalid")
    assert all(isinstance(row.outcome, WorkerUnavailable) for row in h.repository.outcomes.values())


@pytest.mark.trace("E14-03", "R12")
def test_nonfinite_measurements_cannot_enter_worker_forecast_dto() -> None:
    with pytest.raises(ProblemError) as caught:
        Quantity(Decimal("NaN"), Unit("/min"))
    assert caught.value.code is ErrorCode.INVALID_VALUE


@pytest.mark.trace("E14-03", "R12", "V69")
def test_worker_timeout_is_unavailable_while_deterministic_baseline_keeps_working() -> None:
    h = ForecastHarness()
    h.worker.response = WorkerUnavailable("worker_timeout")
    h.refresh()
    h.finish(h.claim())
    assert h.latest() == Unavailable("forecast_worker", "worker_timeout")
    summary = get_summary(summary_service(h.reader, Periods()))
    assert isinstance(summary.usual, BaselineSummary) and summary.usual.value.value == Decimal("30")


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize(
    "missing,expected",
    [
        ("worker", Unavailable("forecast_worker", "worker_not_configured")),
        ("tasks", Unavailable("task_runner", "task_runner_unbound")),
        ("reader", Unavailable("E01", "observation_reader_unbound")),
        ("exclusions", Unavailable("E15", "episode_periods_unavailable")),
        ("incomplete", Unavailable("E15", "episode_periods_incomplete")),
    ],
)
def test_missing_forecast_dependency_fails_closed(missing: str, expected: Unavailable) -> None:
    h = ForecastHarness()
    service = TrendsForecastCommands(
        None if missing == "reader" else h.reader,
        None if missing == "worker" else h.worker,
        None if missing == "tasks" else h.tasks,
        mission_clock(),
        h.ids,
        h.bounds,
        repository=h.repository,
        exclusions=None if missing == "exclusions" else Periods(missing != "incomplete"),
    )
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        assert service.refresh(uow, h.session, KEY) == expected
    assert not h.repository.inputs and not h.factory.state.tasks


@pytest.mark.trace("E14-03", "V32")
def test_no_qualified_history_is_unavailable_and_never_submits_task() -> None:
    h = ForecastHarness()
    h.reader.values = ()
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        assert h.commands.refresh(uow, h.session, KEY) == Unavailable("trends", "no_qualified_history")
    assert not h.factory.state.tasks


@pytest.mark.trace("E14-03", "V32")
def test_staging_has_a_hard_bound_and_discard_releases_capacity() -> None:
    h = ForecastHarness(staging=1)
    h.refresh()
    first = h.claim()
    staged = h.handler.execute(first)
    h.reader.values += (observation(14, "49", identity="obs_new"),)
    h.refresh()
    second = h.claim()
    with pytest.raises(ProblemError) as caught:
        h.handler.execute(second)
    assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert not h.repository.outcomes
    h.handler.discard(staged)
    h.finish(second)
    assert isinstance(h.latest(), ForecastView)


@pytest.mark.trace("E14-03", "V32")
def test_applied_result_frees_its_staging_slot_without_an_explicit_discard() -> None:
    """Regression: a published result must not keep its staging slot, or a bounded map refuses all later work."""
    h = ForecastHarness(staging=1)
    h.refresh()
    first = h.claim()
    first_result = h.handler.execute(first)
    with h.factory.begin(actor=ENGINE, correlation_id="complete") as uow:
        assert h.tasks.complete(uow, first, first_result)
    h.reader.values += (observation(14, "49", identity="obs_new"),)
    h.refresh()
    second = h.claim()
    second_result = h.handler.execute(second)
    with h.factory.begin(actor=ENGINE, correlation_id="complete") as uow:
        assert h.tasks.complete(uow, second, second_result)
    assert len(h.repository.outcomes) == 2


@pytest.mark.trace("E14-03", "V32")
def test_apply_is_idempotent_on_replay_and_discard_is_idempotent() -> None:
    h = ForecastHarness()
    h.refresh()
    lease = h.claim()
    staged = h.handler.execute(lease)
    with h.factory.begin(actor=ENGINE, correlation_id="apply") as uow:
        h.handler.apply(uow, lease, staged)
        h.handler.apply(uow, lease, staged)
    assert len(h.repository.outcomes) == 1
    h.handler.discard(staged)
    h.handler.discard(staged)


@pytest.mark.trace("E14-03", "V25")
@pytest.mark.parametrize(
    "session", [person_session("another"), person_session(kind="station"), person_session(kind="assistant")]
)
def test_forecast_commands_and_queries_refuse_non_owner(session: SessionContext) -> None:
    h = ForecastHarness()
    with h.factory.begin(actor=session.actor, correlation_id="denied") as uow:
        with pytest.raises(ProblemError) as caught:
            h.commands.refresh(uow, session, KEY)
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    with h.factory.snapshot() as snapshot:
        with pytest.raises(ProblemError) as caught:
            h.queries.latest(snapshot, session, KEY)
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assert not h.repository.inputs


@pytest.mark.trace("E14-03", "AGENTS-1.4")
def test_trends_has_no_safety_imports_or_public_mutation_path() -> None:
    assert ports.__file__ is not None
    root = Path(ports.__file__).parents[1]
    forbidden = (
        "crew_health.rules",
        "crew_health.help",
        "crew_health.pathways",
        "crew_health.episodes",
        "crew_health.assistant",
    )
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden), path
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden) for alias in node.names), path
    tree = ast.parse(Path(ports.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            assert not node.name.startswith(("raise", "clear", "lower", "resolve"))


@pytest.mark.trace("E14-03", "R12", "V32")
def test_forecast_uses_entire_contiguous_history_beyond_display_window() -> None:
    h = ForecastHarness()
    h.reader.values = tuple(observation(day, str(10 + day)) for day in range(1, 15))
    ticket = h.refresh()
    row = h.repository.inputs[ticket.input_ref]
    assert tuple(point.mission_day for point in row.series) == tuple(range(1, 15))
    assert row.observation_ids == tuple(f"obs_{day}" for day in range(1, 15))


def refused(h: ForecastHarness) -> ForecastTicket | Unavailable:
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        return h.commands.refresh(uow, h.session, KEY)


@pytest.mark.trace("E14-03", "R12", "V32", "V14")
def test_stale_history_never_forecasts_days_already_past() -> None:
    """m4 regression: history ends on day 3 and today is day 14, so every step would be a day already past."""
    h = ForecastHarness()
    h.reader.values = tuple(observation(day, str(10 + day)) for day in (1, 2, 3))
    assert refused(h) == Unavailable("trends", "history_stale")
    assert not h.repository.inputs and not h.factory.state.tasks


@pytest.mark.trace("E14-03", "R12", "V32")
def test_history_ending_yesterday_is_forecast_from_today() -> None:
    h = ForecastHarness()
    h.reader.values = tuple(observation(day, str(10 + day)) for day in range(8, 14))
    ticket = h.refresh()
    assert h.repository.inputs[ticket.input_ref].series[-1].mission_day == 13


@pytest.mark.trace("E14-03", "V14")
def test_a_stored_forecast_drops_past_steps_and_expires_when_none_is_left() -> None:
    """m4 regression for the read side: a forecast made earlier never shows a day already past as a forecast."""
    h = ForecastHarness()
    h.refresh()
    h.worker.response = None
    lease = h.claim()
    last = h.reader.values[-1].quantity
    h.worker.response = WorkerForecast(
        "model",
        "revision",
        "a" * 64,
        "policy.v1",
        Decimal("0.8"),
        tuple(ForecastStep(day, last, last, last) for day in (15, 16, 17)),
    )
    h.finish(lease)
    h.factory.clock = MemoryClock(NOW + timedelta(days=2), 0, "process")
    view = h.latest()
    assert isinstance(view, ForecastView) and tuple(s.mission_day for s in view.steps) == (16, 17)
    h.factory.clock = MemoryClock(NOW + timedelta(days=4), 0, "process")
    assert h.latest() == Unavailable("trends", "forecast_expired")


@pytest.mark.trace("E14-03", "R12", "V32")
def test_absent_catalog_or_uncatalogued_code_never_reaches_the_model() -> None:
    """M4 regression: only an eligible indicator of the pinned catalog is forecast (tool catalog get_forecast)."""
    h = ForecastHarness()
    h.factory.release = MemoryRelease("release_without_catalog", {})
    assert refused(h) == Unavailable("trends", "indicator_catalog_absent")
    h.factory.release = catalog_release()
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        result = h.commands.refresh(uow, h.session, replace(KEY, code="not_catalogued"))
    assert result == Unavailable("trends", "code_not_in_catalog")
    assert not h.repository.inputs and not h.factory.state.tasks


@pytest.mark.trace("E14-03", "R12", "V09", "V47")
def test_forecast_input_never_mixes_contexts_the_baseline_keeps_apart() -> None:
    """M4 regression: rest and exercise pulse alternate by day; only the newest reading's context reaches the model."""
    h = ForecastHarness()
    h.reader.values = tuple(
        replace(
            observation(day, "60" if day % 2 else "150"),
            context=(("activity", "rest" if day % 2 else "exercise"),),
        )
        for day in range(1, 15)
    )
    ticket = h.refresh()
    series = h.repository.inputs[ticket.input_ref].series
    assert {str(point.value.value) for point in series} == {"150"}
    assert len(series) == 1  # the other exercise days are not contiguous: no gap is filled


@pytest.mark.trace("E14-03", "R12", "V09")
def test_episode_linked_readings_and_episode_periods_never_reach_the_model() -> None:
    """M4 regression: readings the usual value excludes for an episode are excluded from the forecast input too."""
    h = ForecastHarness()
    h.reader.values = tuple(
        replace(observation(day, str(60 + day)), episode_id="episode_1" if day == 10 else None)
        for day in range(1, 15)
    )
    ticket = h.refresh()
    row = h.repository.inputs[ticket.input_ref]
    assert "obs_10" not in row.observation_ids
    assert tuple(p.mission_day for p in row.series) == (11, 12, 13, 14)
    period = h.reader.values[11].normalized.corrected_at
    assert period is not None
    h.periods.periods_value = (ExclusionPeriod("episode_2", period, period + timedelta(hours=1)),)
    ticket = h.refresh()
    assert tuple(p.mission_day for p in h.repository.inputs[ticket.input_ref].series) == (13, 14)
    h.reader.values = tuple(replace(row, episode_id="episode_3") for row in h.reader.values)
    assert refused(h) == Unavailable("trends", "no_qualified_history")


@pytest.mark.trace("E14-03", "V69", "D17")
def test_transient_worker_failure_is_retried_by_the_next_refresh() -> None:
    """M3 regression: a timeout is shown, but the next refresh asks the worker again instead of reconnecting."""
    h = ForecastHarness()
    h.worker.response = WorkerUnavailable("worker_timeout")
    first = h.refresh()
    h.finish(h.claim())
    assert h.latest() == Unavailable("forecast_worker", "worker_timeout")
    h.worker.response = None
    again = h.refresh()
    assert not again.reconnected and again.generation == 2 and again.task_id != first.task_id
    h.finish(h.claim())
    assert isinstance(h.latest(), ForecastView)
    assert len(h.worker.requests) == 2


@pytest.mark.trace("E14-03", "V32", "D17")
def test_answer_fixed_by_the_input_reconnects_without_new_work() -> None:
    h = ForecastHarness()
    h.worker.response = WorkerUnavailable("history_too_short")
    first = h.refresh()
    h.finish(h.claim())
    assert h.refresh() == replace(first, reconnected=True)
    assert len(h.worker.requests) == 1 and len(h.factory.state.tasks) == 1


@pytest.mark.trace("E14-03", "V69", "D17")
def test_failed_task_is_resubmitted_as_a_new_generation_not_reconnected() -> None:
    """M3 regression: a task that ended failed leaves no result; refresh starts new work instead of waiting forever."""
    h = ForecastHarness()
    first = h.refresh()
    assert h.refresh() == replace(first, reconnected=True)  # still queued: reconnect, no duplicate work
    lease = h.claim()
    with h.factory.begin(actor=ENGINE, correlation_id="fail") as uow:
        assert h.tasks.fail(uow, lease, code=ErrorCode.CAPABILITY_UNAVAILABLE, retryable=False)
    again = h.refresh()
    assert not again.reconnected and again.generation == 2 and again.task_id != first.task_id
    h.finish(h.claim())
    assert isinstance(h.latest(), ForecastView)


@pytest.mark.trace("E14-03", "R12", "V32")
def test_history_cap_returns_unavailable_when_contiguous_tail_might_extend_further() -> None:
    h = ForecastHarness(history=3)
    h.reader.values = tuple(observation(day, str(10 + day)) for day in (14, 13, 12))
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        result = h.commands.refresh(uow, h.session, KEY)
    assert result == Unavailable("trends", "history_limit_reached")
    assert not h.repository.inputs and not h.factory.state.tasks


@pytest.mark.trace("E14-03", "R12", "V32")
def test_gap_proves_tail_complete_even_when_reader_reaches_history_cap() -> None:
    h = ForecastHarness(history=3)
    h.reader.values = tuple(observation(day, str(10 + day)) for day in (14, 13, 11))
    ticket = h.refresh()
    row = h.repository.inputs[ticket.input_ref]
    assert tuple(point.mission_day for point in row.series) == (13, 14)
    assert row.observation_ids == ("obs_13", "obs_14")
