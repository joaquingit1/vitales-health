"""The assistant never changes safety state, through the served hub (AGENTS 1 rule 4, E24 4.2, 4.6, H1, H2).

A real station (create_app over real SQLite, real mission, sensors and rules modules) raises the CO2 act warning
from real readings. Then the assistant tries every way in: `actOnWarning` over HTTP with an assistant actor inside
a crew member's session (the shape E26's assistant flow uses), and `AssistantWarningGuard` with every level and
every action. Each attempt is refused, the warning row is byte for byte unchanged and each guard refusal is
audited. The domain half of the same rule is `test_assistant_safety.py`.

T-E24-004, 005, 062, 003; INV-E24-02, INV-E24-03; H1, H2.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from test_station_co2 import act, station

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.rules.application.ports import Allowed, AssistantContext, AssistantWarningGuard, Rejected
from crew_health.rules.domain.vocabulary import ASSISTANT_LEVELS, Level, WarningAction

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-02", "INV-E24-03", "H1", "H2")]

ROW = "SELECT revision, state, level, body_json FROM rules_warning WHERE warning_id = ?"


def _rows(st: base.Station, sql: str, *args: object) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def _co2(st: base.Station) -> str:
    found = _rows(st, "SELECT warning_id FROM rules_warning WHERE definition_id = 'co2_above_limit'")
    assert len(found) == 1
    return str(found[0][0])


def _as_assistant(st: base.Station) -> None:
    """The assistant acting inside crew member per_a's session: the session kind is crew, the actor is not."""
    st.security.tokens["assistant"] = SessionContext(
        "s_bot", "crew", Actor("assistant", "assistant_stub"), "per_a", frozenset({Role.CREW})
    )


BODIES: dict[WarningAction, dict[str, str]] = {
    WarningAction.ACKNOWLEDGE: {"action": "acknowledge"},
    WarningAction.ADD_NOTE: {"action": "add_note", "note": "the model says it is fine"},
    WarningAction.RESOLVE: {"action": "resolve", "reason": "the model says it is fine"},
    WarningAction.DISMISS: {"action": "dismiss", "reason": "the model says it is fine"},
}


@pytest.mark.trace("T-E24-004", "T-E24-005", "T-E24-062")
def test_the_assistant_cannot_act_on_a_sensor_warning_over_http(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    _as_assistant(st)
    wid = _co2(st)
    before = _rows(st, ROW, wid)
    seen = st.client.get(f"/v1/warnings/{wid}", headers=st.headers("assistant"))
    assert seen.status_code == 200 and seen.json()["allowed_actions"] == []
    assert set(BODIES) == set(WarningAction)  # every action of the contract enum is tried
    for n, (action, body) in enumerate(sorted(BODIES.items())):
        refused = act(st, "assistant", wid, body, f"bot_{n}", '"1"')
        assert refused.status_code == 403, (action, refused.text)
        assert refused.json()["code"] == "ACTION_NOT_ALLOWED", action
    assert _rows(st, ROW, wid) == before
    person = act(st, "crew_a", wid, BODIES[WarningAction.ACKNOWLEDGE], "per_a_1", '"1"')
    assert (
        person.status_code == 200
    )  # the same person's own session still acknowledges: only the assistant is barred


@pytest.mark.trace("T-E24-003", "T-E24-062")
def test_the_guard_allows_info_and_watch_only_and_no_action_at_all(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    wid = _co2(st)
    before = _rows(st, ROW, wid)
    guard = st.resolver().require(AssistantWarningGuard)
    ctx = AssistantContext("assistant_stub", "per_a")
    audited_before = _rows(st, "SELECT count(*) FROM platform_audit")[0][0]
    refusals = 0
    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        for level in [*(lv.value for lv in Level), "ACT", "Emergency", "", "critical"]:
            got = guard.propose_level(uow, ctx, level, "sleep_reaction")
            if level in {lv.value for lv in ASSISTANT_LEVELS}:
                assert isinstance(got, Allowed) and got.level == level
            else:
                assert isinstance(got, Rejected) and got.code == "LEVEL_NOT_ALLOWED", level
                refusals += 1
        for action in [*(a.value for a in WarningAction), "clear", "lower", "silence"]:
            refused = guard.propose_action(uow, ctx, wid, action)
            assert isinstance(refused, Rejected) and refused.code == "ACTION_NOT_ALLOWED"
            refusals += 1
    assert _rows(st, ROW, wid) == before
    assert _rows(st, "SELECT count(*) FROM platform_audit")[0][0] - audited_before >= refusals
