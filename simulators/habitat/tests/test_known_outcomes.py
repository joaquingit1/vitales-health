"""Replay known outcomes (AGENTS section 4): documented solar particle events through the real feed path.

These tests validate the radiation model and the replay path against what NASA documented for the
September 2017 event. They are model validation, not diagnosis: no crew dose and no crew action is
inferred. Each reading goes through the feeder as a contract-valid batch, and the checks run on what
the hub received, not on the CSV.

Expected values are read from the case files under docs/product/nasa-data/cases/ (outcome.json), which
quote N40 (Berger et al. 2018) and N39 (JPL release 2017-254), or are typed below with their location.
"""

import csv
import json
import random
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.feeder import Feeder, FeederSettings, HttpResponse, SequenceStore
from crew_health_sim_habitat.replay import ReplayCounts, dataset_rows, to_wire, verify_dataset

from conftest import REPO_ROOT, Validate

CASES = REPO_ROOT / "docs" / "product" / "nasa-data" / "cases"
ISS_CASE = CASES / "spe-2017-09-iss-columbus" / "outcome.json"
MARS_CASE = CASES / "spe-2017-09-mars-surface" / "outcome.json"
# N40 Section 3.3, PDF page 9 (quoted in ISS_CASE): the event began on 10 Sep 2017 at 16:06 UTC and the
# first SPE protons were seen at the ISS on 11 Sep at 04:30.
EVENT_START = datetime(2017, 9, 10, 16, 6, tzinfo=UTC)
ISS_ONSET = datetime(2017, 9, 11, 4, 30, tzinfo=UTC)
# ISS_CASE replay_checks_derived_by_curator.note states the case method: dose = rate x interval to the
# next row, intervals over 300 s skipped; "high latitude" = absolute latitude at least 45 deg. N40 Section
# 3.3 takes 10 September as the quiet day; the quiet baseline is its high-latitude readings before the event
# start, the same baseline the SPE twin's shape is measured against (config/scenarios.json).
MAX_DOSE_INTERVAL_S = 300.0
HIGH_LATITUDE_DEG = 45.0
QUIET_DAY_START = datetime(2017, 9, 10, tzinfo=UTC)
# MARS_CASE: pre-event rows are those before 2017-09-10T12:00; the release (N39 paragraphs 4 and 5) says
# the surface levels were "more than double"; the curator bounds the peak to 2017-09-10 to 2017-09-13.
MARS_PRE_EVENT_END = datetime(2017, 9, 10, 12, 0, tzinfo=UTC)
MARS_PEAK_WINDOW = (datetime(2017, 9, 10, tzinfo=UTC), datetime(2017, 9, 14, tzinfo=UTC))
MARS_DOUBLING = 2.0


