"""Owner scoped deterministic summaries from a pinned release and observation time."""

from dataclasses import replace
from datetime import datetime, timedelta

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.met import MissionClock
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.trends.application import ports
from crew_health.trends.application.catalog import Catalog, comparison_of, load_catalog
from crew_health.trends.domain import baseline, model, trend
from crew_health.trends.settings import TrendsSettings


def authorize(session: SessionContext, person_id: str) -> None:
    if session.actor.kind != "person" or session.person_id != person_id:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def validate_key(
    person_id: str, mission_id: str, origin_mode: ports.OriginMode, codes: tuple[str, ...]
) -> None:
    try:
        for value in (person_id, mission_id, *codes):
            require_id(value)
    except ProblemError as error:
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value) from error
    if origin_mode not in ("LIVE", "REPLAY", "SIM"):
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)


def read_history(
    view: ReadSnapshot | UnitOfWork,
    session: SessionContext,
    request: ports.SummaryRequest,
    code: str,
    reader: ports.ObservationReader,
    clock: MissionClock,
    limit: int,
) -> tuple[tuple[ports.ObservationRef, ...], model.Selection]:
    rows = reader.accepted(
        view,
        session,
        request.person_id,
        code,
        mission_id=request.mission_id,
        origin_mode=request.origin_mode,
        limit=limit,
    )
    samples = tuple(to_sample(row) for row in rows if row.code == code)
    selected = baseline.select(
        samples,
        model.Partition(request.person_id, request.mission_id, request.origin_mode),
        view.now,
    )
    dated: list[model.Sample] = []
    exclusions = list(selected.exclusions)
    for sample in selected.samples:
        day = None if sample.observed_at is None else mission_day(clock, sample.observed_at)
        if day is None:
            # A reading the mission clock cannot place (before its epoch, in a gap) is shown as excluded, never
            # forced onto a day and never allowed to abort the other readings.
            exclusions.append(
                model.Exclusion(sample.observation_id, "outside_mission_clock", sample.observed_at)
            )
        else:
            dated.append(replace(sample, mission_day=day))
    return rows, model.Selection(tuple(dated), tuple(exclusions))


def mission_day(clock: MissionClock, at: datetime) -> int | None:
    """The mission day of an instant, or None when no retained clock version can place it (SYSTEM 5.3)."""
    try:
        return clock.mission_day(at)
    except ProblemError as error:
        if error.code in (ErrorCode.CONFIG_INVALID, ErrorCode.INVALID_VALUE):
            return None
        raise


def current_day(origin_mode: ports.OriginMode, clock: MissionClock, now: datetime) -> int | ports.Unavailable:
    """REPLAY needs its dataset mission clock (SYSTEM 5.3), which no provider binds yet (A9); LIVE and SIM need now."""
    if origin_mode == "REPLAY":
        return ports.Unavailable("clock", "replay_dataset_clock_unbound")
    day = mission_day(clock, now)
    return ports.Unavailable("clock", "mission_day_unavailable") if day is None else day


def to_sample(row: ports.ObservationRef) -> model.Sample:
    # The corrected observation time orders and bins the reading (D3); None while the time is unknown.
    return model.Sample(
        row.observation_id,
        row.patient_id,
        row.operator_id,
        row.mission_id,
        row.episode_id,
        row.origin_mode,
        row.code,
        row.quantity,
        row.quality_status,
        row.acquisition_method,
        row.device_id,
        row.context,
        row.supersedes,
        row.observed,
        row.normalized.corrected_at,
        row.normalized.state,
        None,
    )


def history_incomplete(rows: tuple[ports.ObservationRef, ...], limit: int, needed: datetime) -> bool:
    """A full page may have cut older readings; it is complete only when it already reaches back past `needed`.

    A short page is the whole history under the port's provider contract (filters before the limit).
    """
    if len(rows) < limit:
        return False
    times = tuple(row.normalized.corrected_at for row in rows if row.normalized.corrected_at is not None)
    return not times or min(times) >= needed


def reading(sample: model.Sample | None) -> ports.ReadingRef | None:
    if sample is None:
        return None
    if sample.observed_at is None or sample.mission_day is None:
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
    return ports.ReadingRef(
        sample.observation_id,
        sample.quantity,
        sample.observed_at,
        sample.mission_day,
        sample.acquisition_method,
        sample.device_id,
        sample.operator_id,
        sample.superseded_ids,
    )


def baseline_view(
    value: model.BaselineSummary | model.InsufficientBaseline,
) -> ports.BaselineSummary | ports.InsufficientBaseline:
    exclusions = tuple(ports.Exclusion(item.observation_id, item.reason) for item in value.exclusions)
    if isinstance(value, model.BaselineSummary):
        return ports.BaselineSummary(
            value.value,
            value.method,
            value.window_days,
            value.min_samples,
            value.sample_count,
            value.included_ids,
            exclusions,
            value.cohort_policy,
            value.exclusion_policy,
            value.catalog_version,
            value.basis,
            comparison_view(value.comparison, value.value),
        )
    return ports.InsufficientBaseline(
        value.sample_count,
        value.min_samples,
        value.window_days,
        value.included_ids,
        exclusions,
        value.cohort_policy,
        value.exclusion_policy,
        value.catalog_version,
    )


def comparison_view(value: model.Comparison, usual: Quantity) -> ports.ComparisonPolicy:
    return ports.ComparisonPolicy(
        Quantity(value.band, usual.unit),
        value.basis,
        value.source_ids,
        value.nasa_checked,
        value.to_be_set_by,
    )


