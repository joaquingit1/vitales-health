"""Owner-scoped summaries, provider failures and configuration-driven baselines."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ConfigName
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.domain.observations import ObservationRecord
from crew_health.trends.adapters.catalog_check import IndicatorCatalogCheck
from crew_health.trends.application.catalog import load_catalog
from crew_health.trends.application.ports import (
    BaselineSummary,
    ExclusionPeriod,
    ExclusionPeriods,
    IndicatorSummary,
    IndicatorUnavailable,
    InsufficientBaseline,
    ObservationRef,
    OriginMode,
    SummaryRequest,
    Unavailable,
)
from crew_health.trends.application.summary import TrendsSummary
from crew_health.trends.settings import TrendsSettings

from .support import catalog_release, mission_clock, person_session, record, settings, timed

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


class NoStorage:
    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        raise AssertionError("Summary must use its reader, not raw storage")

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        raise AssertionError("Summary must never mutate storage")


@dataclass(frozen=True)
class Snapshot:
    release: MemoryRelease
    now: datetime = NOW
    connection: NoStorage = NoStorage()


@dataclass
class Rows:
    values: tuple[ObservationRecord, ...]
    calls: int = 0

    def accepted(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        code: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        limit: int,
    ) -> tuple[ObservationRef, ...]:
        self.calls += 1
        return self.values[:limit]


@dataclass
class Periods:
    complete: bool = True
    periods_value: tuple[ExclusionPeriod, ...] = ()

    def periods(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        until: datetime,
        limit: int,
    ) -> ExclusionPeriods:
        return ExclusionPeriods("episodes.v1", self.periods_value, self.complete)


def observation(day: int, value: str, *, identity: str | None = None) -> ObservationRecord:
    return record(
        identity or f"obs_{day}", Quantity(Decimal(value), Unit("/min")), NOW - timedelta(days=14 - day)
    )


def baseline_rows() -> tuple[ObservationRecord, ...]:
    return tuple(
        observation(day, value)
        for day, value in ((6, "10"), (8, "20"), (10, "30"), (12, "40"), (13, "50"), (14, "45"))
    )


def summary_service(
    reader: Rows | None, exclusions: Periods | None = None, *, history: int = 1000
) -> TrendsSummary:
    return TrendsSummary(
        reader,
        exclusions,
        mission_clock(),
        TrendsSettings.read(settings(**{"trends.history_max_rows": history})),
    )


def get_summary(service: TrendsSummary, snapshot: Snapshot | None = None) -> IndicatorSummary:
    result = service.summaries(
        snapshot or Snapshot(catalog_release()),
        person_session(),
        SummaryRequest("person", "mission", "SIM", ("pulse",)),
    )
    assert len(result) == 1
    item = result[0]
    assert isinstance(item, IndicatorSummary)
    return item


@pytest.mark.trace("E14-01", "V25")
@pytest.mark.parametrize(
    "session", [person_session("another"), person_session(kind="station"), person_session(kind="assistant")]
)
def test_only_owner_can_read_summary(session: SessionContext) -> None:
    reader = Rows(baseline_rows())
    with pytest.raises(ProblemError) as caught:
        summary_service(reader, Periods()).summaries(
            Snapshot(catalog_release()), session, SummaryRequest("person", "mission", "SIM", ("pulse",))
        )
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assert reader.calls == 0


@pytest.mark.trace("E14-01", "R12")
def test_missing_observation_reader_has_typed_unavailable_for_each_code() -> None:
    result = summary_service(None).summaries(
        Snapshot(catalog_release()),
        person_session(),
        SummaryRequest("person", "mission", "SIM", ("pulse", "mood")),
    )
    assert result == (
        IndicatorUnavailable("pulse", "SIM", Unavailable("E01", "observation_reader_unbound")),
        IndicatorUnavailable("mood", "SIM", Unavailable("E01", "observation_reader_unbound")),
    )


@pytest.mark.trace("E14-01", "R12")
@pytest.mark.parametrize(
    "exclusions,reason",
    [(None, "episode_periods_unavailable"), (Periods(False), "episode_periods_incomplete")],
)
def test_missing_or_incomplete_episode_periods_preserve_today_latest_and_trend(
    exclusions: Periods | None, reason: str
) -> None:
    summary = get_summary(summary_service(Rows(baseline_rows()), exclusions))
    assert summary.usual == Unavailable("E15", reason)
    assert summary.state == "no_baseline" and summary.delta is None
    assert summary.today is not None and summary.today.value.value == Decimal("45")
    assert summary.latest == summary.today
    assert isinstance(summary.trend, tuple) and summary.trend[-1].value == summary.today.value


@pytest.mark.trace("E14-01", "V14")
def test_old_reading_keeps_its_own_time_and_never_becomes_today() -> None:
    sample = observation(13, "42")
    summary = get_summary(summary_service(Rows((sample,)), Periods()))
    assert summary.state == "no_reading" and summary.today is None
    assert summary.latest is not None and summary.latest.observed_at == sample.normalized.corrected_at
    assert summary.latest.mission_day == 13
    assert summary.delta is None


@pytest.mark.trace("E14-01", "R12")
def test_absent_catalog_is_unavailable_and_corrupt_catalog_is_config_invalid() -> None:
    assert load_catalog(MemoryRelease("release_empty", {})) == Unavailable(
        "trends", "indicator_catalog_absent"
    )
    import hashlib

    raw = b'{"indicators":[]}'
    document = ConfigDocumentView(ConfigName.INDICATOR_CATALOG, hashlib.sha256(raw).hexdigest(), raw)
    invalid = MemoryRelease("release_invalid", {document.name: document})
    with pytest.raises(ProblemError) as caught:
        load_catalog(invalid)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    result = summary_service(Rows(())).summaries(
        Snapshot(MemoryRelease("release_empty", {})),
        person_session(),
        SummaryRequest("person", "mission", "SIM", ("pulse",)),
    )
    assert result == (
        IndicatorUnavailable("pulse", "SIM", Unavailable("trends", "indicator_catalog_absent")),
    )


@pytest.mark.trace("E14-01", "R12")
def test_unknown_indicator_has_typed_unavailable() -> None:
    result = summary_service(Rows(())).summaries(
        Snapshot(catalog_release()),
        person_session(),
        SummaryRequest("person", "mission", "SIM", ("unknown_code",)),
    )
    assert result == (
        IndicatorUnavailable("unknown_code", "SIM", Unavailable("trends", "code_not_in_catalog")),
    )


@pytest.mark.trace("E14-01", "V31")
def test_history_cap_never_presents_truncated_baseline_or_trend_as_complete() -> None:
    rows = (observation(14, "40"), observation(13, "30"), observation(12, "20"))
    summary = get_summary(summary_service(Rows(rows), Periods(), history=3))
    assert summary.usual == Unavailable("trends", "history_limit_reached")
    assert summary.trend == Unavailable("trends", "history_limit_reached")
    assert summary.today is not None and summary.today.observation_id == "obs_14"


def edited_value(
    *, window: int | None = None, minimum: int | None = None, within: dict[str, object] | None = None
) -> dict[str, object]:
    """The example catalog with only the pulse baseline edited, as a release author would edit it."""
    document = catalog_release().document(ConfigName.INDICATOR_CATALOG)
    value = cast(dict[str, object], json.loads(document.raw))
    indicators = value["indicators"]
    assert isinstance(indicators, list)
    for indicator in indicators:
        assert isinstance(indicator, dict)
        if indicator["code"] == "pulse":
            baseline = indicator["baseline"]
            assert isinstance(baseline, dict)
            if window is not None:
                baseline["window_days"] = window
            if minimum is not None:
                baseline["min_samples"] = minimum
            if within is not None:
                band = baseline["within_usual"]
                assert isinstance(band, dict)
                band.update(within)
    return value


def edited_catalog(*, window: int, minimum: int) -> MemoryRelease:
    return catalog_release(edited_value(window=window, minimum=minimum))


@pytest.mark.trace("E14-01", "V75", "AGENTS-1.1")
def test_editing_catalog_window_and_minimum_changes_baseline_without_code_change() -> None:
    service = summary_service(Rows(baseline_rows()), Periods())
    original = get_summary(service)
    changed = get_summary(service, Snapshot(edited_catalog(window=3, minimum=2)))
    assert isinstance(original.usual, BaselineSummary) and original.usual.value.value == Decimal("30")
    assert isinstance(changed.usual, BaselineSummary) and changed.usual.value.value == Decimal("45")
    assert original.usual.catalog_version != changed.usual.catalog_version
    insufficient = get_summary(service, Snapshot(edited_catalog(window=3, minimum=3)))
    assert isinstance(insufficient.usual, InsufficientBaseline) and insufficient.usual.sample_count == 2
    assert insufficient.state == "no_baseline"


@pytest.mark.trace("E14-01", "V31")
def test_history_completeness_is_checked_separately_for_baseline_and_trend() -> None:
    rows = (observation(14, "40"), observation(13, "30"), observation(9, "20"))
    result = get_summary(
        summary_service(Rows(rows), Periods(), history=3), Snapshot(edited_catalog(window=3, minimum=1))
    )
    assert isinstance(result.usual, BaselineSummary) and result.usual.value.value == Decimal("30")
    assert result.trend == Unavailable("trends", "history_limit_reached")


@pytest.mark.trace("E14-01", "V25")
@pytest.mark.parametrize(
    "summary_request",
    [
        SummaryRequest("bad id", "mission", "SIM", ("pulse",)),
        SummaryRequest("person", "bad id", "SIM", ("pulse",)),
        SummaryRequest("person", "mission", "SIM", ("bad code",)),
        SummaryRequest("person", "mission", "SIM", ("pulse",) * 201),
    ],
)
def test_invalid_identifiers_and_request_size_are_rejected(summary_request: SummaryRequest) -> None:
    session = replace(person_session(), person_id=summary_request.person_id)
    with pytest.raises(ProblemError) as caught:
        summary_service(Rows(())).summaries(Snapshot(catalog_release()), session, summary_request)
    assert caught.value.code is ErrorCode.INVALID_VALUE


@pytest.mark.trace("E14-01", "V25")
def test_reader_storage_failure_is_not_downgraded_to_unavailable() -> None:
    class FailingRows(Rows):
        def accepted(
            self,
            view: ReadSnapshot | UnitOfWork,
            session: SessionContext,
            person_id: str,
            code: str,
            *,
            mission_id: str,
            origin_mode: OriginMode,
            limit: int,
        ) -> tuple[ObservationRef, ...]:
            raise OSError("disk failure")

    with pytest.raises(OSError, match="disk failure"):
        get_summary(summary_service(FailingRows(()), Periods()))


@pytest.mark.trace("E14-01", "V09", "V47", "V14")
@pytest.mark.parametrize(
    "bad_sample,reason",
    [
        (
            timed(observation(14, "999", identity="obs_invalid"), NOW + timedelta(days=2000)),
            "future_time",
        ),
        (
            replace(
                timed(observation(14, "999", identity="obs_invalid"), NOW - timedelta(days=2000)),
                patient_id="another",
            ),
            "other_partition",
        ),
        (
            replace(
                timed(observation(14, "999", identity="obs_invalid"), NOW - timedelta(days=2000)),
                quality_status="rejected",
            ),
            "not_accepted",
        ),
    ],
)
def test_unusable_samples_are_excluded_before_mission_day_conversion(
    bad_sample: ObservationRecord, reason: str
) -> None:
    summary = get_summary(summary_service(Rows(baseline_rows() + (bad_sample,)), Periods()))
    assert isinstance(summary.usual, BaselineSummary)
    assert summary.usual.value.value == Decimal("30") and summary.usual.sample_count == 5
    assert ("obs_invalid", reason) in {
        (item.observation_id, item.reason) for item in summary.usual.exclusions
    }


@pytest.mark.trace("E14-01", "R12", "V31", "AGENTS-1.1")
def test_usual_value_carries_the_within_usual_band_and_its_unsourced_provenance() -> None:
    """m3: the band used for within_usual travels with the usual value, marked unsourced, in the reading's unit."""
    rows = tuple(observation(day, "60") for day in range(6, 14)) + (observation(14, "61"),)
    summary = get_summary(summary_service(Rows(rows), Periods()))
    assert isinstance(summary.usual, BaselineSummary)
    comparison = summary.usual.comparison
    assert comparison.band == Quantity(Decimal(0), Unit("/min")) and comparison.basis == "team default"
    assert comparison.source_ids == () and comparison.to_be_set_by == "clinical review"
    assert set(comparison.nasa_checked) == {"C09", "C14", "C15", "C23", "N03"}
    assert summary.state == "above_usual"


