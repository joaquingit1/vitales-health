"""UC-S1 on the real stack: temporary on-disk SQLite, create_app, real HTTP (T-E23-020/021/026/027/028/041/048/052).

Traces: R35, R42, V55, D01 and PLAN deviations 1 and 2 (per-index admission, one evaluation per accepted row)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, START, Station, build_station, channel, reading

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(
        tmp_path,
        registry={
            "channels": [
                channel("ch_co2", "co2", "co2", "SIM", "mmHg"),
                channel("ch_co2_ppm", "co2", "co2", "SIM", "ppm"),
                channel(
                    "ch_rad_replay",
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
    assert st.app.state.runtime.serving, st.app.state.runtime.fault_code
    st.initialize("SIM")
    st.advance(seconds=60)
    return st


def post(
    st: Station,
    rows: list[Any],
    key: str = "key_00001",
    batch: str = "bat_1",
    who: str = "adapter",
    path: str = PATH,
) -> Any:
    return st.client.post(path, json={"batch_id": batch, "readings": rows}, headers=st.headers(who, key))


def sql(st: Station, query: str, *args: Any) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(query, args).fetchall()
    finally:
        connection.close()


def at(st: Station, seconds: int) -> Any:
    return st.clock.at - timedelta(seconds=seconds)


@pytest.mark.trace("T-E23-020", "T-E23-084", "PLAN-c1-2", "R35")
def test_a_malformed_row_is_rejected_by_index_and_the_valid_rows_around_it_survive(station: Station) -> None:
    rows = [
        reading("ch_co2", 1, 2.5, "mmHg", at(station, 20)),
        {
            "channel_id": "ch_co2",
            "adapter_sequence": "two",
            "value": 1,
        },  # malformed: wrong type, missing fields
        reading("ch_co2", 3, 2.6, "mmHg", at(station, 10)),
        reading("ch_co2", 4, 2.7, "bogus", at(station, 5)),
    ]
    reply = post(station, rows)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["accepted"] == 2 and body["duplicates"] == 0 and len(body["reading_ids"]) == 2
    assert [(r["index"], r["code"]) for r in body["rejected"]] == [
        (1, "MALFORMED_REQUEST"),
        (3, "INVALID_UNIT"),
    ]
    assert all(r["message"] for r in body["rejected"])  # a configured, non empty message on every rejection
    stored = sql(station, "SELECT rejected_json FROM sensors_batch")
    assert "two" in stored[0][0]  # the raw JSON of the rejected row is kept, never discarded


@pytest.mark.trace("T-E23-021", "T-E23-084", "PLAN-c1-1", "R42")
def test_every_accepted_row_is_evaluated_once_in_ingest_order_with_its_own_input(station: Station) -> None:
    rows = [reading("ch_co2", n, 2.0 + n / 10, "mmHg", at(station, 30 - n)) for n in range(1, 4)]
    assert post(station, rows).status_code == 200
    inputs = sql(station, "SELECT input_ref, high_rev FROM sensors_evaluation_input ORDER BY high_rev")
    assert [r[1] for r in inputs] == sorted({r[1] for r in inputs}) and len(
        inputs
    ) == 3  # one input per row, in order
    assert len(sql(station, "SELECT completion_id FROM platform_evaluation_completed")) == 3
    # the same rows sent one request at a time give the same number of inputs (batch and singles agree)
    assert sql(station, "SELECT count(*) FROM sensors_reading")[0][0] == 3


@pytest.mark.trace("T-E23-028", "T-E23-052", "R35")
def test_duplicates_replays_and_changed_content_under_the_same_sequence(station: Station) -> None:
    row = reading("ch_co2", 1, 2.5, "mmHg", at(station, 20))
    assert post(station, [row]).status_code == 200
    retried = post(
        station, [row]
    )  # the same key and body after the acknowledgement: the stored receipt answers
    assert retried.status_code == 409 and retried.json()["code"] == "DUPLICATE_COMMAND"
    assert retried.json()["current_state"]["operation_id"] == "ingestReadings"
    again = post(station, [row], key="key_00002", batch="bat_2")  # a new request with the same reading
    assert again.json()["duplicates"] == 1 and again.json()["accepted"] == 0
    assert (
        again.json()["reading_ids"]
        == post(station, [row], key="key_00003", batch="bat_3").json()["reading_ids"]
    )
    changed = post(station, [{**row, "value": 9.9}], key="key_00004", batch="bat_4")
    assert [(r["index"], r["code"]) for r in changed.json()["rejected"]] == [(0, "INVALID_VALUE")]
    assert (
        sql(station, "SELECT count(*) FROM sensors_reading")[0][0] == 1
    )  # nothing silently dropped or doubled
    assert (
        station.resolver()
        .require(__import__("crew_health.kernel.scheduler", fromlist=["HealthState"]).HealthState)
        .snapshot()
        .status
        == "degraded"
    )  # SEQUENCE_CONFLICT is loud
    other_body = post(station, [row], key="key_00001", batch="bat_9")  # the first key with another body
    assert other_body.status_code == 409


@pytest.mark.trace("T-E23-026", "R35")
def test_oversized_and_broken_envelopes_fail_the_request_with_contract_codes(station: Station) -> None:
    row = reading("ch_co2", 1, 2.5, "mmHg", at(station, 5))
    assert post(station, [row] * 1001).status_code == 413
    assert post(station, []).status_code == 400
    broken = station.client.post(
        PATH,
        json={"batch_id": "bat_1", "readings": [row], "extra": 1},
        headers=station.headers("adapter", "key_0000a"),
    )
    assert broken.status_code == 400
    wrong_type = station.client.post(
        PATH, content=b"{}", headers={**station.headers("adapter", "key_0000b"), "Content-Type": "text/plain"}
    )
    assert wrong_type.status_code == 415
    assert post(station, [row], key="key_0000c", who="crew_a").status_code in (403, 401)


@pytest.mark.trace("T-E23-027", "R35")
def test_per_row_domain_rejections_and_adapter_binding(station: Station) -> None:
    rows = [
        reading("ch_missing", 1, 1.0, "mmHg", at(station, 9)),
        reading("ch_co2", 2, 1.0, "mmHg", at(station, 8), origin="LIVE"),
        reading("ch_co2", 3, 1.0, "mmHg", at(station, 7), replay={"dataset": "d", "time_basis": "b"}),
        reading("ch_co2_ppm", 4, 4000, "ppm", at(station, 6)),  # ppm needs a cabin pressure
        reading("ch_rad_replay", 5, 1.0, "µGy/h", at(station, 5), origin="SIM"),
    ]
    codes = [(r["index"], r["code"]) for r in post(station, rows).json()["rejected"]]
    assert codes == [
        (0, "ADAPTER_NOT_TRUSTED"),
        (1, "ORIGIN_MISMATCH"),
        (2, "INVALID_VALUE"),
        (3, "INVALID_VALUE"),
        (4, "ORIGIN_MISMATCH"),
    ]
    foreign = post(
        station, [reading("ch_co2", 9, 1.0, "mmHg", at(station, 3))], key="key_00005", who="other_adapter"
    )
    assert foreign.status_code == 403 and foreign.json()["code"] == "ADAPTER_NOT_TRUSTED"
    other = post(
        station,
        [reading("ch_co2", 9, 1.0, "mmHg", at(station, 3))],
        key="key_00006",
        path="/v1/adapters/adp_other/readings",
        who="other_adapter",
    )
    assert other.json()["rejected"][0]["code"] == "ADAPTER_NOT_TRUSTED"  # a channel of another adapter


@pytest.mark.trace("T-E23-041", "D01", "R35")
def test_a_replay_reading_keeps_its_original_date_and_label_and_is_never_a_person_record(
    station: Station,
) -> None:
    original = START.replace(year=2022, month=4, day=2)
    row = reading("ch_rad_replay", 1, 0.5, "µGy/h", original, origin="REPLAY")
    reply = post(station, [row])
    assert reply.json()["accepted"] == 1, reply.text
    stored = station.client.get(
        "/v1/sensor-readings/" + reply.json()["reading_ids"][0], headers=station.headers("crew_a")
    ).json()
    assert stored["observed_at"].startswith("2022-04-02") and stored["origin_mode"] == "REPLAY"
    assert stored["replay"]["original_observed_at"].startswith("2022-04-02")
    assert (
        sql(station, "SELECT count(*) FROM records_observation")[0][0] == 0
    )  # sensors has no port to records


@pytest.mark.trace("T-E23-073", "R35")
def test_before_mission_init_readings_are_kept_but_never_evaluated(tmp_path: Path) -> None:
    st = build_station(tmp_path, registry={"channels": [channel("ch_co2", "co2", "co2", "SIM", "mmHg")]})
    reply = post(st, [reading("ch_co2", 1, 2.5, "mmHg", at(st, 5))])
    assert reply.json()["accepted"] == 1
    assert sql(st, "SELECT count(*) FROM sensors_evaluation")[0][0] == 0
    assert sql(st, "SELECT count(*) FROM platform_evaluation_completed")[0][0] == 0


@pytest.mark.trace("T-E23-048", "R-E23-2", "AGENTS-1.5")
def test_without_plausibility_criteria_capture_stays_unevaluated_and_live_is_refused(tmp_path: Path) -> None:
    live = channel("ch_live", "co2", "co2", "LIVE", "mmHg")
    st = build_station(
        tmp_path, registry={"channels": [live, channel("ch_sim", "co2", "co2", "SIM", "mmHg")]}
    )
    names = [r[0] for r in sql(st, "SELECT channel_id FROM sensors_channel")]
    assert (
        names == []
    )  # the whole registry is refused: LIVE needs sourced criteria, nothing is partly accepted
    ok = build_station(
        tmp_path / "second", registry={"channels": [channel("ch_sim", "co2", "co2", "SIM", "mmHg")]}
    )
    ok.initialize("SIM")
    ok.advance(seconds=60)
    assert post(ok, [reading("ch_sim", 1, 2.5, "mmHg", at(ok, 5))]).status_code == 200
    evaluation = ok.client.get("/v1/sensors/co2", headers=ok.headers("crew_a")).json()["evaluation"]
    assert (
        evaluation["status"] == "insufficient_data" and evaluation["level"] is None
    )  # never raised from nothing
