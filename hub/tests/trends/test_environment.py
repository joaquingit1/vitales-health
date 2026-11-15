"""Habitat history keeps its dates and never represents personal exposure."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.application.environment import TrendsEnvironment
from crew_health.trends.application.ports import (
    EnvironmentSeries,
    SeriesPoint,
    SeriesSlice,
    Unavailable,
)
from crew_health.trends.settings import TrendsSettings

from .support import NOW, SensorReader, catalog_release, person_session, settings


@dataclass(frozen=True)
class Snapshot:
    release: ReleaseHandle
    now: datetime

    @property
    def connection(self) -> StorageSession:
        raise RuntimeError("environment context must not access storage")


def view() -> Snapshot:
    return Snapshot(catalog_release(), NOW)


@pytest.mark.trace("E14-02", "V10", "V72", "D01", "D16")
def test_replay_keeps_original_dataset_date_and_explicit_place_context() -> None:
    original = datetime(2012, 3, 4, tzinfo=UTC)
    point = SeriesPoint(original, Quantity(Decimal("0.4"), Unit("kPa")), "accepted", "REPLAY")
    reader = SensorReader(SeriesSlice((point,), False))
    result = TrendsEnvironment(reader, TrendsSettings.read(settings())).series(
        view(), person_session(), "channel_1", until=NOW
    )
    assert isinstance(result, EnvironmentSeries)
    assert result.points[0].observed_at == original
    assert result.points[0].origin_mode == "REPLAY"
    assert result.origin_mode == "REPLAY"
    assert result.personal_exposure is False
    assert result.place_context_only is True
    assert result.original_dates_kept is True
    assert result.pressure_basis == Unavailable("E23", "pressure_basis_not_provided")
    assert reader.limit == settings().integer("trends.environment_points_max")


@pytest.mark.trace("E14-02", "V10", "V72", "D01", "D16")
def test_nonaccepted_points_are_removed_and_mixed_origins_stay_visible() -> None:
    quantity = Quantity(Decimal("1"), Unit("1"))
    points = (
        SeriesPoint(NOW, quantity, "accepted", "LIVE"),
        SeriesPoint(NOW, quantity, "rejected", "SIM"),
        SeriesPoint(NOW, quantity, "uncertain", "REPLAY"),
        SeriesPoint(NOW, quantity, "not_assessed", "LIVE"),
        SeriesPoint(NOW, quantity, "accepted", "REPLAY"),
    )
    result = TrendsEnvironment(
        SensorReader(SeriesSlice(points, False)), TrendsSettings.read(settings())
    ).series(view(), person_session(kind="station"), "channel_1", until=NOW)
    assert isinstance(result, EnvironmentSeries)
    assert result.origin_mode == "mixed"
    assert tuple(point.origin_mode for point in result.points) == ("LIVE", "REPLAY")
    assert all(point.quality_status == "accepted" for point in result.points)
    assert result.personal_exposure is False


@pytest.mark.trace("E14-02", "V10", "V72", "D16")
def test_truncated_slice_and_absent_sensor_are_typed_unavailable() -> None:
    configured = TrendsSettings.read(settings())
    result = TrendsEnvironment(SensorReader(SeriesSlice((), True)), configured).series(
        view(), person_session(), "channel_1", until=NOW
    )
    assert result == Unavailable("trends", "series_truncated")
    assert TrendsEnvironment(None, configured).series(
        view(), person_session(), "channel_1", until=NOW
    ) == Unavailable("E23", "sensor_series_unbound")


@pytest.mark.trace("E14-02", "V25", "D16")
def test_assistant_cannot_read_habitat_context() -> None:
    with pytest.raises(ProblemError) as failure:
        TrendsEnvironment(None, TrendsSettings.read(settings())).series(
            view(), person_session(kind="assistant"), "channel_1", until=NOW
        )
    assert failure.value.code == ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("E14-02", "V25", "AGENTS-3.7")
@pytest.mark.parametrize("channel", ["", "bad channel", "x" * 300])
def test_channel_id_is_parsed_at_the_boundary(channel: str) -> None:
    """m9 regression: a malformed channel id is refused before any provider sees it."""
    reader = SensorReader(SeriesSlice((), False))
    with pytest.raises(ProblemError) as failure:
        TrendsEnvironment(reader, TrendsSettings.read(settings())).series(
            view(), person_session(), channel, until=NOW
        )
    assert failure.value.code == ErrorCode.INVALID_VALUE
    assert reader.limit is None


@pytest.mark.trace("E14-02", "V25")
def test_shared_station_screen_reads_habitat_context_as_contracts_allow() -> None:
    """m9: CONTRACTS 1086 lets the station session read habitat views; this is a decision, not an oversight."""
    reader = SensorReader(SeriesSlice((), False))
    result = TrendsEnvironment(reader, TrendsSettings.read(settings())).series(
        view(), person_session(kind="station"), "channel_1", until=NOW
    )
    assert (
        isinstance(result, EnvironmentSeries)
        and result.channel_id == "channel_1"
        and result.place_context_only
    )
