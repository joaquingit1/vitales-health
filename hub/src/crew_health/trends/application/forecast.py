"""Internal experimental forecast work, with durable inputs and generation guarded publication."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import Lock
from typing import Protocol

from crew_health.kernel.commands import canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.tasks import TaskInput, TaskLease, TaskQueue, TaskResult
from crew_health.kernel.units import Unit
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
)
from crew_health.trends.application import ports
from crew_health.trends.application.catalog import load_catalog
from crew_health.trends.application.summary import (
    authorize,
    current_day,
    history_incomplete,
    mission_day,
    read_history,
    validate_key,
)
from crew_health.trends.domain import baseline, forecast, model, trend
from crew_health.trends.settings import TrendsSettings


@dataclass(frozen=True, slots=True)
class ForecastInputRecord:
    input_ref: str
    key: ports.SeriesKey
    generation: int
    fingerprint: str
    unit: Unit
    series: tuple[ports.DailyValue, ...]
    observation_ids: tuple[str, ...]
    release_id: str
    task_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ForecastOutcomeRecord:
    input_ref: str
    task_id: str
    task_generation: int
    outcome: ports.WorkerForecast | ports.WorkerUnavailable
    created_at: datetime


class ForecastRepository(Protocol):
    def latest(self, view: ReadSnapshot | UnitOfWork, key: ports.SeriesKey) -> ForecastInputRecord | None: ...
    def input(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastInputRecord | None: ...
    def insert(self, uow: UnitOfWork, row: ForecastInputRecord) -> None: ...
    def result(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastOutcomeRecord | None: ...
    def save_result(self, uow: UnitOfWork, row: ForecastOutcomeRecord) -> None: ...


# Worker answers that are fixed by the input and the pinned policy: asking again with the same input cannot change
# them. Every other unavailable answer (timeout, unreachable, busy, weights absent, inference failure) is transient
# and a refresh of the same input starts a new generation instead of reconnecting to it.
FINAL_UNAVAILABLE = frozenset(
    {
        "malformed_value",
        "nonfinite_value",
        "unit_missing",
        "empty",
        "too_many_points",
        "days_not_increasing",
        "history_not_contiguous",
        "history_too_short",
        "policy_version_mismatch",
        "model_output_invalid",
        "worker_response_invalid",
        "worker_response_too_large",
    }
)


def key_bytes(key: ports.SeriesKey) -> bytes:
    return canonical_json(
        {
            "person_id": key.person_id,
            "mission_id": key.mission_id,
            "origin_mode": key.origin_mode,
            "code": key.code,
        }
    )


class TrendsForecastCommands:
    def __init__(
        self,
        reader: ports.ObservationReader | None,
        worker: ports.ForecastWorkerPort | None,
        tasks: TaskQueue | None,
        clock: MissionClock,
        ids: IdFactory,
        settings: TrendsSettings,
        *,
        repository: ForecastRepository,
        exclusions: ports.EpisodeExclusionReader | None,
        worker_absent_reason: str = "worker_not_configured",
    ) -> None:
        self.reader, self.worker, self.tasks, self.exclusions = reader, worker, tasks, exclusions
        self.clock, self.ids, self.settings, self.repository = clock, ids, settings, repository
        self.worker_absent_reason = worker_absent_reason

    def refresh(
        self, uow: UnitOfWork, session: SessionContext, key: ports.SeriesKey
    ) -> ports.ForecastTicket | ports.Unavailable:
        authorize(session, key.person_id)
        validate_key(key.person_id, key.mission_id, key.origin_mode, (key.code,))
        if self.worker is None:
            return ports.Unavailable("forecast_worker", self.worker_absent_reason)
        tasks = self.tasks
        if tasks is None:
            return ports.Unavailable("task_runner", "task_runner_unbound")
        prepared = self._history(uow, session, key)
        if isinstance(prepared, ports.Unavailable):
            return prepared
        series, observation_ids = prepared
        fingerprint = forecast.fingerprint(
            key_bytes(key).decode("utf-8"),
            tuple(model.DailyValue(point.mission_day, point.value) for point in series),
            observation_ids,
        )
        previous = self.repository.latest(uow, key)
        if (
            previous is not None
            and previous.fingerprint == fingerprint
            and self._live(uow, session, previous, tasks)
        ):
            if previous.task_id is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
            return ports.ForecastTicket(previous.input_ref, previous.generation, previous.task_id, True)
        input_ref, task_id = self.ids.new("fci"), self.ids.new("tsk")
        row = ForecastInputRecord(
            input_ref,
            key,
            1 if previous is None else previous.generation + 1,
            fingerprint,
            series[0].value.unit,
            series,
            observation_ids,
            uow.release.release_id,
            task_id,
            uow.now,
        )
        self.repository.insert(uow, row)
        task = self._task(session, row)
        status = tasks.submit(uow, task)
        if status.task_id != task_id:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        return ports.ForecastTicket(input_ref, row.generation, task_id, False)

    def _live(
        self, uow: UnitOfWork, session: SessionContext, previous: ForecastInputRecord, tasks: TaskQueue
    ) -> bool:
        """Reconnect only to work that can still answer: a forecast, a final answer or a task still queued or running."""
        outcome = self.repository.result(uow, previous.input_ref)
        if outcome is not None:
            return not (
                isinstance(outcome.outcome, ports.WorkerUnavailable)
                and outcome.outcome.reason not in FINAL_UNAVAILABLE
            )
        # Submitting the identical task is idempotent and returns its current status (SYSTEM 5.10).
        status = tasks.submit(uow, self._task(session, previous))
        return status.state not in ("failed", "cancelled")

    def _history(
        self, uow: UnitOfWork, session: SessionContext, key: ports.SeriesKey
    ) -> tuple[tuple[ports.DailyValue, ...], tuple[str, ...]] | ports.Unavailable:
        """Only an eligible, comparable, contiguous history ending today or yesterday reaches the model."""
        eligible = self._eligible(uow, session, key)
        if isinstance(eligible, ports.Unavailable):
            return eligible
        rows, selected, today = eligible
        days = tuple(sample.mission_day for sample in selected.samples if sample.mission_day is not None)
        span = today - min(days, default=today) + 1
        points = trend.daily_points(selected.samples, current_day=today, trend_days=max(span, 1))
        tail = trend.contiguous_tail(points)
        if not tail:
            return ports.Unavailable("trends", "no_qualified_history")
        if tail[-1].mission_day < today - 1:
            # The first step would be a day already past: a stale history never yields a forecast (PLAN 27).
            return ports.Unavailable("trends", "history_stale")
        first, _ = self.clock.day_bounds(
            tail[0].mission_day, version=self.clock.version_at(uow.now).clock_version
        )
        if history_incomplete(rows, self.settings.history_max_rows, first):
            return ports.Unavailable("trends", "history_limit_reached")
        in_tail = {point.mission_day for point in tail}
        observation_ids = tuple(
            p.observation_id for p in points if p.mission_day in in_tail and p.observation_id is not None
        )
        return tuple(ports.DailyValue(p.mission_day, p.value) for p in tail), observation_ids

    def _eligible(
        self, uow: UnitOfWork, session: SessionContext, key: ports.SeriesKey
    ) -> tuple[tuple[ports.ObservationRef, ...], model.Selection, int] | ports.Unavailable:
        if self.reader is None:
            return ports.Unavailable("E01", "observation_reader_unbound")
        catalog = load_catalog(uow.release)
        if isinstance(catalog, ports.Unavailable):
            return catalog
        if catalog.indicator(key.code) is None:
            return ports.Unavailable("trends", "code_not_in_catalog")
        today = current_day(key.origin_mode, self.clock, uow.now)
        if isinstance(today, ports.Unavailable):
            return today
        if self.exclusions is None:
            return ports.Unavailable("E15", "episode_periods_unavailable")
        periods = self.exclusions.periods(
            uow,
            session,
            key.person_id,
            mission_id=key.mission_id,
            origin_mode=key.origin_mode,
            until=uow.now,
            limit=self.settings.history_max_rows,
        )
        if not periods.complete:
            return ports.Unavailable("E15", "episode_periods_incomplete")
        request = ports.SummaryRequest(key.person_id, key.mission_id, key.origin_mode, (key.code,))
        rows, selected = read_history(
            uow, session, request, key.code, self.reader, self.clock, self.settings.history_max_rows
        )
        qualified = baseline.comparable(
            selected, tuple(model.Period(p.start, p.end) for p in periods.periods)
        )
        return rows, qualified, today

    @staticmethod
    def _task(session: SessionContext, row: ForecastInputRecord) -> TaskInput:
        """Rebuilt identically from the durable input row, so a resubmission is an idempotent status read."""
        if row.task_id is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        return TaskInput(
            row.task_id,
            "trends.forecast",
            "1",
            row.input_ref,
            row.fingerprint,
            row.release_id,
            None,
            session.actor,
            Audience("trends.owner", row.input_ref, AudienceProjectionKey("trends", "forecast", 1), "1"),
            command_identity(
                key="trends.forecast:" + row.input_ref,
                actor_ref=session.actor.id,
                operation="trends.forecast.refresh",
                target=row.input_ref,
                parameters_json=key_bytes(row.key),
                body_fingerprint=row.fingerprint,
                fingerprint_kind="sha256",
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class _Staged:
    result: TaskResult
    record: ForecastOutcomeRecord


class ForecastTaskHandler:
    def __init__(
        self,
        snapshots: ReadSnapshotFactory,
        worker: ports.ForecastWorkerPort | None,
        settings: TrendsSettings,
        *,
        repository: ForecastRepository,
        worker_absent_reason: str = "worker_not_configured",
    ) -> None:
        self.snapshots, self.worker, self.settings, self.repository = snapshots, worker, settings, repository
        self.worker_absent_reason = worker_absent_reason
        self._staged: dict[str, _Staged] = {}
        self._reserved: set[str] = set()
        self._lock = Lock()

    def execute(self, lease: TaskLease) -> TaskResult:
        ref = lease.task.input_ref + ":" + str(lease.generation)
        with self._lock:
            if ref in self._staged:
                return self._staged[ref].result
            if (
                ref in self._reserved
                or len(self._reserved) + len(self._staged) >= self.settings.forecast_staging_max
            ):
                raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)
            self._reserved.add(ref)
        try:
            staged = self._execute(lease, ref)
            with self._lock:
                self._staged[ref] = staged
            return staged.result
        finally:
            with self._lock:
                self._reserved.discard(ref)

    def _execute(self, lease: TaskLease, ref: str) -> _Staged:
        with self.snapshots.snapshot() as snapshot:
            row = self.repository.input(snapshot, lease.task.input_ref)
            at = snapshot.now
        if (
            row is None
            or row.task_id != lease.task.task_id
            or row.fingerprint != lease.task.input_fingerprint
        ):
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        outcome = (
            ports.WorkerUnavailable(self.worker_absent_reason)
            if self.worker is None
            else self.worker.forecast(ports.ForecastRequest(row.input_ref, None, row.series))
        )
        if isinstance(outcome, ports.WorkerForecast) and not self._valid(outcome, row):
            outcome = ports.WorkerUnavailable("worker_response_invalid")
        record = ForecastOutcomeRecord(row.input_ref, lease.task.task_id, lease.generation, outcome, at)
        digest = sha256(canonical_json(_outcome_data(outcome))).hexdigest()
        return _Staged(TaskResult(lease.task.task_id, lease.generation, ref, digest), record)

    def _valid(self, outcome: ports.WorkerForecast, row: ForecastInputRecord) -> bool:
        value = forecast.WorkerForecast(
            outcome.model_id,
            outcome.model_revision,
            outcome.model_sha256,
            outcome.policy_version,
            outcome.interval_level,
            tuple(forecast.ForecastStep(s.mission_day, s.low, s.median, s.high) for s in outcome.steps),
        )
        return forecast.validate_forecast(
            value,
            input_unit=row.unit,
            last_input_day=row.series[-1].mission_day,
            steps_max=self.settings.forecast_steps_max,
        )

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        if result.task_id != lease.task.task_id or result.generation != lease.generation:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        if self.repository.result(uow, lease.task.input_ref) is not None:
            self.discard(result)
            return
        row = self.repository.input(uow, lease.task.input_ref)
        if row is None:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        latest = self.repository.latest(uow, row.key)
        if latest is None or latest.input_ref != row.input_ref:
            self.discard(result)
            return
        with self._lock:
            staged = self._staged.get(result.result_ref)
        if staged is None or staged.result != result or staged.record.input_ref != row.input_ref:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        self.repository.save_result(uow, staged.record)
        # The staged copy is freed once written so the bounded staging map never fills with published work.
        self.discard(result)

    def discard(self, result: TaskResult) -> None:
        with self._lock:
            staged = self._staged.get(result.result_ref)
            if staged is not None and staged.result == result:
                del self._staged[result.result_ref]


def _outcome_data(value: ports.WorkerForecast | ports.WorkerUnavailable) -> object:
    if isinstance(value, ports.WorkerUnavailable):
        return {"reason": value.reason}
    return {
        "model": [value.model_id, value.model_revision, value.model_sha256],
        "policy_version": value.policy_version,
        "interval_level": str(value.interval_level),
        "steps": [
            [p.mission_day, str(p.low.value), str(p.median.value), str(p.high.value), p.median.unit.code]
            for p in value.steps
        ],
    }


class TrendsForecastQueries:
    def __init__(self, repository: ForecastRepository, clock: MissionClock) -> None:
        self.repository, self.clock = repository, clock

    def latest(
        self, snapshot: ReadSnapshot, session: SessionContext, key: ports.SeriesKey
    ) -> ports.ForecastView | ports.Unavailable:
        authorize(session, key.person_id)
        validate_key(key.person_id, key.mission_id, key.origin_mode, (key.code,))
        row = self.repository.latest(snapshot, key)
        if row is None:
            return ports.Unavailable("trends", "no_forecast_requested")
        result = self.repository.result(snapshot, row.input_ref)
        if result is None:
            return ports.Unavailable("trends", "forecast_pending")
        if isinstance(result.outcome, ports.WorkerUnavailable):
            return ports.Unavailable("forecast_worker", result.outcome.reason)
        value = result.outcome
        today = mission_day(self.clock, snapshot.now)
        if today is None:
            return ports.Unavailable("clock", "mission_day_unavailable")
        # Steps for days already past are dropped; a forecast with no step left is expired, never shown.
        steps = tuple(step for step in value.steps if step.mission_day >= today)
        if not steps:
            return ports.Unavailable("trends", "forecast_expired")
        return ports.ForecastView(
            "experimental_forecast",
            True,
            True,
            row.input_ref,
            row.observation_ids,
            tuple(p.mission_day for p in row.series),
            value.model_id,
            value.model_revision,
            value.model_sha256,
            value.policy_version,
            value.interval_level,
            steps,
            result.created_at,
        )
