"""R-E23-2 on the real stack: the RR-1 CO2 replay and one injected fault per check (T-E23-025, 029, 070).

LIVE, REPLAY and SIM share the one ingest path; the faults run on a SIM channel and the replay on a REPLAY channel
to show it. Expected outcomes come from the NASA file read independently, not from the criteria under test."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_helpers import read_rr1_ppm
from e23_station import ADAPTER, START, Station, build_station, channel, reading

pytestmark = [pytest.mark.e23, pytest.mark.trace("T-E23-025", "T-E23-029", "T-E23-070", "R-E23-2")]
PATH = "/v1/adapters/" + ADAPTER + "/readings"
PRESSURE = {"value": 760, "basis": "assumed"}


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(
        tmp_path,
        registry={
            "channels": [
                channel("ch_co2_sim", "co2", "co2", "SIM", "mmHg"),
                channel(
                    "ch_co2_rr1",
                    "co2",
                    "co2",
                    "REPLAY",
                    "ppm",
                    expected_interval_seconds=300,
                    expected_interval_basis="RR-1 file cadence, N02",
                    stale_after_seconds=900,
                    stale_after_basis="three samples, team default",
                ),
                channel(
                    "ch_rad",
                    "rad",
                    "radiation",
                    "SIM",
                    "µGy/h",
                    expected_interval_seconds=None,
                    stale_after_seconds=600,
                ),
            ]
        },
    )
    st.initialize("SIM")
    st.advance(seconds=60)
    return st


def post(st: Station, rows: list[Any], key: str = "key_00001", batch: str = "bat_1") -> Any:
    reply = st.client.post(
        PATH, json={"batch_id": batch, "readings": rows}, headers=st.headers("adapter", key)
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["rejected"] == [], reply.json()
    return reply


def stored(st: Station, channel_id: str) -> list[tuple[int, str, str | None]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(
            "SELECT adapter_sequence, quality_status, provenance_trace FROM sensors_reading "
            "WHERE channel_id = ? ORDER BY adapter_sequence",
            (channel_id,),
        ).fetchall()
    finally:
        connection.close()


def longest_run_start(values: list[Any]) -> int:
    best = (0, 0)
    begin = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[begin]:
            best = max(best, (i - begin, begin))
            begin = i
    return best[1]


def sim(st: Station, seq: int, value: float, seconds_ago: int) -> dict[str, Any]:
    return reading("ch_co2_sim", seq, value, "mmHg", START - timedelta(seconds=seconds_ago))


def test_the_rr1_co2_replay_is_assessed_and_the_held_stretch_is_kept_suspect_not_discarded(
    station: Station,
) -> None:
    data = read_rr1_ppm()
    start = longest_run_start([r.value for r in data])
    assert (
        data[start].at.isoformat().startswith("2014-09-21T04:24")
    )  # the file's held stretch, found independently
    count = start + 60
    rows = [
        reading("ch_co2_rr1", i + 1, float(r.value), "ppm", r.at, "REPLAY", cabin_pressure_mmhg=PRESSURE)
        for i, r in enumerate(data[:count])
    ]
    for offset in range(0, len(rows), 200):
        post(station, rows[offset : offset + 200], key=f"key_{offset:05d}", batch=f"bat_{offset}")
    found = stored(station, "ch_co2_rr1")
    assert len(found) == count  # nothing discarded
    assert found[0][2] == "insufficient_history"  # the first reading has no earlier one: never a pass
    assert all(q == "accepted" and t in (None, "insufficient_history") for _, q, t in found[: start + 37])
    assert not [s for s, q, _ in found if q == "rejected"]  # a stuck stretch is suspect, never rejected
    suspect = [(s, t) for s, q, t in found if q == "uncertain"]
    assert suspect == [
        (i + 1, "stuck_value") for i in range(start + 37, count)
    ]  # 38th identical sample onward
    assert sum(1 for _, q, t in found if q == "accepted" and t is None) > 100  # readings now evaluate
    assert not any(t == "criteria_missing" for _, _, t in found)


@pytest.mark.parametrize(
    ("value", "check"),
    [(3.0, None), (800.0, "out_of_range"), (-1.0, "out_of_range"), (0.0, "saturated")],
)
def test_range_and_saturation_faults_are_stored_rejected_with_their_check(
    station: Station, value: float, check: str | None
) -> None:
    rows = [
        sim(station, 1, 2.0, 1800),
        sim(station, 2, 2.1, 1200),
        sim(station, 3, value, 600),
    ]  # 600 s apart
    post(station, rows)
    last = stored(station, "ch_co2_sim")[-1]
    assert last[1] == ("rejected" if check else "accepted") and last[2] == check


def test_a_rate_jump_is_stored_suspect_and_the_channel_recovers_on_the_next_reading(
    station: Station,
) -> None:
    rows = [
        sim(station, 1, 2.0, 40),
        sim(station, 2, 2.1, 30),
        sim(station, 3, 3.4, 20),
        sim(station, 4, 3.4, 10),
    ]
    post(station, rows)
    found = stored(station, "ch_co2_sim")
    assert [(q, t) for _, q, t in found][2:] == [("uncertain", "rate_exceeded"), ("accepted", None)]
    assert (
        len(found) == 4
    )  # the jump is kept and may raise: it may be a real event, and the level it reached is the new reference


def test_a_stuck_sensor_is_suspect_once_its_run_reaches_the_sourced_count_and_span(station: Station) -> None:
    rows = [
        reading("ch_co2_sim", i + 1, 2.5, "mmHg", START - timedelta(hours=5, seconds=-300 * i))
        for i in range(45)
    ]
    for offset in range(0, 45, 15):
        post(station, rows[offset : offset + 15], key=f"key_{offset:05d}", batch=f"bat_{offset}")
    found = stored(station, "ch_co2_sim")
    flagged = [s for s, q, t in found if t == "stuck_value"]
    assert flagged == list(range(38, 46)) and all(q == "uncertain" for s, q, t in found if s in flagged)
    assert all(t == "insufficient_history" or t is None for s, _, t in found if s < 38)


def test_without_sourced_criteria_nothing_is_evaluated_and_nothing_is_rejected(station: Station) -> None:
    post(station, [reading("ch_rad", 1, 0.5, "µGy/h", START - timedelta(seconds=30))])
    assert stored(station, "ch_rad") == [(1, "accepted", "criteria_missing")]
    evaluation = station.client.get("/v1/sensors/rad", headers=station.headers("crew_a")).json()["evaluation"]
    assert evaluation["level"] is None and evaluation["status"] not in ("within_limit", "beyond_limit")


def test_live_is_refused_for_a_type_without_criteria_and_accepted_with_them(tmp_path: Path) -> None:
    refused = build_station(
        tmp_path / "a", registry={"channels": [channel("ch_rad", "rad", "radiation", "LIVE", "µGy/h")]}
    )
    assert [
        r[0] for r in sqlite3.connect(refused.db.path).execute("SELECT channel_id FROM sensors_channel")
    ] == []
    accepted = build_station(
        tmp_path / "b", registry={"channels": [channel("ch_co2", "co2", "co2", "LIVE", "mmHg")]}
    )
    rows = (
        sqlite3.connect(accepted.db.path)
        .execute("SELECT channel_id, origin_mode FROM sensors_channel")
        .fetchall()
    )
    assert rows == [("ch_co2", "LIVE")]
    assert (
        json.loads(accepted.registry_file.read_text(encoding="utf-8"))["channels"][0]["origin_mode"] == "LIVE"
    )


def test_a_channel_with_proved_readings_is_evaluated_and_one_with_unproved_readings_is_not(
    station: Station,
) -> None:
    def status() -> dict[str, Any]:
        found: dict[str, Any] = station.client.get(
            "/v1/sensors/co2", headers=station.headers("crew_a")
        ).json()
        return found["evaluation"]

    post(
        station,
        [reading("ch_co2_sim", 1, 2.0, "mmHg", station.clock.at - timedelta(seconds=15))],
        key="key_00001",
        batch="bat_1",
    )
    first = status()
    assert first["aggregated_value"] is None and first["level"] is None  # one reading proves no rate
    post(
        station,
        [reading("ch_co2_sim", 2, 2.001, "mmHg", station.clock.at - timedelta(seconds=5))],
        key="key_00002",
        batch="bat_2",
    )
    second = status()
    assert (
        second["aggregated_value"] == 2.0005 and second["coverage"] > 0
    )  # now aggregated; coverage still short
