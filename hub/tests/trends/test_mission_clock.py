"""Mission days come from the real mission clock: early days, pre-epoch readings and REPLAY datasets."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.history import MissionClockService
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.met import MetLabel, MissionClock, MissionClockVersion
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.uow import Actor
from crew_health.records.domain.observations import ObservationRecord
from crew_health.trends.application.ports import (
    BaselineSummary,
    IndicatorSummary,
    IndicatorUnavailable,
    SeriesKey,
    SummaryRequest,
    Unavailable,
)
from crew_health.trends.application.summary import TrendsSummary
from crew_health.trends.settings import TrendsSettings

from .support import (
    EPOCH,
    _StationSettings,
    catalog_release,
    mission_clock,
    person_session,
    settings,
    station,
    timed,
)
from .test_forecast import ForecastHarness
from .test_summary import NOW, Periods, Rows, Snapshot, baseline_rows, observation


def real_clock(tmp_path: Path, epoch: datetime) -> MissionClockService:
    h = station(tmp_path)
    repository = SqlClockRepository(
        settings=_StationSettings(), node_id=h.db.origin_node_id, incarnation_id=h.db.origin_incarnation
    )
    service = MissionClockService(snapshots=h.db, repository=repository)
    with h.db.begin(actor=Actor("operator", "operator"), correlation_id="mission") as uow:
        service.append(
            uow, MissionClockVersion("mission_clock_1", 1, epoch, epoch, None, "Day {day}"), ids=h.db.ids
        )
    return service


def summarize(rows: tuple[ObservationRecord, ...], clock: MissionClock, origin: str = "SIM") -> object:
    service = TrendsSummary(Rows(rows), Periods(), clock, TrendsSettings.read(settings()))
    request = SummaryRequest("person", "mission", origin, ("pulse",))  # type: ignore[arg-type]
    return service.summaries(Snapshot(catalog_release()), person_session(), request)[0]


@pytest.mark.trace("E14-01", "SYSTEM-5.3", "V14")
def test_early_mission_days_clamp_the_trend_window_to_day_one_with_the_real_clock(tmp_path: Path) -> None:
    """M2 regression: on mission day 3 with trend_days 7 the window is days 1 to 3, not days -3 to 3."""
    clock = real_clock(tmp_path, NOW - timedelta(days=2, hours=12))
    assert clock.mission_day(NOW) == 3
    today = timed(observation(14, "45"), NOW - timedelta(hours=1))
    summary = summarize((today,), clock)
    assert isinstance(summary, IndicatorSummary)
    assert isinstance(summary.trend, tuple)
    assert [(p.mission_day, p.value) for p in summary.trend] == [(1, None), (2, None), (3, today.quantity)]
    assert summary.today is not None and summary.today.mission_day == 3


def replay_rows() -> tuple[ObservationRecord, ...]:
    dataset = datetime(2014, 10, 1, 12, tzinfo=UTC)
    return tuple(
        replace(
            timed(observation(day, str(400 + day)), dataset + timedelta(days=day), "replay"),
            origin_mode="REPLAY",
        )
        for day in range(1, 8)
    )


@pytest.mark.trace("E14-01", "SYSTEM-5.3", "V10")
@pytest.mark.parametrize("real", [False, True])
def test_replay_summary_is_unavailable_until_its_dataset_clock_is_bound(tmp_path: Path, real: bool) -> None:
    """M1 regression: 2014 REPLAY readings never raise and are never placed on the live mission clock."""
    clock: MissionClock = real_clock(tmp_path, EPOCH) if real else mission_clock()
    assert summarize(replay_rows(), clock, "REPLAY") == IndicatorUnavailable(
        "pulse", "REPLAY", Unavailable("clock", "replay_dataset_clock_unbound")
    )


@pytest.mark.trace("E14-01", "SYSTEM-5.3", "V14")
@pytest.mark.parametrize("real", [False, True])
def test_reading_before_the_mission_clock_is_excluded_and_the_rest_still_summarize(
    tmp_path: Path, real: bool
) -> None:
    """M1 regression: one pre-flight reading the clock cannot place is shown as excluded, not a crash."""
    clock: MissionClock = real_clock(tmp_path, EPOCH) if real else mission_clock()
    before = timed(observation(14, "60", identity="obs_preflight"), EPOCH - timedelta(days=3))
    summary = summarize(baseline_rows() + (before,), clock)
    assert isinstance(summary, IndicatorSummary)
    assert summary.today is not None and summary.today.observation_id == "obs_14"
    assert isinstance(summary.usual, BaselineSummary) and summary.usual.value.value == Decimal("30")
    assert ("obs_preflight", "outside_mission_clock") in {
        (e.observation_id, e.reason) for e in summary.usual.exclusions
    }


@pytest.mark.trace("E14-01", "SYSTEM-5.3")
def test_now_outside_every_clock_version_is_unavailable_for_every_code() -> None:
    clock = mission_clock()
    service = TrendsSummary(Rows(baseline_rows()), Periods(), clock, TrendsSettings.read(settings()))
    early = Snapshot(catalog_release(), now=EPOCH - timedelta(days=1))
    request = SummaryRequest("person", "mission", "SIM", ("pulse", "mood"))
    unavailable = Unavailable("clock", "mission_day_unavailable")
    assert service.summaries(early, person_session(), request) == (
        IndicatorUnavailable("pulse", "SIM", unavailable),
        IndicatorUnavailable("mood", "SIM", unavailable),
    )


@pytest.mark.trace("E14-03", "SYSTEM-5.3", "V10")
def test_replay_forecast_is_unavailable_until_its_dataset_clock_is_bound() -> None:
    h = ForecastHarness()
    h.reader.values = replay_rows()
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        result = h.commands.refresh(uow, h.session, SeriesKey("person", "mission", "REPLAY", "pulse"))
    assert result == Unavailable("clock", "replay_dataset_clock_unbound")
    assert not h.repository.inputs and not h.factory.state.tasks


@pytest.mark.trace("E14-03", "SYSTEM-5.3")
def test_pre_epoch_reading_never_reaches_the_forecast_input() -> None:
    h = ForecastHarness()
    before = timed(observation(14, "60", identity="obs_preflight"), EPOCH - timedelta(hours=1))
    h.reader.values = baseline_rows() + (before,)
    with h.factory.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        ticket = h.commands.refresh(uow, h.session, SeriesKey("person", "mission", "SIM", "pulse"))
    assert not isinstance(ticket, Unavailable)
    assert "obs_preflight" not in h.repository.inputs[ticket.input_ref].observation_ids


@pytest.mark.trace("E14-03", "SYSTEM-5.3")
def test_reading_a_stored_forecast_outside_every_clock_version_is_unavailable() -> None:
    h = ForecastHarness()
    h.refresh()
    h.finish(h.claim())
    h.factory.clock = MemoryClock(EPOCH - timedelta(days=1), 0, "process")
    assert h.latest() == Unavailable("clock", "mission_day_unavailable")


@pytest.mark.trace("E14-01", "AGENTS-6")
def test_a_clock_storage_failure_is_never_downgraded_to_an_unplaced_reading() -> None:
    class BrokenClock:
        def version_at(self, at: datetime) -> MissionClockVersion:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

        def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

        def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

        def mission_day(self, at: datetime, *, version: str | None = None) -> int:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

        def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

    with pytest.raises(ProblemError) as caught:
        summarize(baseline_rows(), BrokenClock())
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
