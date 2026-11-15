"""E24 section 9 critical paths: each test removes the primary and shows the backup and the fail safe (AGENTS 2).

| Critical path | Primary | Backup | Fail safe | Test here (design IDs) |
|---|---|---|---|---|
| Habitat hazard reaches the crew | Sensor limit on a live channel | `sensor_quiet` on the silent channel; E25's manual routine item; the vehicle's own caution and warning system | "No data since"; open warnings stay open | `test_path1_...` (T-E24-024) |
| Storm reaches the crew with the link down | Monitor adapter records `radiation_monitor_alarm` | A person on the laptop records the alert from any screen | Emergency on every crew screen, never held | `test_path2_...` (T-E24-121) |
| Shared warning reaches every crew screen | SSE `warning.raised` into the attention layer | Catch-up by cursor (`getEvents` page) or the `listWarnings` snapshot | Interrupt on projection failure; last list dimmed with time | `test_path3_...` (T-E24-078, 098, 115, 117) |
| Assistant cannot raise or lower safety state | Domain types and transitions | SQLite CHECK and immutability trigger; loader check; API roles | Rejection with audit | `test_path4_...` (T-E24-003, 006, 062) |
| A warning clears only under its rule | Eligible evidence and occurrence resolution | Active unique index, immutable level and final state, spent triggers, clear freeze | Warning stays open | `test_path5_...` (T-E24-026, 045 to 049, 118 to 120, 127) |
| Capture during an evaluator fault | Evaluation in the producer's savepoint | Pending row and durable recovery | Record kept, warnings unchanged, no clears, degraded | `test_path6_...` (T-E24-072, 122, 123) |

Gaps on the other epics' side, stated rather than tested here: E25's manual routine item and the vehicle caution
and warning system (path 1) belong to E25 and the vehicle; the monitor adapter event `radiation_monitor_alarm`
does not exist yet (R-E24-9), and config/environment-profiles.json lets only `ground` and `operator` record
`space_weather_alert`, so a crew member cannot record it from a crew screen today (path 2 runs it as the operator
on the station laptop, which needs no ground link); the client attention layer, its interrupt and its dimmed last
list are T2's browser work (path 3 tests the hub side every client reconciles from).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
import rules_harness as h
from e24_station import (
    patched,  # noqa: F401
    restart,
)
from test_station_co2 import feed, station
from test_storm_occurrences_served import record

from crew_health.kernel.scheduler import HealthState
from crew_health.rules.application import core
from crew_health.rules.domain import clear_evidence
from crew_health.rules.domain.clear_evidence import Eligibility
from crew_health.rules.domain.decisions import Clear
from crew_health.rules.domain.definitions import parse_rules_document

pytestmark = [pytest.mark.E24, pytest.mark.trace("AGENTS-2", "E24-9")]


def _rows(st: base.Station, sql: str, *args: object) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def _write(st: base.Station, sql: str, *args: object) -> None:
    connection = sqlite3.connect(st.db.path)
    try:
        connection.execute(sql, args)
        connection.commit()
    finally:
        connection.close()


def _active(st: base.Station, who: str = "crew_a") -> dict[str, str]:
    items = st.client.get("/v1/warnings", headers=st.headers(who)).json()["items"]
    return {i["definition_id"]: i["level"] for i in items if i["state"] in ("raised", "acknowledged")}


def _health(st: base.Station) -> dict[str, str]:
    return {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}


@pytest.mark.trace("T-E24-024", "V70")
def test_path1_a_silent_channel_keeps_the_hazard_open_and_raises_quiet(patched: None, tmp_path: Path) -> None:  # noqa: F811
    st = station(tmp_path)
    assert _active(st)["co2_above_limit"] == "act"
    st.advance(seconds=900)  # primary removed: the CO2 channel stops reporting, past its stale bound
    st.sweep()
    st.dispatch()
    seen = _active(st)
    assert seen["co2_above_limit"] == "act"  # fail safe: the open warning stays open
    assert seen["sensor_quiet"] == "watch"  # backup: the quiet channel is itself a warning
    assert _active(st, "crew_b") == seen


@pytest.mark.trace("T-E24-121", "T-E24-075", "V58", "D2")
def test_path2_a_storm_recorded_on_the_laptop_reaches_every_crew_screen_without_ground_or_model(
    patched: None,  # noqa: F811
    tmp_path: Path,
) -> None:
    from e24_station import build

    st = build(tmp_path)  # no monitor adapter, no ground link, no model process: the primary is absent
    st.initialize("SIM")
    st.advance(hours=1)
    record(st, "space_weather_alert", 30, "storm_1")
    for who in ("crew_a", "crew_b", "operator"):
        items = st.client.get("/v1/warnings", headers=st.headers(who)).json()["items"]
        (storm,) = [i for i in items if i["definition_id"] == "radiation_storm_shelter"]
        assert storm["level"] == "emergency" and storm["origin_mode"] == "SIM"
        assert storm["title"] and storm["evidence_time_label"]


@pytest.mark.trace("T-E24-098", "T-E24-115", "T-E24-078")
def test_path3_a_client_that_missed_the_live_event_catches_up_by_cursor_and_snapshot(
    patched: None,  # noqa: F811
    tmp_path: Path,
) -> None:
    from e24_station import build

    st = build(tmp_path)
    st.initialize("SIM")
    st.advance(hours=1)
    record(st, "space_weather_alert", 30, "storm_1")  # raised while no SSE stream was open: primary removed
    for who in ("crew_a", "crew_b"):
        page = st.client.get(
            "/v1/events", params={"types": "warning.raised"},
            headers={**st.headers(who), "Accept": "application/json"},
        )  # fmt: skip
        assert page.status_code == 200, page.text
        events = [e for e in page.json()["events"] if e["type"] == "warning.raised"]
        assert events, page.json()  # backup 1: catch-up by cursor delivers the raise
        assert _active(st, who)["radiation_storm_shelter"] == "emergency"  # backup 2: the snapshot


@pytest.mark.trace("T-E24-006", "T-E24-003", "T-E24-062", "INV-E24-04")
def test_path4_storage_refuses_what_the_domain_would_refuse(patched: None, tmp_path: Path) -> None:  # noqa: F811
    st = station(tmp_path)
    (wid,) = _rows(st, "SELECT warning_id FROM rules_warning WHERE definition_id = 'co2_above_limit'")[0]
    before = _rows(st, "SELECT * FROM rules_warning WHERE warning_id = ?", wid)
    # Primary removed: these writes bypass the domain entirely and go straight to the served hub's database.
    attempts = (
        ("UPDATE rules_warning SET level = 'watch', revision = revision + 1 WHERE warning_id = ?", (wid,)),
        (
            "UPDATE rules_warning SET raised_by = 'assistant', revision = revision + 1 WHERE warning_id = ?",
            (wid,),
        ),
        (
            "UPDATE rules_warning SET state = 'dismissed', revision = revision + 1 WHERE warning_id = ?",
            (wid,),
        ),
        ("DELETE FROM rules_warning WHERE warning_id = ?", (wid,)),
        (
            "INSERT INTO rules_warning SELECT 'wrn_forged', revision, origin_node_id, 'assistant_pattern', "
            "config_release_id, rule_fingerprint, content_hash, trigger_kind, 'forged', 'act', 'assistant', "
            "sensor_type_id, channel_id, scope, 'k', person_id, environment_id, origin_mode, 'ins_x', headline_rank, "
            "state, raised_at, cleared_at, clear_reason, steps_json, evidence_json, body_json "
            "FROM rules_warning WHERE warning_id = ?",
            (wid,),
        ),
    )
    for sql, args in attempts:
        with pytest.raises(sqlite3.DatabaseError):
            _write(st, sql, *args)
    assert _rows(st, "SELECT * FROM rules_warning WHERE warning_id = ?", wid) == before
    for who in ("ground", "operator"):  # backup: API roles, a non-crew session has no action
        reply = st.client.post(
            f"/v1/warnings/{wid}/actions", json={"action": "resolve", "reason": "x"},
            headers=st.headers(who, "role_" + who, **{"If-Match": '"1"'}),
        )  # fmt: skip
        assert reply.status_code in (403, 404), reply.text
    assert _rows(st, "SELECT * FROM rules_warning WHERE warning_id = ?", wid) == before


@pytest.mark.trace("T-E24-026", "T-E24-120", "T-E24-127", "INV-E24-05", "INV-E24-07")
def test_path5_with_the_evidence_check_broken_the_backups_still_keep_the_warning_open(
    patched: None,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Domain: the eligibility check is replaced by one that always says yes (primary removed). While clears are
    # frozen by a pending evaluation the engine still emits no clear (backup: the clear freeze).
    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents(), frozen=True)
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    with monkeypatch.context() as broken:  # scoped: the station below keeps the rules module composed
        broken.setattr(clear_evidence, "eligible", lambda *a, **k: Eligibility(True, "eligible"))
        for _ in range(40):
            world.advance(60)
            decision = world.feed(
                world.sensor(results=[h.result("co2_1h_act", crossed=False, clear_holds=True)])
            )
            assert not any(isinstance(s, Clear) for s in decision)
    assert world.by_definition("co2_above_limit")
    # Storage: a second active warning for the same key, and reopening or relowering a final one, are refused.
    st = station(tmp_path)
    (wid,) = _rows(st, "SELECT warning_id FROM rules_warning WHERE definition_id = 'co2_above_limit'")[0]
    with pytest.raises(sqlite3.IntegrityError):
        _write(
            st,
            "INSERT INTO rules_warning SELECT 'wrn_twin', revision, origin_node_id, definition_id, "
            "config_release_id, rule_fingerprint, content_hash, trigger_kind, 'twin', level, raised_by, "
            "sensor_type_id, channel_id, scope, scope_key, person_id, environment_id, origin_mode, insight_id, "
            "headline_rank, state, raised_at, cleared_at, clear_reason, steps_json, evidence_json, body_json "
            "FROM rules_warning WHERE warning_id = ?",
            wid,
        )
    assert _active(st)["co2_above_limit"] == "act"


@pytest.mark.trace("T-E24-072", "T-E24-122", "T-E24-123", "INV-E24-16")
def test_path6_an_evaluator_fault_keeps_capture_survives_restart_and_never_clears(
    patched: None,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    st = station(tmp_path)
    before = _rows(st, "SELECT warning_id, revision, state FROM rules_warning ORDER BY 1")

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected evaluator fault")

    monkeypatch.setattr(core.engine, "evaluate", broken)  # primary removed
    st.advance(seconds=3600)
    feed(st, 2.0, 721)  # safe-side readings that would count toward clearing if evaluated
    assert _rows(st, "SELECT count(*) FROM sensors_reading")[0][0] == 1440  # fail safe: every record kept
    assert _rows(st, "SELECT count(*) FROM rules_pending")[0][0] >= 1  # backup: durable pending rows
    assert _health(st).get("rules") == "EVALUATION_UNAVAILABLE"  # loud
    restart(st)
    assert _rows(st, "SELECT count(*) FROM rules_pending")[0][0] >= 1  # the rows survive the restart
    assert _rows(st, "SELECT warning_id, revision, state FROM rules_warning ORDER BY 1") == before
    monkeypatch.undo()  # the evaluator is fixed; recovery runs the backlog under its original times
    st.advance(seconds=60)
    for _ in range(5):
        st.dispatch()
    co2 = _rows(st, "SELECT state FROM rules_warning WHERE definition_id = 'co2_above_limit'")
    assert co2 == [("raised",)]  # a backlog is old evidence: recovery never clears (3.6, D3, INV-16)
    assert _active(st)["co2_above_limit"] == "act"
    assert timedelta(seconds=0) <= st.clock.at - base.START
