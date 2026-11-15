"""Replay fidelity: replayed readings equal the NASA CSV rows, with their original times and provenance."""

import csv
import json
import random
import shutil
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.errors import DatasetError, HubUnavailableError
from crew_health_sim_habitat.feeder import Feeder, FeederSettings, HttpResponse, SequenceStore
from crew_health_sim_habitat.replay import (
    ReplayCounts,
    ReplayCursor,
    dataset_rows,
    feed_replay,
    read_rows,
    to_wire,
    verify_dataset,
)
from crew_health_sim_habitat.units import MMHG_PER_KPA
from crew_health_sim_habitat.wiring import Dataset

from conftest import REPO_ROOT

# NASA-SOURCES.md section 2: RR-1 has 12,087 rows; the last hour before 2014-10-07 15:36 averages
# 4,606 ppm, which is 3.50 mmHg at the assumed 760 mmHg.
RR1_ROWS = 12087
DEMO_STOP = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)
DEMO_HOUR_MEAN_PPM, DEMO_HOUR_MMHG = 4606.0, 3.50
DOSTEL1_ROWS = 84


def _csv(path: Path, columns: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, fieldnames=list(columns) if columns is not None else None))


def _provenance_rows(dataset: Dataset) -> int:
    data = json.loads((REPO_ROOT / dataset.provenance).read_text(encoding="utf-8"))
    rows = data["rows"]
    assert isinstance(rows, int)
    return rows


REPLAY_FEEDS = [
    "rr1_co2",
    "rr1_temperature",
    "rr1_humidity",
    "dostel1_rad",
    "rr5_co2",
    "rr5_temperature",
    "rr5_humidity",
    "dostel2_spe_2017",
    "tepc_spe_2017",
    "msl_rad_spe_2017",
]


@pytest.mark.parametrize("feed_id", REPLAY_FEEDS)
def test_replayed_values_equal_the_csv_rows(loaded: Loaded, feed_id: str) -> None:
    feed = loaded.replay_feed(feed_id)
    dataset = loaded.wiring.datasets[feed.dataset]
    source = _csv(verify_dataset(REPO_ROOT, dataset), dataset.columns)
    counts = ReplayCounts()
    rows = list(dataset_rows(REPO_ROOT, dataset, feed, counts))
    assert counts.rows_rejected == 0
    for row in rows:
        original = source[row.row_number - 1]
        assert row.quantity.value == float(original[feed.value_column])
        assert row.quantity.unit == feed.unit
        assert row.observed_at.replace(tzinfo=None) == datetime.strptime(
            original[feed.time_column], feed.time_format
        )
    assert len(source) == _provenance_rows(dataset)
    kept = [r for r in source if feed.filter_column is None or r[feed.filter_column] == feed.filter_equals]
    assert len(rows) == len(kept) > 0
    if feed.dataset == "rr1":
        assert len(rows) == RR1_ROWS
    if feed.dataset == "dostel":
        assert len(rows) == DOSTEL1_ROWS


def test_tepc_sees_the_event_day_too(loaded: Loaded) -> None:
    """The independent TEPC (N30, one dose per 1 min interval, UTC) sums highest on 11 Sep 2017, as
    DOSTEL-2 does in N40 Table 4. Not sent to the hub: the catalog has no uGy/min unit for radiation."""
    feed = loaded.replay_feed("tepc_spe_2017")
    assert feed.channel_id is None
    days: dict[str, float] = {}
    for row in dataset_rows(REPO_ROOT, loaded.wiring.datasets[feed.dataset], feed, ReplayCounts()):
        day = row.observed_at.date().isoformat()
        days[day] = days.get(day, 0.0) + row.quantity.value
    assert max(days, key=days.__getitem__) == "2017-09-11"


def test_replay_readings_carry_their_provenance(loaded: Loaded) -> None:
    feed = loaded.replay_feed("rr1_co2")
    path = verify_dataset(REPO_ROOT, loaded.wiring.datasets[feed.dataset])
    row = next(read_rows(path, feed, ReplayCounts()))
    wire = to_wire(row, feed, "accepted")
    assert wire.origin_mode == "REPLAY"
    assert wire.observed_at == row.observed_at
    assert wire.replay_dataset == feed.dataset_label
    assert wire.cabin_pressure_basis == "assumed"
    assert wire.cabin_pressure_mmhg == 760.0


def test_demo_stop_hour_matches_nasa_sources(loaded: Loaded) -> None:
    feed = loaded.replay_feed("rr1_co2")
    path = verify_dataset(REPO_ROOT, loaded.wiring.datasets[feed.dataset])
    hour = [
        r.quantity.value
        for r in read_rows(path, feed, ReplayCounts())
        if DEMO_STOP.timestamp() - 3600 < r.observed_at.timestamp() <= DEMO_STOP.timestamp()
    ]
    assert len(hour) == 12
    mean = sum(hour) / len(hour)
    assert round(mean) == DEMO_HOUR_MEAN_PPM
    assert round(mean * 760.0 / 1e6, 2) == DEMO_HOUR_MMHG
    assert pytest.approx(101.325) == 760.0 / MMHG_PER_KPA


