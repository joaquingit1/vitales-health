"""T-E24-110 (V69 warning part): a whole mission day with the assistant model stopped.

The station has no model worker at all. Rule and sensor warnings raise and clear on their own, nothing
creates an insight, and an assistant warning that already existed expires on its own clock. Time moves only
through the station's injected clock; every input enters the real hub the way its producer's does.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
import rules_harness as h
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.uow import Actor
from crew_health.mission.application.ports import MissionEventRecorded
from crew_health.rules.application.ports import InsightWarningPort
from crew_health.rules.domain.inputs import EvidenceKey, InsightDraft, RecordFacts
from crew_health.rules.domain.vocabulary import Level, OriginMode
from e24_station import RECORD_FACTS, build, edited_documents, post, replaced

pytestmark = pytest.mark.E24

DAY_S = 86400
DWELL_S = 600  # sensor-limits `clear.dwell_seconds` of o2_high_act, read from the release below


def rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def states(st: base.Station) -> dict[str, tuple[str, str | None]]:
    found = rows(st, "SELECT definition_id, state, clear_reason FROM rules_warning ORDER BY raised_at")
    return {d: (s, r) for d, s, r in found}


def event(st: base.Station, event_type: str, event_id: str) -> MissionEventRecorded:
    return MissionEventRecorded(
        event_id, event_type, event_type, st.clock.at, None, st.clock.at, "SIM", "station_leo", (), None
    )


def readings(st: base.Station, first: int, value: float, count: int) -> list[Any]:
    return [
        base.reading("ch_o2", first + n, value, "mmHg", st.clock.at - timedelta(seconds=5 * (count - n)))
        for n in range(count)
    ]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Finding (R-E23-2): oxygen has no sourced plausibility criteria, so E23 marks every o2 reading "
        "unavailable and never evaluates it; oxygen_high_act can neither raise nor clear from real readings. "
        "Fail closed for clearing, but an oxygen hazard goes unwarned. Passes again once o2 criteria exist."
    ),
)
def test_a_mission_day_without_the_model_raises_clears_and_expires_by_rule(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    edited_documents(
        monkeypatch, lambda v: replaced(v, ConfigName.ASSISTANT_POLICY, h.example_value("assistant-policy"))
    )
    st = build(tmp_path, registry={"channels": [base.channel("ch_o2", "o2", "oxygen", "SIM", "mmHg")]})
    st.initialize("SIM", "station_leo")
    st.advance(seconds=3600)
    limits = json.loads((Path(__file__).resolve().parents[3] / "config/sensor-limits.json").read_text())
    clear = next(x for x in limits["limits"] if x["limit_id"] == "o2_high_act")["clear"]
    assert clear["dwell_seconds"] == DWELL_S

    key = EvidenceKey("observation", "obs_day")
    RECORD_FACTS.rows[key] = RecordFacts(
        True, "per_a", OriginMode.SIM, False, "ok", "accepted", st.clock.at, "pulse"
    )
    draft = InsightDraft(
        "ins_day", "per_a", "sleep_reaction", Level.WATCH, "Short sleep", "You slept less.",
        "From your records.", "Short sleep", ("Go to bed earlier.",), "eyes", (key,), (), False, (),
        OriginMode.SIM, st.clock.at, "model_1",
    )  # fmt: skip
    started = st.clock.at
    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        assert st.resolver().require(InsightWarningPort).validate_and_raise(uow, draft) is not None

    st.advance(hours=1)  # a storm: Emergency by rule, then its all-clear
    with st.begin() as uow:
        uow.publish(event(st, "space_weather_alert", "evt_alert"))
    assert states(st)["radiation_storm_shelter"] == ("raised", None)
    st.advance(hours=1)
    with st.begin() as uow:
        uow.publish(event(st, "space_weather_all_clear", "evt_clear"))
    assert states(st)["radiation_storm_shelter"] == ("cleared", "clear_event")

    st.advance(hours=1)  # an oxygen excursion, then fresh readings back in range for a full dwell
    assert post(st, readings(st, 1, 400.0, 1), "high").status_code == 200
    assert states(st)["oxygen_high_act"] == ("raised", None)
    sequence = 2
    for minute in range(DWELL_S // 60 + 2):
        st.advance(seconds=60)
        assert post(st, readings(st, sequence, 200.0, 12), f"ok_{minute}").status_code == 200
        sequence += 12
    assert states(st)["oxygen_high_act"] == ("cleared", "condition_false")

    st.advance(seconds=DAY_S - (st.clock.at - started).total_seconds() + 10)  # the day ends
    st.dispatch()
    assert states(st)["assistant_pattern"] == ("cleared", "expired")
    assert rows(st, "SELECT count(*) FROM rules_warning WHERE raised_by = 'assistant'") == [
        (1,)
    ]  # no new insight
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(0,)]
