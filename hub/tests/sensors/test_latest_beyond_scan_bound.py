"""Hard rule 5 on a long-running channel: once it holds more readings than sensors.window_scan_max_readings, the card's
latest reading and the series handed to trends are still the newest data, never a frozen old slice shown as current.

The bound is 20,000 in production (about 28 h of CO2 at the 5 s EV01 Table 6.1-1 cadence); it is lowered at
runtime here, as test_paths does, so eight readings cross it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, Station, build_station, channel, reading

from crew_health.sensors.application.ports import FreshnessSweeper, SensorSeriesReader

pytestmark = [pytest.mark.e23, pytest.mark.trace("T-E23-025", "R-E23-2")]
PATH = "/v1/adapters/" + ADAPTER + "/readings"
COUNT = 8


def _fed(tmp: Path, values: list[float] | None = None) -> tuple[Station, list[dict[str, Any]]]:
    """Eight moving readings 30 s apart, then the scan bound lowered to 3: the channel now holds more readings
    than the bound while its stored evaluation is current, as a CO2 channel does after about 28 h."""
    st = build_station(
        tmp, registry={"channels": [channel("ch_co2", "co2", "co2", "SIM", "mmHg", stale_after_seconds=300)]}
    )
    st.initialize("SIM")
    st.advance(seconds=60)
    rows = []
    for i, value in enumerate(values or [2.0 + 0.01 * i for i in range(1, COUNT + 1)], start=1):
        row = reading("ch_co2", i, value, "mmHg", st.clock.at - timedelta(seconds=1))
        reply = st.client.post(
            PATH,
            json={"batch_id": f"b_{i:05d}", "readings": [row]},
            headers=st.headers("adapter", f"k_{i:05d}"),
        )
        assert reply.status_code == 200, (i, reply.text)
        rows.append(row)
        st.advance(seconds=30)
    st.advance(seconds=-29)  # one second after the last reading: the channel is current
    runtime = st.resolver().require(FreshnessSweeper)._live.need()  # type: ignore[attr-defined]
    runtime.limits = replace(runtime.limits, window_scan_max_readings=3, series_max_points=3)
    return st, rows


def test_the_card_shows_the_newest_reading_past_the_scan_bound(tmp_path: Path) -> None:
    st, rows = _fed(tmp_path)
    status = st.client.get("/v1/sensors/co2", headers=st.headers("crew_a")).json()
    assert status["reading_state"] == "current"
    latest = status["latest_reading"]
    assert (latest["value"], latest["observed_at"]) == (rows[-1]["value"], rows[-1]["observed_at"])


def test_the_trend_series_is_the_newest_window_in_ascending_order(tmp_path: Path) -> None:
    st, rows = _fed(tmp_path)
    reader = st.resolver().require(SensorSeriesReader)
    found = reader.accepted("ch_co2", until=st.clock.at, limit=3)
    values = [float(p.value.value) for p in found.points]
    assert values == [r["value"] for r in rows[-3:]] and found.truncated
    observed = [p.observed_at for p in found.points]
    assert observed == sorted(observed)
    earlier = reader.accepted("ch_co2", until=st.clock.at - timedelta(seconds=60), limit=2)
    assert [float(p.value.value) for p in earlier.points] == [r["value"] for r in rows[-4:-2]]


def test_the_served_series_never_hands_back_an_old_slice_past_its_bound(tmp_path: Path) -> None:
    """A rejected reading among the oldest rows must not hide that the series is over sensors.series_max_points."""
    values = [2.01, 800.0, *(2.02 + 0.01 * i for i in range(6))]
    st, rows = _fed(tmp_path, values)
    whole = st.client.get("/v1/sensors/co2/series?bucket_seconds=1", headers=st.headers("crew_a"))
    assert whole.status_code == 422 and whole.json()["code"] == "INVALID_VALUE", whole.text
    window = f"from={rows[-3]['observed_at']}&to={rows[-2]['observed_at']}&bucket_seconds=1"
    narrow = st.client.get("/v1/sensors/co2/series?" + window, headers=st.headers("crew_a"))
    assert narrow.status_code == 200, narrow.text
    assert [p["value"] for p in narrow.json()["points"]] == [r["value"] for r in rows[-3:-1]]
    early = f"from={rows[0]['observed_at']}&to={rows[3]['observed_at']}&bucket_seconds=1"
    first = st.client.get("/v1/sensors/co2/series?" + early, headers=st.headers("crew_a"))
    assert first.status_code == 200, first.text
    assert [p["value"] for p in first.json()["points"]] == [
        rows[0]["value"],
        rows[2]["value"],
        rows[3]["value"],
    ]
