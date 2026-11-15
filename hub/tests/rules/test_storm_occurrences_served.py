"""A delayed all-clear never clears a newer storm alert, on the served hub and across restarts (E24 3.9, D3).

Mission events go through the real `recordMissionEvent` operation (E23 mission module), the real outbox and the
real rules module over one on-disk SQLite. Between every delivery the hub is restarted: a new `create_app` over the
same database, so nothing survives in memory. Causality uses `occurred_at`, never the delivery order.

Worked sequence (T-E24-118, 119): alert A occurred 10:00 raises W; alert B occurred 10:20 attaches; all-clear C
that occurred 10:10 arrives last and resolves A only, so W stays with the newer cause B outstanding; duplicates of
A, B and C change nothing; the all-clear occurred 10:30 clears W.

T-E24-075, 118, 119, 035, 037; INV-E24-08, INV-E24-17; H8, H13; D2, D3.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import build, restart

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-08", "INV-E24-17", "H8", "H13", "D3")]

ALERT, ALL_CLEAR = "space_weather_alert", "space_weather_all_clear"


def _rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def record(st: base.Station, event_type: str, minutes: int, key: str) -> None:
    occurred = base.START + timedelta(hours=1, minutes=minutes)
    body = {
        "event_type": event_type,
        "occurred_at": occurred.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "person_ids": [],
        "origin_mode": "SIM",
    }
    done = st.client.post("/v1/mission-events", json=body, headers=st.headers("operator", key))
    if (
        done.status_code == 409
    ):  # a retried key: D6's accepted replay receipt, the command is never executed again
        receipt = done.json()["current_state"]
        assert (receipt["kind"], receipt["command_key"], receipt["target"]) == (
            "accepted_command",
            key,
            "/v1/mission-events",
        ), done.text
    else:
        assert done.status_code in (200, 201), done.text
    st.dispatch()


def storm(st: base.Station) -> list[tuple[Any, ...]]:
    return _rows(
        st,
        "SELECT warning_id, level, state, clear_reason FROM rules_warning "
        "WHERE definition_id = 'radiation_storm_shelter' ORDER BY raised_at, warning_id",
    )


def outstanding(st: base.Station) -> list[str]:
    return [
        str(r[0])
        for r in _rows(
            st,
            "SELECT o.occurred_at FROM rules_occurrence o WHERE o.kind = 'trigger' "
            "AND o.resolved_by_event_id IS NULL ORDER BY o.occurred_at",
        )
    ]


def screens(st: base.Station) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for who in ("crew_a", "crew_b"):
        items = st.client.get("/v1/warnings", headers=st.headers(who)).json()["items"]
        seen[who] = [i["level"] for i in items if i["definition_id"] == "radiation_storm_shelter"]
    return seen


@pytest.mark.trace("T-E24-118", "T-E24-119", "T-E24-075", "T-E24-035", "T-E24-037")
def test_a_delayed_all_clear_keeps_the_newer_alert_across_restarts(patched: None, tmp_path: Path) -> None:
    st = build(tmp_path)
    st.initialize("SIM")
    st.advance(hours=2)  # every event below occurred in the past hour; delivery order is not occurrence order
    record(st, ALERT, 0, "evt_a")
    ((wid, level, state, _),) = storm(st)
    assert (level, state) == ("emergency", "raised")  # D2: the storm shelter call is Emergency
    assert screens(st) == {"crew_a": ["emergency"], "crew_b": ["emergency"]}
    restart(st)
    record(st, ALERT, 20, "evt_b")
    assert storm(st) == [(wid, "emergency", "raised", None)] and len(outstanding(st)) == 2
    restart(st)
    record(st, ALL_CLEAR, 10, "evt_c_delayed")  # occurred 10:10, delivered last
    assert storm(st) == [(wid, "emergency", "raised", None)]
    left = outstanding(st)
    assert len(left) == 1 and "T10:20" in left[0]  # the newer cause stays outstanding
    restart(st)
    for key, event_type, minutes in (
        ("evt_a", ALERT, 0),
        ("evt_b", ALERT, 20),
        ("evt_c_delayed", ALL_CLEAR, 10),
    ):
        record(st, event_type, minutes, key)  # the same commands retried after the restart: nothing changes
    record(st, ALERT, 5, "evt_late_old")  # a new alert that occurred before the recorded 10:10 all-clear
    assert storm(st) == [(wid, "emergency", "raised", None)] and len(outstanding(st)) == 1
    assert screens(st) == {"crew_a": ["emergency"], "crew_b": ["emergency"]}
    restart(st)
    record(st, ALL_CLEAR, 30, "evt_d")
    assert storm(st) == [(wid, "emergency", "cleared", "clear_event")]
    assert screens(st) == {"crew_a": [], "crew_b": []}
