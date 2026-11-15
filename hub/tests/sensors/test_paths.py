"""Remaining runtime paths on the real stack: throttled status events, bounded windows, conversion with a cabin
pressure, unmapped and future time, rejected quality (T-E23-022/023/024/025/068/080)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, Station, build_station, channel, reading

from crew_health.kernel.scheduler import HealthState

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"


def make(tmp: Path, **kw: Any) -> Station:
    st = build_station(
        tmp,
        registry={
            "channels": [
                channel("ch_co2", "co2", "co2", "SIM", "mmHg"),
                channel("ch_ppm", "co2", "co2", "SIM", "ppm"),
                channel("ch_lost", "co2", "co2", "SIM", "mmHg", adapter_id="adp_other"),
            ]
        },
        **kw,
    )
    st.initialize("SIM")
    st.advance(seconds=60)
    return st


def send(st: Station, rows: list[Any], key: str, who: str = "adapter", adapter: str = ADAPTER) -> Any:
    return st.client.post(
        f"/v1/adapters/{adapter}/readings",
        json={"batch_id": "b_" + key, "readings": rows},
        headers=st.headers(who, key),
    )


def events(st: Station, kind: str) -> int:
    connection = sqlite3.connect(st.db.path)
    try:
        return int(
            connection.execute("SELECT count(*) FROM events_outbox WHERE event_type = ?", (kind,)).fetchone()[
                0
            ]
        )
    finally:
        connection.close()


@pytest.mark.trace("T-E23-068", "CONTRACTS-13")
def test_status_changed_is_throttled_with_a_durable_trailing_emission(tmp_path: Path) -> None:
    st = make(tmp_path)
    row = lambda n: [reading("ch_co2", n, 2.5, "mmHg", st.clock.at - timedelta(seconds=1))]  # noqa: E731
    assert send(st, row(1), "key_00001").json()["accepted"] == 1
    st.advance(seconds=305)
    st.sweep()  # quiet: the first status change is emitted at once
    first = events(st, "sensor.status_changed")
    assert first == 2  # the reading's own change at ingest, then quiet 305 s later (outside the 5 s interval)
    assert send(st, row(2), "key_00002").json()["accepted"] == 1  # resumed inside the expected interval
    assert events(st, "sensor.status_changed") == first  # held, not dropped
    st.advance(seconds=6)
    st.dispatch()  # the trailing job fires after the interval
    assert events(st, "sensor.status_changed") == first + 1


@pytest.mark.trace("T-E23-025", "T-E23-070", "E23-3.7")
def test_a_window_above_the_scan_bound_is_unevaluated_never_partial_and_raises_the_fault(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from crew_health.sensors.application.ports import FreshnessSweeper

    st = make(tmp_path)
    runtime = st.resolver().require(FreshnessSweeper)._live.need()  # type: ignore[attr-defined]
    runtime.limits = replace(
        runtime.limits, window_scan_max_readings=2
    )  # a hub setting lowered after the release passed
    rows = [reading("ch_co2", n, 2.5, "mmHg", st.clock.at - timedelta(seconds=10 - n)) for n in range(1, 5)]
    assert send(st, rows, "key_00001").status_code == 200
    health = st.resolver().require(HealthState).snapshot()
    assert any(c.code == "WINDOW_SCAN_BOUND" for c in health.components)
    status = st.client.get("/v1/sensors/co2", headers=st.headers("crew_a")).json()
    assert status["evaluation"]["status"] == "insufficient_data" and status["shown_level"] is None


@pytest.mark.trace("T-E23-022", "T-E23-023", "T-E23-024", "DE23-6")
def test_a_ppm_reading_stores_its_original_value_unit_and_the_assumed_pressure(tmp_path: Path) -> None:
    st = make(tmp_path)
    row = reading(
        "ch_ppm",
        1,
        4646,
        "ppm",
        st.clock.at - timedelta(seconds=2),
        cabin_pressure_mmhg={"value": 760, "basis": "assumed"},
        aggregation={"kind": "mean", "window_seconds": 60},
    )
    body = send(st, [row], "key_00001").json()
    assert body["accepted"] == 1, body
    stored = st.client.get(
        "/v1/sensor-readings/" + body["reading_ids"][0], headers=st.headers("crew_a")
    ).json()
    assert stored["original_value"] == 4646 and stored["original_unit"] == "ppm" and stored["unit"] == "mmHg"
    assert (
        stored["conversion"]["method"] == "ppm_to_mmhg"
        and stored["conversion"]["pressure_basis"] == "assumed"
    )
    assert abs(stored["value"] - 3.531) < 0.001  # 4646 ppm x 760 mmHg / 1e6, checked by hand
    assert stored["aggregation"] == {"kind": "mean", "window_seconds": 60}


@pytest.mark.trace("T-E23-080", "D3", "AGENTS-5")
def test_unmapped_future_and_rejected_readings_are_kept_but_never_current_or_evaluated(
    tmp_path: Path,
) -> None:
    st = make(tmp_path)
    future = reading("ch_co2", 1, 2.5, "mmHg", st.clock.at + timedelta(days=2))
    rejected = reading(
        "ch_co2", 2, 2.5, "mmHg", st.clock.at - timedelta(seconds=2), quality_status="rejected"
    )
    body = send(st, [future, rejected], "key_00001").json()
    assert body["accepted"] == 2
    channels = {
        c["channel_id"]: c
        for c in st.client.get("/v1/sensor-channels", headers=st.headers("crew_a")).json()["items"]
    }
    assert channels["ch_co2"]["freshness"] == "stale"  # a future time is never current
    unmapped = send(
        st,
        [reading("ch_lost", 1, 2.5, "mmHg", st.clock.at - timedelta(seconds=2))],
        "key_00002",
        "other_adapter",
        "adp_other",
    )
    assert unmapped.json()["accepted"] == 1  # no proven clock segment: kept, time unknown
    channels = {
        c["channel_id"]: c
        for c in st.client.get("/v1/sensor-channels", headers=st.headers("crew_a")).json()["items"]
    }
    assert channels["ch_lost"]["freshness"] == "stale"  # unknown time fails closed, never fresh
    connection = sqlite3.connect(st.db.path)
    unevaluated = connection.execute("SELECT count(*) FROM platform_evaluation_completed").fetchone()[0]
    connection.close()
    assert unevaluated == 2  # the rejected reading was never evaluated; the future and unmapped ones were