def test_a_changed_file_is_refused(loaded: Loaded, tmp_path: Path) -> None:
    dataset = loaded.wiring.datasets["dostel"]
    copy = tmp_path / dataset.path
    copy.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / dataset.path, copy)
    with copy.open("a", encoding="utf-8") as handle:
        handle.write('"2022-04-02T01:06:00","DosTel1",1.0,"DosTel1","DosTel","Columbus","ISS"\n')
    with pytest.raises(DatasetError):
        verify_dataset(tmp_path, dataset)


def test_an_unparseable_row_is_counted_never_substituted(loaded: Loaded, tmp_path: Path) -> None:
    feed = replace(loaded.replay_feed("dostel1_rad"))
    path = tmp_path / "rad.csv"
    path.write_text(
        "timestamp,instrument_id,absorbed_dose_rate\n"
        '"2022-04-01T23:00:00","DosTel1",9.8\n'
        '"2022-04-01T23:00:33","DosTel1",\n'
        '"not a time","DosTel1",9.9\n'
        '"2022-04-01T23:01:40","DosTel1",nan\n'
        '"2022-04-01T23:02:13","DosTel1",9.7\n',
        encoding="utf-8",
    )
    counts = ReplayCounts()
    rows = list(read_rows(path, feed, counts))
    assert [r.quantity.value for r in rows] == [9.8, 9.7]
    assert [r.row_number for r in rows] == [1, 5]
    assert counts.rows_rejected == 3


class FlakyHub:
    """In-memory hub: accepts the first `accept` batches, then answers 503 (or accepts all with None)."""

    def __init__(self, accept: int | None) -> None:
        self.accept = accept
        self.observed: list[str] = []

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        if self.accept is not None and self.accept <= 0:
            return HttpResponse(503, b"{}")
        if self.accept is not None:
            self.accept -= 1
        batch = json.loads(body.decode("utf-8"))
        self.observed.extend(str(r["observed_at"]) for r in batch["readings"])
        result = {"batch_id": batch["batch_id"], "accepted": len(batch["readings"]), "duplicates": 0}
        return HttpResponse(200, json.dumps(result).encode("utf-8"))


class SteppedClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _replay_feeder(hub: FlakyHub, clock: SteppedClock, settings: FeederSettings) -> Feeder:
    return Feeder(
        hub_url="http://hub.invalid",
        adapter_id="adp_test_replay",
        token="test-token",
        settings=settings,
        transport=hub,
        now=clock.now,
        rng=random.Random(0),
        sequences=SequenceStore(None),
    )


def test_a_restarted_replay_resumes_after_the_rows_the_hub_accepted(loaded: Loaded, tmp_path: Path) -> None:
    feed = loaded.replay_feed("rr1_co2")
    dataset = loaded.wiring.datasets[feed.dataset]
    settings = FeederSettings(batch_max_readings=500, outage_max_s=60.0)
    first, clock = FlakyHub(accept=3), SteppedClock()
    cursor = (ReplayCursor(tmp_path / "cursor.json"), ReplayCursor.key(feed, dataset))
    rows = dataset_rows(REPO_ROOT, dataset, feed, ReplayCounts())
    with pytest.raises(HubUnavailableError):
        feed_replay(rows, feed, "accepted", _replay_feeder(first, clock, settings), cursor, clock.sleep)
    assert len(first.observed) == 3 * 500

    second = FlakyHub(accept=None)
    restarted = (ReplayCursor(tmp_path / "cursor.json"), cursor[1])
    rows = dataset_rows(REPO_ROOT, dataset, feed, ReplayCounts())
    run = feed_replay(rows, feed, "accepted", _replay_feeder(second, clock, settings), restarted, clock.sleep)
    assert run.resumed_after == 3 * 500
    sent = first.observed + second.observed
    assert len(sent) == len(set(sent)) == RR1_ROWS


def test_a_full_queue_with_the_hub_down_fails_within_its_bound(loaded: Loaded) -> None:
    feed = loaded.replay_feed("rr1_co2")
    dataset = loaded.wiring.datasets[feed.dataset]
    settings = FeederSettings(
        queue_max_readings=10, batch_max_readings=10, outage_max_s=7 * 86400.0, wait_polls_max=50
    )
    clock = SteppedClock()
    feeder = _replay_feeder(FlakyHub(accept=0), clock, settings)
    rows = dataset_rows(REPO_ROOT, dataset, feed, ReplayCounts())
    with pytest.raises(HubUnavailableError, match="stayed full"):
        feed_replay(rows, feed, "accepted", feeder, (ReplayCursor(None), "k"), clock.sleep)
