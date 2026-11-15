"""E24 on a real station (real SQLite, E01 mechanisms, real mission and sensors modules, real HTTP).

T-E24-023 CO2 raise end to end, 050 acknowledge, 054 headers, 056/124 durable replay, 072 evaluator fault, 106
storage fault, 060/062 assistant guard, 051 privacy. The E23 plausibility gate is answered "passed" in the test only
(R-E23-2: no sourced criteria exist), see e24_station.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import build, post, seconds

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.scheduler import HealthState
from crew_health.rules.application import core
from crew_health.rules.application.ports import AssistantContext, AssistantWarningGuard

pytestmark = pytest.mark.E24


def _rows(st: base.Station, value: float, start: int, count: int) -> list[Any]:
    return [
        base.reading("ch_co2", start + n, value, "mmHg", seconds(st, (count - n) * 5)) for n in range(count)
    ]


def station(tmp: Path, *, send: bool = True) -> base.Station:
    st = build(tmp, registry={"channels": [base.channel("ch_co2", "co2", "co2", "SIM", "mmHg")]})
    st.initialize("SIM")
    st.advance(seconds=3600)
    if send:
        feed(st, 4.0, 1)
    return st


def feed(st: base.Station, value: float, start: int) -> None:
    rows = _rows(st, value, start, 720)
    for i in range(0, 720, 200):
        sent = post(st, rows[i : i + 200], f"k{start}_{i}")
        assert sent.status_code == 200, sent.text


def table(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def act(
    st: base.Station, who: str, warning_id: str, body: dict[str, Any], key: str, match: str | None
) -> Any:
    extra = {} if match is None else {"If-Match": match}
    return st.client.post(
        f"/v1/warnings/{warning_id}/actions", json=body, headers=st.headers(who, key, **extra)
    )


@pytest.mark.trace("T-E24-023")
def test_limit_crossing_raises_one_act_warning_visible_to_crew(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    items = st.client.get("/v1/warnings", headers=st.headers("crew_a")).json()["items"]
    by_definition = {i["definition_id"]: i for i in items}
    one = by_definition["co2_above_limit"]
    assert (one["level"], one["raised_by"], one["origin_mode"], one["state"]) == (
        "act", "sensor_limit", "SIM", "acknowledged" if False else "raised",
    )  # fmt: skip
    assert one["steps"] and one["allowed_actions"] == ["acknowledge", "add_note"]
    assert (
        by_definition["sensor_quiet"]["level"] == "watch"
    )  # the channel was silent before its first reading
    fetched = st.client.get(f"/v1/warnings/{one['warning_id']}", headers=st.headers("crew_a"))
    assert fetched.headers["ETag"] == '"1"' and fetched.json() == one
    again = [i for i in st.client.get("/v1/warnings", headers=st.headers("crew_b")).json()["items"]]
    assert {i["warning_id"] for i in again} == {
        i["warning_id"] for i in items
    }  # habitat scope: every crew screen


def test_more_readings_never_duplicate_the_active_warning(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    st.advance(seconds=3600)
    feed(st, 4.0, 721)
    rows = table(st, "SELECT definition_id, count(*) FROM rules_warning GROUP BY 1")
    assert dict(rows)["co2_above_limit"] == 1


@pytest.mark.trace("T-E24-050", "T-E24-054", "T-E24-056", "T-E24-124")
def test_acknowledge_headers_replay_and_conflicts(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    wid = next(
        i["warning_id"]
        for i in st.client.get("/v1/warnings", headers=st.headers("crew_a")).json()["items"]
        if i["definition_id"] == "co2_above_limit"
    )
    assert act(st, "crew_a", wid, {"action": "acknowledge"}, "a1", None).status_code == 428
    assert act(st, "crew_a", wid, {"action": "acknowledge"}, "a2", '"7"').status_code == 409
    done = act(st, "crew_a", wid, {"action": "acknowledge"}, "a3", '"1"')
    assert done.status_code == 200 and done.json()["state"] == "acknowledged"
    assert done.json()["acknowledgements"][0]["person_id"] == "per_a"
    assert done.json()["allowed_actions"] == ["add_note"]
    replay = act(st, "crew_a", wid, {"action": "acknowledge"}, "a3", '"1"')  # the old revision, the same key
    assert replay.status_code == 200 and replay.content == done.content
    other_body = act(st, "crew_a", wid, {"action": "add_note", "note": "x"}, "a3", '"1"')
    assert other_body.status_code == 409 and other_body.json()["code"] == "DUPLICATE_COMMAND"
    second = act(st, "crew_a", wid, {"action": "acknowledge"}, "a4", '"2"')
    assert second.status_code == 409 and second.json()["code"] == "INVALID_TRANSITION"
    assert second.json()["current_state"]["warning_id"] == wid
    bad = act(st, "crew_b", wid, {"action": "resolve"}, "a5", '"2"')
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_VALUE" and bad.json()["field_errors"]
    st.advance(hours=25)
    late = act(st, "crew_a", wid, {"action": "acknowledge"}, "a3", '"1"')
    assert late.status_code == 409 and late.json()["code"] == "DUPLICATE_COMMAND"
    assert late.json()["current_state"]["warning_id"] == wid


def test_unknown_warning_is_not_found(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path, send=False)
    assert st.client.get("/v1/warnings/wrn_none", headers=st.headers("crew_a")).status_code == 404
    assert act(st, "crew_a", "wrn_none", {"action": "acknowledge"}, "n1", '"1"').status_code == 404


@pytest.mark.trace("T-E24-072")
def test_evaluator_fault_keeps_the_reading_and_freezes_clears(
    patched: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    st = station(tmp_path)
    before = table(st, "SELECT warning_id, revision, state FROM rules_warning ORDER BY 1")

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected evaluator fault")

    monkeypatch.setattr(core.engine, "evaluate", broken)
    st.advance(seconds=3600)
    feed(st, 2.0, 721)  # every reading is accepted although the evaluator is down
    assert table(st, "SELECT count(*) FROM sensors_reading")[0][0] == 1440
    assert table(st, "SELECT warning_id, revision, state FROM rules_warning ORDER BY 1") == before
    assert table(st, "SELECT count(*) FROM rules_pending")[0][0] >= 1
    health = {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}
    assert health.get("rules") == "EVALUATION_UNAVAILABLE"


@pytest.mark.trace("T-E24-106", "T-E24-123")
def test_storage_fault_commits_nothing_and_is_never_converted(
    patched: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    st = station(tmp_path, send=False)

    def full(*args: object, **kwargs: object) -> object:
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(core.engine, "evaluate", full)
    rows = _rows(st, 4.0, 1, 200)
    sent = post(st, rows, "f1")
    assert sent.status_code == 500 and sent.json()["retryable"] is True
    assert table(st, "SELECT count(*) FROM sensors_reading")[0][0] == 0
    assert table(st, "SELECT count(*) FROM rules_pending")[0][0] == 0


@pytest.mark.trace("T-E24-062", "T-E24-003")
def test_the_assistant_cannot_raise_act_or_touch_a_warning(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    guard = st.resolver().require(AssistantWarningGuard)
    wid = table(st, "SELECT warning_id FROM rules_warning WHERE definition_id = 'co2_above_limit'")[0][0]
    ctx = AssistantContext("assistant_stub", "per_a")
    with st.begin() as uow:
        assert guard.propose_level(uow, ctx, "act", "sleep_reaction").code == "LEVEL_NOT_ALLOWED"  # type: ignore[union-attr]
        assert guard.propose_level(uow, ctx, "emergency", None).code == "LEVEL_NOT_ALLOWED"  # type: ignore[union-attr]
        assert guard.propose_action(uow, ctx, wid, "clear").code == "ACTION_NOT_ALLOWED"
        assert guard.propose_level(uow, ctx, "watch", "sleep_reaction").level == "watch"  # type: ignore[union-attr]
    assert table(st, f"SELECT revision, state FROM rules_warning WHERE warning_id = '{wid}'") == [
        (1, "raised")
    ]
    audited = table(st, "SELECT count(*) FROM platform_audit WHERE event_id IS NOT NULL")[0][0]
    assert audited > 0
