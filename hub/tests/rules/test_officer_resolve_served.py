"""D21 on the served hub: after a release edits a warning's definition, the crew medical officer resolves it with a
reason and the record names the role; a crew member without the role cannot (E24 3.4, D1, D21). The retried
command replays from a cache that holds no private text (AGENTS 6).

Without this, a warning whose definition was edited or withdrawn could stay active forever: its pinned rule may
never clear it, and nobody would hold the authority to resolve it.

T-E24-016, T-E24-050; D1, D21.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import activate_release, replaced
from test_station_co2 import act, station

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ConfigDocumentView

pytestmark = [pytest.mark.E24, pytest.mark.trace("D1", "D21")]


def _rows(st: base.Station, sql: str, *args: object) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def _retitle_co2(views: dict[ConfigName, ConfigDocumentView]) -> None:
    doc = copy.deepcopy(json.loads(views[ConfigName.WARNING_RULES].raw))
    (co2,) = [d for d in doc["definitions"] if d["id"] == "co2_above_limit"]
    co2["content"]["title"] = co2["content"]["title"] + " (edited)"
    replaced(views, ConfigName.WARNING_RULES, doc)


def _allowed(st: base.Station, who: str, wid: str) -> tuple[list[str], str]:
    got = st.client.get(f"/v1/warnings/{wid}", headers=st.headers(who))
    assert got.status_code == 200, got.text
    return list(got.json()["allowed_actions"]), got.headers["ETag"]


@pytest.mark.trace("T-E24-016", "T-E24-050")
def test_after_a_definition_edit_the_officer_resolves_with_a_reason_and_a_crew_member_cannot(
    patched: None, tmp_path: Path
) -> None:
    st = station(tmp_path)
    ((wid,),) = _rows(st, "SELECT warning_id FROM rules_warning WHERE definition_id = 'co2_above_limit'")
    before, _ = _allowed(st, "cmo", wid)
    assert "resolve" not in before  # current definition: the pinned rule clears it, nobody resolves
    activate_release(st, "release_two", _retitle_co2)
    assert _rows(st, "SELECT state, config_release_id FROM rules_warning WHERE warning_id = ?", wid) == [
        ("raised", "release_e23")
    ]  # D1: the new release neither clears nor repins it
    crew, etag = _allowed(st, "crew_b", wid)
    assert "resolve" not in crew
    refused = act(st, "crew_b", wid, {"action": "resolve", "reason": "looks fine"}, "r_crew", etag)
    assert refused.status_code == 403, refused.text
    officer, etag = _allowed(st, "cmo", wid)
    assert "resolve" in officer
    done = act(st, "cmo", wid, {"action": "resolve", "reason": "cabin CO2 confirmed normal"}, "r_cmo", etag)
    assert done.status_code == 200, done.text
    ((state, body),) = _rows(st, "SELECT state, body_json FROM rules_warning WHERE warning_id = ?", wid)
    resolved = json.loads(body)["resolved"]
    assert state == "resolved"
    assert (resolved["role"], resolved["reason"]) == ("crew_medical_officer", "cabin CO2 confirmed normal")
    # m4: the idempotency cache keeps only the revision, never the reason or the projection (AGENTS 6), and the
    # retried command still returns the same response, re-projected.
    cached = _rows(st, "SELECT body_json FROM rules_action_result WHERE warning_id = ?", wid)
    assert [json.loads(b) for (b,) in cached] == [{"revision": done.json()["revision"]}]
    again = act(st, "cmo", wid, {"action": "resolve", "reason": "cabin CO2 confirmed normal"}, "r_cmo", etag)
    assert again.status_code == 200 and again.content == done.content