@pytest.mark.trace("E14-01", "R12", "V31", "V75", "AGENTS-1.1")
def test_editing_the_catalog_band_changes_the_verdict_without_code_change() -> None:
    """The within_usual band and its provenance come from the catalog release, not from code."""
    rows = tuple(observation(day, "60") for day in range(6, 14)) + (observation(14, "62"),)
    service = summary_service(Rows(rows), Periods())
    assert get_summary(service).state == "above_usual"
    widened = catalog_release(edited_value(within={"band": "2", "basis": "sourced", "source_ids": ["C09"]}))
    changed = get_summary(service, Snapshot(widened))
    assert changed.state == "within_usual"
    assert isinstance(changed.usual, BaselineSummary)
    assert changed.usual.comparison.band == Quantity(Decimal(2), Unit("/min"))
    assert changed.usual.comparison.basis == "sourced" and changed.usual.comparison.source_ids == ("C09",)
    narrowed = catalog_release(edited_value(within={"band": "1"}))
    assert get_summary(service, Snapshot(narrowed)).state == "above_usual"


def candidate(value: dict[str, object]) -> CandidateRelease:
    raw = json.dumps(value, sort_keys=True).encode("utf-8")
    document = ConfigDocumentView(ConfigName.INDICATOR_CATALOG, "candidate", raw)
    return CandidateRelease("release_candidate", {ConfigName.INDICATOR_CATALOG: document})


@pytest.mark.trace("E14-01", "R12", "AGENTS-3", "AGENTS-6")
@pytest.mark.parametrize(
    "within",
    [
        {"band": "0.5"},
        {"basis": "sourced"},
    ],
)
def test_release_check_rejects_a_band_finer_than_display_precision_or_sourced_without_a_source(
    within: dict[str, object],
) -> None:
    check = IndicatorCatalogCheck()
    assert check.check(candidate(edited_value())) == ()
    (finding,) = check.check(candidate(edited_value(within=within)))
    assert finding.document is ConfigName.INDICATOR_CATALOG
    assert finding.message == "comparison_band_invalid"
    assert finding.pointer.endswith("/baseline/within_usual/band")


@pytest.mark.trace("E14-01", "R12", "AGENTS-3")
def test_release_check_rejects_a_catalog_without_its_band() -> None:
    value = edited_value()
    indicators = value["indicators"]
    assert isinstance(indicators, list) and isinstance(indicators[0], dict)
    baseline = indicators[0]["baseline"]
    assert isinstance(baseline, dict)
    del baseline["within_usual"]
    (finding,) = IndicatorCatalogCheck().check(candidate(value))
    assert finding.message == "indicator_catalog_invalid"
