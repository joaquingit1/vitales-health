"""T-E01-12 V46: durable segment, correction, mission-day and HLC vectors."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.clock.application.history import MissionClockService
from crew_health.clock.domain.hlc import advance
from crew_health.clock.domain.met import met_label
from crew_health.kernel.clock import ClockCorrection, ClockSegment, Hlc, TimeInput
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.testing import MemoryClock, MemoryIds
from crew_health.kernel.uow import Actor
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-12", "V46", "D3")]


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 2, hour, minute, tzinfo=UTC)


@pytest.fixture
def station(tmp_path: Path):
    clock = MemoryClock(at(12), 0, "epoch_test")
    db = database(tmp_path, clock=clock)
    db.migrate(
        Path("hub/src/crew_health/clock/adapters/migrations/0001_clock.sql").read_text(encoding="utf-8")
    )
    repository = SqlClockRepository(settings=db.settings, node_id="node_test", incarnation_id="inc_test")
    service = PersistentClocks(
        repository=repository,
        hub_source_clock_id="source",
        settings=db.settings,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(1000))),
    )
    return db, repository, service, clock


def correction(version: int, segment: str, offset_ms: int = 0, *, source: str = "source") -> ClockCorrection:
    return ClockCorrection(
        f"correction_{version}", source, segment, version, at(0), None, offset_ms, 0, at(12), "operator"
    )


def test_equal_raw_timestamp_in_durable_segments(station) -> None:
    db, repository, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A"))
        service.open(uow, ClockSegment("source", "physical", "segment_B", "segment_A", 20, 2))
        service.apply(uow, correction(2, "segment_B", 3600000))
    restarted = PersistentClocks(repository=repository, settings=db.settings, ids=MemoryIds(identifiers=()))
    with db.snapshot() as snapshot:
        first = restarted.normalize(
            snapshot, TimeInput(at(10, 5), "source", "segment_A", 1, "source_raw", "LIVE")
        )
        second = restarted.normalize(
            snapshot, TimeInput(at(10, 5), "source", "segment_B", 2, "source_raw", "LIVE")
        )
        assert first.corrected_at == at(10, 5)
        assert second.corrected_at == at(11, 5)
        assert (
            restarted.for_sequence(snapshot, source_clock_id="source", sequence=19).segment_id == "segment_A"
        )
        assert (
            restarted.for_sequence(snapshot, source_clock_id="source", sequence=20).segment_id == "segment_B"
        )
        assert (
            restarted.normalize(
                snapshot, TimeInput(at(10, 5), "source", "unmapped", 1, "source_raw", "LIVE")
            ).state
            == "unknown"
        )
        assert tuple(value.version for value in restarted.history(snapshot, after_version=0, limit=10)) == (
            1,
            2,
        )


def test_clock_history_is_global_and_hub_corrected_uses_capture_revision(station) -> None:
    db, _, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A", 3600000))
        service.open(uow, ClockSegment("other", "physical", "segment_C", None, 1, 2))
        service.apply(uow, correction(2, "segment_C", source="other"))
        service.apply(uow, correction(3, "segment_A", 7200000))
    with db.snapshot() as snapshot:
        normalized = service.normalize(
            snapshot, TimeInput(at(11), "source", "segment_A", 1, "hub_corrected", "LIVE")
        )
        assert normalized.corrected_at == at(12)
        assert normalized.correction_id == "correction_3"
        assert tuple(c.version for c in service.history(snapshot, after_version=1, limit=10)) == (2, 3)
        assert (
            service.normalize(
                snapshot, TimeInput(at(11), "source", "segment_A", 99, "hub_corrected", "LIVE")
            ).state
            == "unknown"
        )


def test_future_negative_elapsed_and_rollback(station) -> None:
    db, _, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A"))
    with (
        pytest.raises(RuntimeError),
        db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow,
    ):
        service.apply(uow, correction(2, "segment_A", 3600000))
        raise RuntimeError("crash before commit")
    with db.snapshot() as snapshot:
        value = TimeInput(at(13), "source", "segment_A", 1, "source_raw", "LIVE")
        assert service.normalize(snapshot, value).state == "future"
        assert service.elapsed(snapshot, value, value) is None
        start = TimeInput(at(11), "source", "segment_A", 1, "source_raw", "LIVE")
        end = TimeInput(at(10), "source", "segment_A", 1, "source_raw", "LIVE")
        assert service.elapsed(snapshot, start, end) is None
        assert len(service.history(snapshot, after_version=0, limit=10)) == 1


def test_segment_revision_validation_and_authority(station) -> None:
    db, _, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        segment = ClockSegment("source", "physical", "segment_A", None, 1, 1)
        service.open(uow, segment)
        service.open(uow, segment)
        service.apply(uow, correction(1, "segment_A"))
        with pytest.raises(ProblemError):
            service.apply(uow, correction(1, "segment_A"))
        with pytest.raises(ProblemError):
            service.open(uow, ClockSegment("source", "physical", "segment_B", None, 10, 2))
    with (
        pytest.raises(ProblemError),
        db.begin(actor=Actor("assistant", "assistant_test"), correlation_id="correlation_test") as uow,
    ):
        service.apply(uow, correction(2, "segment_A"))


def test_hlc_restart_regression_future_and_rollback(station) -> None:
    db, repository, service, clock = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        first = service.tick(uow)
    clock.at -= timedelta(hours=1)
    restarted = PersistentClocks(repository=repository, settings=db.settings, ids=MemoryIds(identifiers=()))
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        second = restarted.tick(uow)
        assert second > first
        assert second.physical_ms == first.physical_ms
        with pytest.raises(ProblemError):
            restarted.observe(uow, Hlc(first.physical_ms + 3600000, 1, "remote", "incarnation"))
    with (
        pytest.raises(RuntimeError),
        db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow,
    ):
        restarted.tick(uow)
        raise RuntimeError("crash")
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        assert restarted.tick(uow).logical == second.logical + 1


def test_mission_versions_are_opaque_pinned_and_half_open(station) -> None:
    db, repository, _, _ = station
    first = MissionClockVersion("clock-z", 1, at(0), at(0), None, "Day {day}")
    second = MissionClockVersion("clock-a", 2, at(1), at(1), None, "Mission day {day}")
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        repository.append_mission_version(uow.connection, first)
        repository.append_mission_version(uow.connection, second)
    mission = MissionClockService(snapshots=db, repository=repository)
    assert mission.version_at(at(2)).clock_version == "clock-a"
    assert mission.met(at(0), version="clock-z").met == "000/00:00:00"
    assert mission.met(at(0) + timedelta(days=1), version="clock-z").mission_day == 2
    assert mission.day_bounds(2, version="clock-z") == (at(0) + timedelta(days=1), at(0) + timedelta(days=2))
    assert mission.met(at(1), version="clock-a").day_label == "Mission day 1"
    assert mission.met(at(0), version="clock-z").day_label == "Day 1"
    with pytest.raises(ProblemError):
        mission.met(at(0) - timedelta(seconds=1), version="clock-z")
    with pytest.raises(ProblemError):
        mission.met(at(0) + timedelta(days=1000), version="clock-z")
    with pytest.raises(ProblemError):
        mission.day_bounds(0, version="clock-z")


@given(st.integers(min_value=0, max_value=999 * 86400 + 86399))
def test_mission_time_integer_golden_roundtrip(seconds: int) -> None:
    epoch = at(0)
    version = MissionClockVersion("opaque", 1, epoch, epoch, None, "Day {day}")
    label = met_label(epoch + timedelta(seconds=seconds), version)
    day_text, time_text = label.met.split("/")
    h, m, s = map(int, time_text.split(":"))
    assert int(day_text) * 86400 + h * 3600 + m * 60 + s == seconds
    assert label.mission_day == seconds // 86400 + 1


@given(st.integers(min_value=0, max_value=100000), st.integers(min_value=0, max_value=100))
def test_hlc_strictly_increases_at_same_or_earlier_physical_time(physical: int, logical: int) -> None:
    previous = Hlc(physical, logical, "node", "inc")
    result = advance(physical, previous, None, node_id="node", incarnation_id="inc", logical_max=1000)
    assert result > previous


def test_hlc_independent_golden_branches_and_overflow() -> None:
    local = Hlc(100, 2, "node", "inc")
    assert advance(101, local, None, node_id="node", incarnation_id="inc", logical_max=10).logical == 0
    assert (
        advance(
            99, local, Hlc(100, 5, "remote", "inc"), node_id="node", incarnation_id="inc", logical_max=10
        ).logical
        == 6
    )
    assert (
        advance(
            99, local, Hlc(101, 5, "remote", "inc"), node_id="node", incarnation_id="inc", logical_max=10
        ).logical
        == 6
    )
    with pytest.raises(ProblemError):
        advance(100, Hlc(100, 10, "node", "inc"), None, node_id="node", incarnation_id="inc", logical_max=10)


def test_regression_freezes_until_new_correction_and_sweep(station) -> None:
    db, _, service, clock = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.check_regression(uow)
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A"))
        service.complete_sweep(uow, correction_version=1)
        assert service.clearing_allowed(uow)
    clock.at -= timedelta(hours=1)
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        assert service.check_regression(uow) is not None
        assert not service.clearing_allowed(uow)
        captured = TimeInput(at(9), "source", "segment_A", 1, "source_raw", "LIVE")
        assert service.normalize(uow, captured).state == "unknown"
        with pytest.raises(ProblemError):
            service.complete_sweep(uow, correction_version=1)
        service.apply(uow, correction(2, "segment_A", 3600000))
        assert not service.clearing_allowed(uow)
        service.complete_sweep(uow, correction_version=2)
        assert service.clearing_allowed(uow)


def test_freshness_requires_entire_uncertainty_interval(station) -> None:
    from crew_health.clock.domain.corrections import fresh
    from crew_health.kernel.clock import NormalizedTime

    value = NormalizedTime(at(11), at(11), "correction_1", 1000, "known")
    assert not fresh(value, now=at(12), window=timedelta(hours=1))
    assert fresh(value, now=at(12), window=timedelta(hours=1, seconds=1))
    future = NormalizedTime(at(12), at(12), "correction_1", 1, "known")
    assert not fresh(future, now=at(12), window=timedelta(hours=1))
    replay = NormalizedTime(at(11), at(11), "correction_1", 0, "replay")
    assert not fresh(replay, now=at(12), window=timedelta(hours=1))


def test_system_clock_uses_injected_sources_and_process_epoch() -> None:
    from crew_health.clock.adapters.system_clock import SystemClock

    clock = SystemClock(
        epoch="epoch_test", offset=lambda: timedelta(hours=1), utc_now=lambda: at(10), monotonic=lambda: 123
    )
    assert clock.now() == at(11)
    assert clock.monotonic_ns() == 123
    assert clock.process_epoch() == "epoch_test"


@pytest.mark.parametrize("template", ["Day {unknown}", "{day.__class__}", "{day!r}", "{day:03}", "{"])
def test_mission_label_template_rejects_unregistered_placeholders(template: str) -> None:
    with pytest.raises(ProblemError):
        met_label(at(0), MissionClockVersion("clock", 1, at(0), at(0), None, template))


def test_new_incarnation_retains_node_high_water(station) -> None:
    db, _, service, clock = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        prior = service.tick(uow)
    clock.at -= timedelta(hours=1)
    next_repository = SqlClockRepository(settings=db.settings, node_id="node_test", incarnation_id="inc_next")
    next_service = PersistentClocks(repository=next_repository, settings=db.settings, ids=db.ids)
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        current = next_service.tick(uow)
        assert current.physical_ms >= prior.physical_ms
        assert (current.physical_ms, current.logical) > (prior.physical_ms, prior.logical)
        assert current.incarnation_id == "inc_next"


def test_elapsed_never_mixes_live_sim_or_replay_partitions(station) -> None:
    db, _, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A"))
    with db.snapshot() as view:
        first = TimeInput(at(10), "source", "segment_A", 1, "source_raw", "LIVE")
        last = TimeInput(at(11), "source", "segment_A", 1, "source_raw", "SIM")
        assert service.elapsed(view, first, last) is None


def test_capture_revision_must_cover_the_raw_capture_time(station) -> None:
    db, _, service, _ = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        limited = ClockCorrection(
            "correction_1", "source", "segment_A", 1, at(10), at(11), 0, 0, at(12), "operator"
        )
        service.apply(uow, limited)
        service.apply(uow, correction(2, "segment_A"))
    with db.snapshot() as view:
        assert (
            service.normalize(
                view, TimeInput(at(11), "source", "segment_A", 1, "hub_corrected", "LIVE")
            ).state
            == "unknown"
        )


def test_other_source_correction_cannot_unfreeze_hub_regression(station) -> None:
    db, _, service, clock = station
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.check_regression(uow)
        service.open(uow, ClockSegment("source", "physical", "segment_A", None, 1, 1))
        service.apply(uow, correction(1, "segment_A"))
        service.complete_sweep(uow, correction_version=1)
    clock.at -= timedelta(hours=1)
    with db.begin(actor=Actor("operator", "operator_test"), correlation_id="correlation_test") as uow:
        service.check_regression(uow)
        service.open(uow, ClockSegment("other", "otherphysical", "segment_B", None, 1, 2))
        service.apply(uow, correction(2, "segment_B", source="other"))
        with pytest.raises(ProblemError):
            service.complete_sweep(uow, correction_version=2)
        assert not service.clearing_allowed(uow)