class TrendsSummary:
    def __init__(
        self,
        reader: ports.ObservationReader | None,
        exclusions: ports.EpisodeExclusionReader | None,
        clock: MissionClock,
        settings: TrendsSettings,
    ) -> None:
        self.reader, self.exclusions, self.clock, self.settings = reader, exclusions, clock, settings

    def summaries(
        self, snapshot: ReadSnapshot, session: SessionContext, request: ports.SummaryRequest
    ) -> tuple[ports.IndicatorSummary | ports.IndicatorUnavailable, ...]:
        authorize(session, request.person_id)
        if len(request.codes) > self.settings.max_codes:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        validate_key(request.person_id, request.mission_id, request.origin_mode, request.codes)
        if self.reader is None:
            unavailable = ports.Unavailable("E01", "observation_reader_unbound")
            return tuple(
                ports.IndicatorUnavailable(code, request.origin_mode, unavailable) for code in request.codes
            )
        catalog = load_catalog(snapshot.release)
        if isinstance(catalog, ports.Unavailable):
            return tuple(
                ports.IndicatorUnavailable(code, request.origin_mode, catalog) for code in request.codes
            )
        day = current_day(request.origin_mode, self.clock, snapshot.now)
        if isinstance(day, ports.Unavailable):
            return tuple(ports.IndicatorUnavailable(code, request.origin_mode, day) for code in request.codes)
        return tuple(
            self._one(snapshot, session, request, code, catalog, self.reader, day) for code in request.codes
        )

    def _one(
        self,
        snapshot: ReadSnapshot,
        session: SessionContext,
        request: ports.SummaryRequest,
        code: str,
        catalog: Catalog,
        reader: ports.ObservationReader,
        day: int,
    ) -> ports.IndicatorSummary | ports.IndicatorUnavailable:
        indicator = catalog.indicator(code)
        if indicator is None:
            return ports.IndicatorUnavailable(
                code, request.origin_mode, ports.Unavailable("trends", "code_not_in_catalog")
            )
        rows, selected = read_history(
            snapshot, session, request, code, reader, self.clock, self.settings.history_max_rows
        )
        today, latest = baseline.readings(selected, day)
        policy = model.BaselinePolicy(
            indicator.baseline.method,
            indicator.baseline.window_days,
            indicator.baseline.min_samples,
            indicator.baseline.basis.root,
            catalog.catalog_version,
            indicator.decimals,
            comparison_of(indicator),
        )
        usual = self._usual(snapshot, session, request, selected, today, policy, rows)
        points = trend.daily_points(selected.samples, current_day=day, trend_days=indicator.trend_days)
        first, _ = self.clock.day_bounds(
            trend.window_start(day, indicator.trend_days),
            version=self.clock.version_at(snapshot.now).clock_version,
        )
        visible_trend: tuple[ports.TrendPoint, ...] | ports.Unavailable
        if history_incomplete(rows, self.settings.history_max_rows, first):
            visible_trend = ports.Unavailable("trends", "history_limit_reached")
        else:
            visible_trend = tuple(
                ports.TrendPoint(p.mission_day, p.value, p.observation_id, p.readings_that_day)
                for p in points
            )
        state: ports.SummaryState = "no_reading" if today is None else "no_baseline"
        delta = None
        if today is not None and isinstance(usual, ports.BaselineSummary):
            state = baseline.compare(today.quantity, usual.value, indicator.decimals, policy.comparison)
            delta = Quantity(today.quantity.value - usual.value.value, today.quantity.unit)
        ids = (
            {p.observation_id for p in points if p.observation_id is not None}
            if isinstance(visible_trend, tuple)
            else set()
        )
        if today is not None:
            ids.add(today.observation_id)
        methods = tuple(
            sorted({sample.acquisition_method for sample in selected.samples if sample.observation_id in ids})
        )
        return ports.IndicatorSummary(
            code,
            request.origin_mode,
            state,
            reading(today),
            reading(latest),
            usual,
            delta,
            visible_trend,
            methods,
            indicator.decimals,
        )

    def _usual(
        self,
        snapshot: ReadSnapshot,
        session: SessionContext,
        request: ports.SummaryRequest,
        selected: model.Selection,
        today: model.Sample | None,
        policy: model.BaselinePolicy,
        rows: tuple[ports.ObservationRef, ...],
    ) -> ports.BaselineSummary | ports.InsufficientBaseline | ports.Unavailable:
        if self.exclusions is None:
            return ports.Unavailable("E15", "episode_periods_unavailable")
        periods = self.exclusions.periods(
            snapshot,
            session,
            request.person_id,
            mission_id=request.mission_id,
            origin_mode=request.origin_mode,
            until=snapshot.now,
            limit=self.settings.history_max_rows,
        )
        if not periods.complete:
            return ports.Unavailable("E15", "episode_periods_incomplete")
        if today is None or today.observed_at is None:
            return ports.Unavailable("trends", "no_current_reading")
        if history_incomplete(
            rows, self.settings.history_max_rows, today.observed_at - timedelta(days=policy.window_days)
        ):
            return ports.Unavailable("trends", "history_limit_reached")
        value = baseline.baseline(
            selected,
            today,
            policy,
            tuple(model.Period(p.start, p.end) for p in periods.periods),
            exclusion_policy=periods.policy_version,
        )
        return baseline_view(value)
