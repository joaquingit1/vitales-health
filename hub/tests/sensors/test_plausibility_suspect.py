"""R-E23-2 asymmetry: a plausibility check catches a broken sensor and never hides a dangerous true reading.

Range and saturation (impossible values) stay rejected. A rate or stuck failure may be a real event (a leak, a fire,
a scrubber failure), so the reading is stored `suspect`: it may raise a warning, it never clears or lowers one."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from e23_helpers import sensor, utc
from e23_station import ADAPTER, START, Station, build_station, channel, reading
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.sensors.adapters.config_reader import SensorConfig
from crew_health.sensors.application.ports import SensorEvaluated, SensorStateQuery
from crew_health.sensors.domain.evaluation import evaluate_channel
from crew_health.sensors.domain.plausibility import (
    OUT_OF_RANGE,
    RATE_EXCEEDED,
    SATURATED,
    STUCK_VALUE,
    PlausibilityResult,
    assess,
)
from crew_health.sensors.domain.reading import ReadingFact

from .conftest import evaluation_input, point

pytestmark = [pytest.mark.e23, pytest.mark.trace("R-E23-2", "DE23-11", "T-E23-025", "AGENTS-2")]
PATH = "/v1/adapters/" + ADAPTER + "/readings"
EXAMPLES = settings(max_examples=200, deadline=None, suppress_health_check=list(HealthCheck))
STEP = 300  # the RR-1 cadence the channel declares
HOUR = 3600


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st_ = build_station(
        tmp_path,
        registry={
            "channels": [
                channel(
                    "ch_co2",
                    "co2",
                    "co2",
                    "SIM",
                    "mmHg",
                    expected_interval_seconds=STEP,
                    expected_interval_basis="RR-1 file cadence, N02",
                    stale_after_seconds=900,
                    stale_after_basis="three samples, team default",
                )
            ]
        },
    )
    st_.initialize("SIM")
    return st_


class Feed:
    """Posts readings of the one co2 channel at offsets (seconds) after START, with a running sequence."""

    def __init__(self, st_: Station) -> None:
        self.st, self.seq, self.elapsed = st_, 0, 0

    def post(self, points: list[tuple[int, float]]) -> None:
        newest = max(offset for offset, _ in points) + 5  # the clock stands just after the newest reading
        if newest > self.elapsed:
            self.st.advance(seconds=newest - self.elapsed)
            self.elapsed = newest
        rows = []
        for offset, value in points:
            self.seq += 1
            rows.append(reading("ch_co2", self.seq, value, "mmHg", START + timedelta(seconds=offset)))
        key = f"key_{self.seq:05d}"
        reply = self.st.client.post(
            PATH,
            json={"batch_id": f"bat_{self.seq}", "readings": rows},
            headers=self.st.headers("adapter", key),
        )
        assert reply.status_code == 200 and reply.json()["rejected"] == [], reply.text


def stored(st_: Station) -> list[tuple[int, str, str | None]]:
    connection = sqlite3.connect(st_.db.path)
    try:
        return connection.execute(
            "SELECT adapter_sequence, quality_status, provenance_trace FROM sensors_reading ORDER BY adapter_sequence"
        ).fetchall()
    finally:
        connection.close()


def last_results(st_: Station) -> SensorEvaluated:
    with st_.begin() as uow:
        found = st_.resolver().require(SensorStateQuery).last_results(uow, "ch_co2")
    assert found is not None
    return found


def act(found: SensorEvaluated) -> Any:
    return next(r for r in found.results if r.limit_id == "co2_1h_act")


def calm(count: int, first: int) -> list[tuple[int, float]]:
    """Alternating, plainly moving cabin values near 2 mmHg, one per cadence step, inside the rate bound."""
    return [(first + STEP * i, 2.0 if i % 2 else 2.001) for i in range(count)]


def test_a_true_fast_co2_rise_is_kept_suspect_and_raises_the_warning_with_the_flag(station: Station) -> None:
    feed = Feed(station)
    feed.post(calm(9, 100))  # 100 .. 2500 s
    feed.post([(2560, 10.0)])  # 2 to 10 mmHg inside a minute: a leak, a fire or a failed scrubber
    feed.post([(2860 + STEP * i, 10.0 + i / 1000) for i in range(4)])
    rows = stored(station)
    assert len(rows) == 14  # nothing is discarded
    assert rows[9] == (10, "uncertain", RATE_EXCEEDED)  # kept as suspect, not rejected
    assert all(q != "rejected" for _, q, _ in rows)
    found = last_results(station)
    assert found.suspect_reason == RATE_EXCEEDED
    outcome = act(found)
    assert outcome.crossed is True  # the dangerous true reading raises
    assert outcome.clear_holds is None  # and proves nothing about clearing
    shown = station.client.get("/v1/sensors/co2", headers=station.headers("crew_a")).json()
    assert shown["evaluation"]["level"] == "act"


def test_the_jump_reading_alone_is_evaluated_for_raising_not_dropped(station: Station) -> None:
    feed = Feed(station)
    feed.post(calm(3, 100))
    feed.post([(1000, 10.0)])
    found = last_results(station)
    assert found.suspect_reason == RATE_EXCEEDED and found.reading_id.startswith("rdg_")
    assert all(r.clear_holds is None for r in found.results)


def test_a_rate_failed_reading_never_clears_until_it_has_left_the_window(station: Station) -> None:
    feed = Feed(station)
    feed.post(calm(9, 100))
    feed.post([(2560, 10.0)])
    feed.post([(2860 + STEP * i, 10.0 + i / 1000) for i in range(4)])
    assert act(last_results(station)).crossed is True
    feed.post([(4100, 2.0)])  # the fall is as fast as the rise: also suspect
    assert stored(station)[-1][1:] == ("uncertain", RATE_EXCEEDED)
    start = 4100 + STEP
    for i in range(15):  # steady low readings: the hour fills below the clear value
        at = start + STEP * i
        feed.post([(at, 2.001 if i % 2 else 2.0)])
        found = last_results(station)
        if at - HOUR < 4100:  # the suspect fall is still inside the one-hour window
            assert act(found).clear_holds is None and act(found).suspect_reason == RATE_EXCEEDED
    # 4100 has left the one-hour window: the window is clean and may now prove the clear
    assert act(found).suspect_reason is None and act(found).clear_holds is True
    week = next(r for r in found.results if r.limit_id == "co2_7d_watch")
    assert week.clear_holds is None  # the seven-day window holds the suspect reading: nothing there clears


def test_a_stuck_channel_never_clears_and_keeps_a_raised_level_raised(station: Station) -> None:
    feed = Feed(station)
    feed.post([(100 + STEP * i, 4.0 + (i % 2) / 100) for i in range(13)])  # above the act value, moving
    assert act(last_results(station)).crossed is True
    feed.post([(100 + STEP * (13 + i), 4.0) for i in range(40)])  # then it freezes at one value
    flagged = [s for s, q, t in stored(station) if t == STUCK_VALUE]
    assert flagged and all(q == "uncertain" for s, q, t in stored(station) if t == STUCK_VALUE)
    found = last_results(station)
    assert found.suspect_reason == STUCK_VALUE
    assert act(found).crossed is True and act(found).clear_holds is None  # it stays raised, never clears


def test_a_stuck_channel_at_a_low_value_cannot_clear(station: Station) -> None:
    feed = Feed(station)
    feed.post(calm(13, 100))
    feed.post([(100 + STEP * (13 + i), 2.0) for i in range(40)])
    found = last_results(station)
    assert found.suspect_reason == STUCK_VALUE and act(found).clear_holds is None


@pytest.mark.parametrize(("value", "check"), [(800.0, OUT_OF_RANGE), (-1.0, OUT_OF_RANGE), (0.0, SATURATED)])
def test_range_and_saturation_stay_rejected_and_never_evaluate(
    station: Station, value: float, check: str
) -> None:
    feed = Feed(station)
    feed.post(calm(4, 100))
    feed.post([(1400, value)])
    assert stored(station)[-1] == (5, "rejected", check)
    found = last_results(station)
    assert found.suspect_reason is None  # the impossible value is not even a suspect evaluation


def test_the_adapters_own_rejected_status_is_never_upgraded_by_a_suspect_verdict(station: Station) -> None:
    feed = Feed(station)
    feed.post(calm(3, 100))
    feed.seq += 1
    row = reading(
        "ch_co2", feed.seq, 10.0, "mmHg", START + timedelta(seconds=1000), quality_status="rejected"
    )
    reply = station.client.post(
        PATH, json={"batch_id": "bat_rej", "readings": [row]}, headers=station.headers("adapter", "key_99999")
    )
    assert reply.status_code == 200
    assert stored(station)[-1][1] == "rejected"


def test_last_results_is_none_before_any_reading_and_for_an_unknown_channel_it_fails(
    station: Station,
) -> None:
    with station.begin() as uow:
        query = station.resolver().require(SensorStateQuery)
        assert query.last_results(uow, "ch_co2") is None
        with pytest.raises(Exception, match="unknown channel"):
            query.last_results(uow, "nope")


def test_last_results_reevaluates_from_stored_readings_and_matches_the_published_evaluation(
    station: Station,
) -> None:
    feed = Feed(station)
    feed.post(calm(13, 100))
    first = last_results(station)
    again = last_results(station)
    assert first == again and first.channel_id == "ch_co2" and first.suspect_reason is None
    assert act(first).limit_id == "co2_1h_act" and act(first).crossed is False
    assert first.origin_mode == "SIM" and first.freshness == "current"


# --- properties: the asymmetry -----------------------------------------------------------------------------


def _fact(at: datetime, value: str, seq: int) -> ReadingFact:
    return ReadingFact(f"rdg_{seq}", "ch", seq, at, at, "known", Decimal(value), "accepted", "SIM", "seg_1")


def _co2(config_kind: Any) -> Any:
    assert config_kind.plausibility is not None
    return config_kind.plausibility


@EXAMPLES
@given(
    base=st.integers(10, 5000),
    extra=st.integers(1, 700_000),
    seconds=st.integers(1, 3600),
    up=st.booleans(),
)
def test_property_a_rate_failure_is_suspect_never_rejected(
    config: SensorConfig, base: int, extra: int, seconds: int, up: bool
) -> None:
    kind = sensor(config, "co2")
    criteria = _co2(kind)
    limit = criteria.max_rate_per_s * seconds
    first = Decimal(base) / 100
    jump = first + (limit + Decimal(extra) / 10000) * (1 if up else -1)
    t0 = utc(2026, 1, 1)
    second = _fact(t0 + timedelta(seconds=seconds), str(jump.quantize(Decimal("0.0001"))), 2)
    result = assess(kind, second, [_fact(t0, str(first), 1)], history_bound=64)
    in_range = (
        criteria.minimum <= second.value <= criteria.maximum and second.value not in criteria.saturation
    )
    if in_range:
        assert result.status == "failed" and result.reason == RATE_EXCEEDED
        assert result.suspect and not result.rejected and result.raises and not result.evaluable
    else:
        assert result.rejected and not result.suspect and not result.raises


@EXAMPLES
@given(
    values=st.lists(st.integers(-200, 2000), min_size=2, max_size=12, unique=True),
    kind_of_failure=st.sampled_from(["passed", RATE_EXCEEDED, STUCK_VALUE]),
)
def test_property_suspect_evaluates_like_passed_for_raising_and_never_clears(
    config: SensorConfig, values: list[int], kind_of_failure: str
) -> None:
    base = utc(2026, 1, 1, 12)
    rows = [point(base + timedelta(seconds=STEP * i), str(Decimal(v) / 100)) for i, v in enumerate(values)]
    inp = evaluation_input(config, "co2", rows, interval=STEP)
    status = "passed" if kind_of_failure == "passed" else "failed"
    check = {RATE_EXCEEDED: "rate", STUCK_VALUE: "stuck"}.get(kind_of_failure)
    verdict = PlausibilityResult(status, "plausible" if status == "passed" else kind_of_failure, check)  # type: ignore[arg-type]
    plain = evaluate_channel(inp, PlausibilityResult("passed", "plausible", None))
    got = evaluate_channel(inp, verdict)
    assert [(r.limit_id, r.crossed) for r in got.results] == [(r.limit_id, r.crossed) for r in plain.results]
    assert got.level == plain.level  # raising is untouched
    if verdict.suspect:
        assert all(r.clear_holds is None for r in got.results) and got.suspect_reason == kind_of_failure
    else:
        assert got.suspect_reason is None and [r.clear_holds for r in got.results] == [
            r.clear_holds for r in plain.results
        ]


@pytest.mark.parametrize("reason", [OUT_OF_RANGE, SATURATED, "insufficient_history", "criteria_missing"])
def test_rejected_or_unproved_readings_evaluate_nothing(config: SensorConfig, reason: str) -> None:
    rows = [point(utc(2026, 1, 1, 12), "9")]
    inp = evaluation_input(config, "co2", rows, interval=STEP)
    status = "failed" if reason in (OUT_OF_RANGE, SATURATED) else "unavailable"
    got = evaluate_channel(inp, PlausibilityResult(status, reason, None))  # type: ignore[arg-type]
    assert got.level is None and all(r.crossed is None and r.clear_holds is None for r in got.results)


def test_a_suspect_reading_blocks_the_clear_only_of_the_windows_that_hold_it(config: SensorConfig) -> None:
    from dataclasses import replace

    base = utc(2026, 1, 1, 12)
    rows = [point(base + timedelta(seconds=STEP * i), "2") for i in range(25)]  # two hours, all low
    inp = evaluation_input(config, "co2", rows, interval=STEP)
    passed = PlausibilityResult("passed", "plausible", None)
    end = base + timedelta(seconds=STEP * 24)
    early = replace(inp, suspects=((base + timedelta(seconds=STEP * 2), RATE_EXCEEDED),))
    late = replace(inp, suspects=((end - timedelta(seconds=STEP * 2), STUCK_VALUE),))
    one_hour = lambda e: next(r for r in e.results if r.limit_id == "co2_1h_act")  # noqa: E731
    assert one_hour(evaluate_channel(inp, passed)).clear_holds is True
    assert one_hour(evaluate_channel(early, passed)).clear_holds is True  # it left the hour a long time ago
    blocked = one_hour(evaluate_channel(late, passed))
    assert blocked.clear_holds is None and blocked.suspect_reason == STUCK_VALUE
