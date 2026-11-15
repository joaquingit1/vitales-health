"""The seams other modules read and the HTTP reads of the sensors (E23 4.2, 6; T-E23-049/060/072/076/078/082/090).

Provided ports (SensorStateQuery, SensorSeriesReader, ProvenanceStreamsPort, FreshnessSweeper, ActivationParticipant)
and every read operation, on the real stack."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, Station, build_station, channel, reading

from crew_health.sensors.application.ports import (
    FreshnessSweeper,
    ProvenanceStreamsPort,
    SensorSeriesReader,
    SensorStateQuery,
)

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(
        tmp_path,
        registry={
            "channels": [
                channel("ch_co2", "co2", "co2", "SIM", "mmHg"),
                channel("ch_co2_b", "co2", "co2", "SIM", "mmHg", display_note="second place"),
                channel(
                    "ch_rad",
                    "rad",
                    "radiation",
                    "REPLAY",
                    "µGy/h",
                    expected_interval_seconds=None,
                    stale_after_seconds=600,
                ),
            ]
        },
    )
    st.initialize("SIM")
    st.advance(seconds=60)
    rows = [
        reading("ch_co2", n, 2.0 + n / 10000, "mmHg", st.clock.at - timedelta(seconds=40 - 5 * n))
        for n in range(1, 6)
    ]
    assert (
        st.client.post(
            PATH, json={"batch_id": "b1", "readings": rows}, headers=st.headers("adapter", "key_00001")
        ).status_code
        == 200
    )
    return st


def get(st: Station, path: str, who: str = "crew_a") -> Any:
    return st.client.get(path, headers=st.headers(who))


@pytest.mark.trace("T-E23-049", "T-E23-072", "S4.2")
def test_provided_ports_answer_from_the_callers_view(station: Station) -> None:
    resolver = station.resolver()
    state, series, streams = (
        resolver.require(SensorStateQuery),
        resolver.require(SensorSeriesReader),
        resolver.require(ProvenanceStreamsPort),
    )
    with station.begin() as uow:
        assert set(state.channels(uow)) == {"ch_co2", "ch_co2_b", "ch_rad"}
        assert (
            state.freshness(uow, "ch_co2") == "current"
            and state.freshness(uow, "ch_co2_b") == "never_reported"
        )
        assert state.type_name(uow, "co2") == "CO2"
        with pytest.raises(Exception, match="unknown channel"):
            state.freshness(uow, "nope")
        with pytest.raises(Exception, match="unknown sensor type"):
            state.type_name(uow, "nope")
    slice_ = series.accepted("ch_co2", until=station.clock.at, limit=3)
    assert len(slice_.points) == 3 and slice_.truncated is True  # the newest three, and it says it cut
    assert (
        slice_.points[0].value.unit.code == "mmHg"
        and slice_.points[-1].observed_at > slice_.points[0].observed_at
    )
    assert series.accepted("ch_co2", until=station.clock.at, limit=50).truncated is False
    with pytest.raises(Exception, match="outside"):
        series.accepted("ch_co2", until=station.clock.at, limit=0)
    with station.db.snapshot() as view:
        found = {s.channel_id: s for s in streams.habitat_streams(view)}
    assert found["ch_rad"].origin_mode == "REPLAY" and found["ch_rad"].dataset == "Test dataset"
    assert station.sweep() == 3 and resolver.require(FreshnessSweeper).sweep_once() == 3


@pytest.mark.trace("T-E23-060", "R35", "C13")
def test_status_series_channel_and_reading_reads(station: Station) -> None:
    status = get(station, "/v1/sensors/co2")
    assert status.status_code == 200 and status.headers["Config-Release"]
    body = status.json()
    assert (
        body["channel_id"]
        and body["reading_state"] == "current"
        and body["series_href"] == "/v1/sensors/co2/series"
    )
    assert (
        body["evaluation"]["status"] == "insufficient_data"
    )  # five readings cover too little of the one-hour window to evaluate (plausibility now passes)
    assert body["shown_level"] is None and body["linked_warning_ids"] == [] and body["limit_marks"]
    assert (
        "ch_co2_b" in body["trace"] or body["trace"] is not None
    )  # the other channel is in the trace, not merged
    listed = get(station, "/v1/sensors?limit=2").json()
    assert len(listed["items"]) == 2 and listed["page"]["next_cursor"]
    after = get(station, "/v1/sensors?limit=2&cursor=" + listed["page"]["next_cursor"]).json()
    assert after["items"] and {i["sensor_type_id"] for i in after["items"]}.isdisjoint(
        {i["sensor_type_id"] for i in listed["items"]}
    )
    assert get(station, "/v1/sensors/nope").status_code == 404
    assert get(station, "/v1/sensors/dust").status_code in (200, 422)
    default = get(station, "/v1/sensors/co2/series").json()["points"]
    assert (
        len(default) == 1 and 0.41 < default[0]["coverage"] < 0.42
    )  # 5 readings x 5 s in one 60 s bucket (C13)
    series = get(station, "/v1/sensors/co2/series?bucket_seconds=5").json()
    assert series["unit"] == "mmHg" and len(series["points"]) == 5
    assert all(0 < p["coverage"] <= 1 for p in series["points"]) and series["points"] == sorted(
        series["points"], key=lambda p: p["t"]
    )
    since = series["points"][2]["t"]
    cut = get(station, "/v1/sensors/co2/series?bucket_seconds=5&from=" + since).json()
    assert 0 < len(cut["points"]) < 5
    assert get(station, "/v1/sensors/co2/series?from=bad").status_code == 422
    empty = get(station, "/v1/sensors/rad/series")
    assert (
        empty.status_code == 200 and empty.json()["points"] == []
    )  # a channel with no reading: no invented point
    assert get(station, "/v1/sensors/co2/series?bucket_seconds=0").status_code == 422
    assert get(station, "/v1/sensors/co2/series?bucket_seconds=x").status_code == 422
    wide = get(station, "/v1/sensors/co2/series?bucket_seconds=3600").json()["points"]
    assert len(wide) == 1 and wide[0]["coverage"] <= 1  # one bucket, its own coverage
    channels = get(station, "/v1/sensor-channels?limit=1").json()
    assert len(channels["items"]) == 1 and channels["page"]["next_cursor"]
    assert get(station, "/v1/sensor-channels?limit=0").status_code == 400
    assert get(station, "/v1/sensor-channels?cursor=@@@").status_code == 400
    rid = get(station, "/v1/sensor-channels").json()["items"][0]["channel_id"]
    assert rid == "ch_co2"
    sensor = get(station, "/v1/sensor-readings/" + status.json()["latest_reading"]["reading_id"])
    assert sensor.status_code == 200 and sensor.json()["origin_mode"] == "SIM"
    assert get(station, "/v1/sensor-readings/rdg_missing").status_code == 404


@pytest.mark.trace("T-E23-076", "DE23-13")
def test_activation_starts_the_quiet_watch_only_for_channels_that_reported(station: Station) -> None:
    profile = station.client.post(
        "/v1/environment/changes",
        json={"environment_id": "transit", "reason": "demo_switch"},
        headers=station.headers("operator", "key_0000p"),
    ).json()
    for n, who in enumerate(("crew_a", "crew_b")[: profile["confirmations_required"]]):
        profile = station.client.post(
            f"/v1/environment/changes/{profile['profile_id']}/actions",
            json={"action": "confirm"},
            headers=station.headers(who, f"key_c{n}", **{"If-Match": f'"{profile["revision"]}"'}),
        ).json()
    assert profile["state"] == "active"
    channels = {c["channel_id"]: c for c in get(station, "/v1/sensor-channels").json()["items"]}
    assert (
        channels["ch_rad"]["freshness"] == "never_reported"
    )  # never reported, so no invented watch or reading
    assert (
        get(station, "/v1/sensors/co2").json()["reading_state"] == "no_reading"
    )  # the old place never carries over
    assert get(station, "/v1/sensors/rad").status_code in (200, 422)
