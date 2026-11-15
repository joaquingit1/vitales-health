"""E14-02: the E23 SensorReader fake replays actual NASA CO2 rows as place context.

The E15 EpisodeReader fake supplies empty periods. Neither fake replaces trends.
"""

import csv
from datetime import datetime
from decimal import Decimal
from itertools import islice
from pathlib import Path

import pytest
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.application.ports import (
    BaselineSummary,
    EnvironmentSeries,
    IndicatorUnavailable,
    SeriesPoint,
    SeriesSlice,
    SummaryRequest,
    Unavailable,
)

from .support import NOW, ROOT, ScenarioHub, SensorReader, person_session

pytestmark = pytest.mark.trace("E14-02", "R12", "R26", "V10", "V72", "D01", "D16")


def nasa_sensor() -> SensorReader:
    """E23 fake, with eight original RR-1 ISS timestamps and CO2 values in ppm."""
    path = ROOT / "docs/product/nasa-data/habitat-environment/eda_rr1_telemetry.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == [
            "time",
            "temperature_iss",
            "rh_iss",
            "co2_iss",
            "temperature_ground",
            "rh_ground",
            "co2_ground",
        ]
        points = tuple(
            SeriesPoint(
                datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S"),
                Quantity(Decimal(row["co2_iss"]), Unit("ppm")),
                "accepted",
                "REPLAY",
            )
            for row in islice(reader, 8)
        )
    assert len(points) == 8
    return SensorReader(SeriesSlice(points, False))


def test_nasa_habitat_replay_beside_live_person_history(tmp_path: Path) -> None:
    sensor = nasa_sensor()
    hub = ScenarioHub(tmp_path, sensor=sensor)
    live_ids = tuple(
        hub.post(day, value, origin="LIVE")
        for day, value in (
            (6, 10),
            (8, 20),
            (10, 30),
            (12, 40),
            (13, 50),
            (14, 45),
        )
    )
    live = hub.read(origin="LIVE")
    assert isinstance(live.usual, BaselineSummary)
    assert live.usual.value.value == 30
    assert live.today is not None
    assert live.today.observation_id == live_ids[-1]
    assert live.origin_mode == "LIVE"
    # Person replay uses the synthetic records profile, independently of the habitat dataset.
    hub.post(14, 50, origin="REPLAY")
    with hub.station.db.snapshot() as snapshot:
        habitat = hub.environment.series(
            snapshot,
            person_session(kind="station"),
            "co2_iss",
            until=NOW,
        )
        (replay,) = hub.summary.summaries(
            snapshot,
            person_session(),
            SummaryRequest("person", "mission", "REPLAY", ("pulse",)),
        )
    assert isinstance(habitat, EnvironmentSeries)
    assert habitat.origin_mode == "REPLAY"
    assert habitat.original_dates_kept
    # eda_rr1_provenance.json explicitly says the source timezone is unknown.
    assert all(point.observed_at.tzinfo is None for point in habitat.points)
    assert habitat.personal_exposure is False
    assert habitat.place_context_only is True
    assert tuple(point.observed_at for point in habitat.points) == tuple(
        point.observed_at for point in sensor.value.points
    )
    assert tuple(point.value for point in habitat.points) == tuple(
        point.value for point in sensor.value.points
    )
    assert all(point.observed_at.year == 2014 and point.value.unit.code == "ppm" for point in habitat.points)
    assert replay == IndicatorUnavailable(
        "pulse",
        "REPLAY",
        Unavailable("clock", "replay_dataset_clock_unbound"),
    )
    assert hub.read(origin="LIVE") == live
    with hub.station.db.snapshot() as snapshot, pytest.raises(ProblemError) as denied:
        hub.environment.series(snapshot, person_session(kind="assistant"), "co2_iss", until=NOW)
    assert denied.value.code is ErrorCode.UNAUTHORIZED_SCOPE