@dataclass
class RecordingHub:
    """In-memory hub: checks every batch against the E23 request schema and accepts it."""

    validate_batch: Validate
    readings: list[dict[str, object]] = field(default_factory=list)

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        batch = json.loads(body.decode("utf-8"))
        self.validate_batch(batch)
        received = batch["readings"]
        self.readings.extend(received)
        result = {"batch_id": batch["batch_id"], "accepted": len(received), "duplicates": 0, "rejected": []}
        return HttpResponse(200, json.dumps(result).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Received:
    observed_at: datetime
    value: float
    unit: str


def _replay_to_hub(loaded: Loaded, feed_id: str, validate_batch: Validate) -> list[Received]:
    """Run a replay feed through the feeder, as the CLI does, and return what the hub received."""
    feed = loaded.replay_feed(feed_id)
    hub = RecordingHub(validate_batch)
    feeder = Feeder(
        hub_url="http://hub.invalid",
        adapter_id=loaded.wiring.adapters["REPLAY"].adapter_id,
        token="test-token",
        settings=FeederSettings(),
        transport=hub,
        now=lambda: 0.0,
        rng=random.Random(0),
        sequences=SequenceStore(None),
    )
    counts = ReplayCounts()
    batch = feeder.settings.batch_max_readings
    for row in dataset_rows(REPO_ROOT, loaded.wiring.datasets[feed.dataset], feed, counts):
        assert feeder.offer(to_wire(row, feed, loaded.wiring.quality_status["ok"]))
        if feeder.pending >= batch:
            assert feeder.pump(), "the hub did not accept a batch"
    for _ in range(feeder.pending // batch + 1):
        if feeder.pending:
            assert feeder.pump(), "the hub did not accept a batch"
    assert feeder.pending == 0
    assert counts.rows_rejected == 0
    assert all(r["channel_id"] == feed.channel_id for r in hub.readings)
    return [
        Received(datetime.fromisoformat(str(r["observed_at"])), float(str(r["value"])), str(r["unit"]))
        for r in hub.readings
    ]


def _daily_dose_ugy(readings: list[Received]) -> dict[str, float]:
    """Dose per UTC day: rate x interval to the next reading, longer gaps skipped (curator's method)."""
    days: dict[str, float] = defaultdict(float)
    for here, after in pairwise(readings):
        interval_s = (after.observed_at - here.observed_at).total_seconds()
        if 0.0 < interval_s <= MAX_DOSE_INTERVAL_S:
            days[here.observed_at.date().isoformat()] += here.value * interval_s / 3600.0
    return dict(days)


def _latitudes(loaded: Loaded, feed_id: str) -> dict[datetime, float]:
    """Latitude of each replayed row, read from the same hashed file (the wire carries no position)."""
    feed = loaded.replay_feed(feed_id)
    path = verify_dataset(REPO_ROOT, loaded.wiring.datasets[feed.dataset])
    out: dict[datetime, float] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if feed.filter_column is not None and row[feed.filter_column] != feed.filter_equals:
                continue
            moment = datetime.strptime(row[feed.time_column], feed.time_format).replace(tzinfo=UTC)
            assert moment not in out, "the latitude join needs one row per timestamp"
            out[moment] = float(row["latitude"])
    return out


def _case(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def iss_received(loaded: Loaded, validate_batch: Validate) -> list[Received]:
    return _replay_to_hub(loaded, "dostel2_spe_2017", validate_batch)


def test_iss_replay_reaches_the_hub_as_recorded(loaded: Loaded, iss_received: list[Received]) -> None:
    feed = loaded.replay_feed("dostel2_spe_2017")
    assert len(iss_received) == len(_latitudes(loaded, "dostel2_spe_2017"))
    assert {r.unit for r in iss_received} == {feed.unit}
    assert iss_received == sorted(iss_received, key=lambda r: r.observed_at)


def test_iss_event_day_has_the_highest_dose_in_the_order_of_table_4(iss_received: list[Received]) -> None:
    table = _case(ISS_CASE)["documented_table"]
    assert isinstance(table, dict)
    documented = {row["date"]: row["DOSTEL-2"] for row in table["rows"] if row["date"].startswith("2017-")}
    assert max(documented, key=documented.__getitem__) == "2017-09-11"
    daily = _daily_dose_ugy(iss_received)
    # Table 4 removes the SAA and this file keeps it, so only the direction is compared (curator's note).
    assert max(daily, key=daily.__getitem__) == "2017-09-11"
    assert daily["2017-09-11"] > daily["2017-09-10"]
    assert daily["2017-09-11"] > daily["2017-09-12"]


@dataclass(frozen=True, slots=True)
class Pass:
    start: datetime
    end: datetime
    southern: bool


def _high_latitude_passes(readings: list[Received], latitude: dict[datetime, float]) -> list[Pass]:
    """Runs of consecutive readings at high latitude in one hemisphere, from the file's latitude track."""
    passes: list[Pass] = []
    run: list[Received] = []
    for reading in readings:
        lat = latitude[reading.observed_at]
        high = abs(lat) >= HIGH_LATITUDE_DEG
        same = bool(run) and (latitude[run[-1].observed_at] < 0) == (lat < 0)
        close = (
            bool(run) and (reading.observed_at - run[-1].observed_at).total_seconds() <= MAX_DOSE_INTERVAL_S
        )
        if high and (not run or (same and close)):
            run.append(reading)
            continue
        if run:
            passes.append(Pass(run[0].observed_at, run[-1].observed_at, latitude[run[0].observed_at] < 0))
        run = [reading] if high else []
    if run:
        passes.append(Pass(run[0].observed_at, run[-1].observed_at, latitude[run[0].observed_at] < 0))
    return passes


def _first_flag(
    readings: list[Received], latitude: dict[datetime, float], baseline: tuple[datetime, datetime]
) -> Received | None:
    """Test-side detector: the first high-latitude reading from the event start on that is above every
    high-latitude reading of the baseline window."""
    high = [r for r in readings if abs(latitude[r.observed_at]) >= HIGH_LATITUDE_DEG]
    threshold = max(r.value for r in high if baseline[0] <= r.observed_at < baseline[1])
    return next((r for r in high if r.observed_at >= EVENT_START and r.value > threshold), None)


def _first_spe_pass(readings: list[Received], latitude: dict[datetime, float]) -> Pass:
    """The high-latitude pass under way at the documented first proton arrival (N40: 11 Sep 04:30)."""
    passes = [p for p in _high_latitude_passes(readings, latitude) if p.start <= ISS_ONSET <= p.end]
    assert len(passes) == 1, "the documented onset falls in no high-latitude pass of the file"
    return passes[0]


def test_replay_data_shows_the_elevation_in_the_first_spe_pass(
    loaded: Loaded, iss_received: list[Received]
) -> None:
    """Replay data check with a test-side detector, not the product's evaluation (E23 is not on main).

    N40 Section 3.3: the ISS was in safe orbits until 11 Sep 04:30, when the first SPE protons were seen at
    high southern latitudes. The search starts at the event start (10 Sep 16:06), so a flag on the evening of
    10 Sep or on an earlier pass of 11 Sep fails, and the flag must fall inside the pass the latitude track
    shows under way at 04:30.
    """
    latitude = _latitudes(loaded, "dostel2_spe_2017")
    first_pass = _first_spe_pass(iss_received, latitude)
    assert first_pass.southern
    flag = _first_flag(iss_received, latitude, (QUIET_DAY_START, EVENT_START))
    assert flag is not None, "the documented elevation was not seen"
    assert first_pass.start <= flag.observed_at <= first_pass.end


def test_a_lower_baseline_day_would_fail_the_onset_check(
    loaded: Loaded, iss_received: list[Received]
) -> None:
    """Negative check: 8 Sep has a lower high-latitude maximum than the 10 Sep quiet baseline, so with 8 Sep
    as the baseline the same detector flags an ordinary pass before the first SPE pass (02:50 on 11 Sep, in
    safe orbits per N40), which the check above rejects. The onset check can therefore fail."""
    latitude = _latitudes(loaded, "dostel2_spe_2017")
    first_pass = _first_spe_pass(iss_received, latitude)
    day = timedelta(days=1)
    flag = _first_flag(iss_received, latitude, (QUIET_DAY_START - 2 * day, QUIET_DAY_START - day))
    assert flag is not None
    assert flag.observed_at < first_pass.start


@pytest.mark.skip(
    reason="pending E23 evaluation and E14 baselines on main: radiation limits are threshold_pending"
)
def test_product_raises_the_radiation_watch_from_the_first_spe_pass() -> None:
    """ISS_CASE expected_product_output: watch from the first high-latitude passes after 11 Sep 04:30, no
    emergency, no watch from quiet-day SAA passes. Runs once the hub evaluates replayed radiation readings."""
    raise NotImplementedError


def test_iss_saa_peak_on_a_quiet_day_is_outside_the_high_latitude_view(
    loaded: Loaded, iss_received: list[Received]
) -> None:
    """ISS_CASE must_not: the file's highest rate (an SAA pass on 6 Sep) must not read as the event."""
    latitude = _latitudes(loaded, "dostel2_spe_2017")
    peak = max(iss_received, key=lambda r: r.value)
    assert peak.observed_at < EVENT_START
    assert abs(latitude[peak.observed_at]) < HIGH_LATITUDE_DEG


def test_mars_surface_rate_more_than_doubles_during_the_event(
    loaded: Loaded, validate_batch: Validate
) -> None:
    """N39: surface levels "more than double"; checked as magnitude, not clock time (MARS_CASE caveat)."""
    received = _replay_to_hub(loaded, "msl_rad_spe_2017", validate_batch)
    pre = [r.value for r in received if r.observed_at < MARS_PRE_EVENT_END]
    pre_mean = sum(pre) / len(pre)
    start, end = MARS_PEAK_WINDOW
    peak = max(received, key=lambda r: r.value)
    assert start <= peak.observed_at < end
    assert peak.value >= MARS_DOUBLING * pre_mean
    assert max(pre) < MARS_DOUBLING * pre_mean
    doubled = [r.observed_at for r in received if r.value >= MARS_DOUBLING * pre_mean]
    assert all(start <= moment < end for moment in doubled)
