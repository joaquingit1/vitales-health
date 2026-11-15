"""Freshness on the real stack (E23 3.4, 9; T-E23-017/018/019/035/053/066/077/079/081/087; D3, D14).

The scheduled deadline is the primary path, the independent sweep the backup, every read the third line."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, START, Station, build_station, channel, reading

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"
STALE = 300
SCOPES = 2  # sensor.quiet and sensor.resumed go to the habitat and the operator scope (C18)


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(
        tmp_path,
        registry={
            "channels": [
                channel("ch_co2", "co2", "co2", "SIM", "mmHg", stale_after_seconds=STALE),
                channel("ch_rad", "rad", "radiation", "REPLAY", "µGy/h", expected_interval_seconds=None, stale_after_seconds=600),
            ]
        },
    )
    st.initialize("SIM")
    st.advance(seconds=60)
    return st


def push(st: Station, rows: list[Any], key: str) -> Any:
    return st.client.post(PATH, json={"batch_id": "b_" + key, "readings": rows}, headers=st.headers("adapter", key))


def state(st: Station, channel_id: str = "ch_co2") -> str:
    items = st.client.get("/v1/sensor-channels", headers=st.headers("crew_a")).json()["items"]
    return next(i["freshness"] for i in items if i["channel_id"] == channel_id)


def events(st: Station, kind: str) -> int:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute("SELECT count(*) FROM events_outbox WHERE event_type = ?", (kind,)).fetchone()[0]
    except sqlite3.Error:
        return -1
    finally:
        connection.close()


def feed(st: Station, sequence: int, key: str) -> None:
    row = reading("ch_co2", sequence, 2.5, "mmHg", st.clock.at - timedelta(seconds=1))
    assert push(st, [row], key).status_code == 200


@pytest.mark.trace("T-E23-017", "D3", "R35")
def test_a_fed_channel_is_current_then_stale_strictly_after_its_deadline(station: Station) -> None:
    feed(station, 1, "key_00001")
    assert state(station) == "current"
    station.advance(seconds=STALE)  # observed 1 s before the receive: the boundary is 299 s ahead, then past
    assert state(station) == "stale"  # a read recomputes: the third line, with no job having run


@pytest.mark.trace("T-E23-018", "T-E23-066", "R35")
def test_the_scheduled_deadline_records_the_transition_and_emits_quiet(station: Station) -> None:
    feed(station, 1, "key_00001")
    station.advance(seconds=STALE + 5)
    assert station.dispatch() >= 1  # the generation checked job fires
    assert events(station, "sensor.quiet") == SCOPES
    assert events(station, "sensor.quiet") == SCOPES and station.dispatch() == 0  # a completed generation never repeats


@pytest.mark.trace("T-E23-019", "T-E23-079", "R35")
def test_with_every_deadline_deleted_the_independent_sweep_still_records_the_transition(station: Station) -> None:
    feed(station, 1, "key_00001")
    connection = sqlite3.connect(station.db.path)
    connection.execute("DELETE FROM platform_schedule")
    connection.commit()
    connection.close()
    station.advance(seconds=STALE + 5)
    assert station.dispatch() == 0  # no deadline exists
    assert station.sweep() >= 1  # the backup finds the missed transition
    assert events(station, "sensor.quiet") == SCOPES
    assert state(station) == "stale"


@pytest.mark.trace("T-E23-053", "T-E23-077", "D3")
def test_a_stale_channel_never_becomes_current_from_a_clock_jump_only_new_data_resumes_it(station: Station) -> None:
    feed(station, 1, "key_00001")
    station.advance(seconds=STALE + 5)
    station.sweep()
    assert state(station) == "stale"
    station.advance(seconds=-(STALE + 120))  # the clock jumps backward, inside the old window
    station.sweep()
    assert state(station) == "stale"  # not rejuvenated
    station.advance(seconds=STALE + 400)
    feed(station, 2, "key_00002")
    assert state(station) == "current"
    assert events(station, "sensor.resumed") == SCOPES  # resumed only on an eligible advancing observation


@pytest.mark.trace("T-E23-081", "T-E23-087", "D3")
def test_a_late_arrival_cannot_pull_the_newest_observation_backward_or_rejuvenate(station: Station) -> None:
    feed(station, 2, "key_00002")
    station.advance(seconds=STALE + 5)
    station.sweep()
    old = reading("ch_co2", 1, 2.4, "mmHg", station.clock.at - timedelta(seconds=STALE + 2))
    assert push(station, [old], "key_00003").status_code == 200  # kept as a real reading
    assert state(station) == "stale"  # but it is older than E: it neither advances E nor resets freshness
    connection = sqlite3.connect(station.db.path)
    last = connection.execute("SELECT last_observed_at, window_end FROM sensors_channel WHERE channel_id = 'ch_co2'").fetchone()
    connection.close()
    assert last[1] is not None and last[0] == last[1]


@pytest.mark.trace("T-E23-035", "D14", "R35")
def test_replay_freshness_follows_the_receive_reference_and_keeps_the_original_date(station: Station) -> None:
    original = START.replace(year=2022, month=4, day=2)
    row = reading("ch_rad", 1, 0.5, "µGy/h", original, origin="REPLAY")
    assert push(station, [row], "key_00004").status_code == 200
    assert state(station, "ch_rad") == "current"  # 2022 data, received now: D14 is the one exception to D3
    again = push(station, [row], "key_00005")  # looping the file never resets E or the reference
    assert again.json()["duplicates"] == 1
    station.advance(seconds=700)
    assert state(station, "ch_rad") == "stale"


@pytest.mark.trace("T-E23-079", "R-E23-1", "DE23-13")
def test_an_environment_switch_keeps_old_readings_out_of_the_new_place_and_watches_quiet(station: Station) -> None:
    feed(station, 1, "key_00001")
    proposal = station.client.post(
        "/v1/environment/changes",
        json={"environment_id": "transit", "reason": "demo_switch"},
        headers=station.headers("operator", "key_0000p"),
    )
    assert proposal.status_code == 201, proposal.text
    profile = proposal.json()
    needed = profile["confirmations_required"]
    for who in ("crew_a", "crew_b")[:needed]:
        done = station.client.post(
            f"/v1/environment/changes/{profile['profile_id']}/actions",
            json={"action": "confirm"},
            headers=station.headers(who, "key_c_" + who, **{"If-Match": '"1"'}),
        )
        profile = done.json()
        assert done.status_code == 200, done.text
    assert profile["state"] == "active" and profile["environment_id"] == "transit"
    status = station.client.get("/v1/sensors/co2", headers=station.headers("crew_a")).json()
    assert status["reading_state"] == "no_reading" and status["latest_reading"] is None  # no carry over
    station.advance(seconds=STALE + 10)
    station.sweep()
    assert events(station, "sensor.quiet") >= 1  # the activation quiet watch still catches a dead feed
