"""E24 surface on a real station: the HTTP operations, hold and attention, replicas and the operator console.

Warnings are raised the way their producers raise them: a mission event, an accepted answer and one oxygen reading
published or posted into the real hub. Nothing here constructs a warning by hand.
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import build, post, seconds

from crew_health.app.cli import main as hub_main
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext
from crew_health.mission.application.ports import (
    MissionEventRecorded,
    MissionStatePort,
    OverlayFact,
    OverlayPort,
)
from crew_health.records.application.ports import AnswerAcceptedFact
from crew_health.rules import cli as rules_cli
from crew_health.rules.application import core
from crew_health.rules.application.presentation import Presenter
from crew_health.rules.application.replication import ReplicatedWarning, apply_replica
from crew_health.rules.application.stores import PendingStore
from crew_health.rules.domain.errors import InvariantViolation

pytestmark = pytest.mark.E24


def rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def lunar(tmp: Path, environment: str = "lunar_surface") -> base.Station:
    st = build(tmp, registry={"channels": [base.channel("ch_o2", "o2", "oxygen", "SIM", "mmHg")]})
    st.initialize("SIM", environment)
    st.advance(seconds=3600)
    return st


def publish(st: base.Station, *events: object) -> None:
    with st.begin() as uow:
        for event in events:
            uow.publish(event)  # type: ignore[arg-type]


def landing(st: base.Station) -> MissionEventRecorded:
    at = st.clock.at - timedelta(hours=1)
    return MissionEventRecorded(
        "evt_land", "landing", "Landing", at, None, at, "SIM", "lunar_surface", ("per_a",), None
    )


def dizzy(st: base.Station, answer_id: str = "ans_1") -> AnswerAcceptedFact:
    return AnswerAcceptedFact(answer_id, "per_a", "dizzy_standing", "present", st.clock.at, "SIM", "mis_1")


def storm(st: base.Station, environment: str = "lunar_surface") -> MissionEventRecorded:
    return MissionEventRecorded(
        "evt_storm",
        "space_weather_alert",
        "Alert",
        st.clock.at,
        None,
        st.clock.at,
        "SIM",
        environment,
        (),
        None,
    )


def oxygen_high(st: base.Station) -> None:
    sent = post(st, [base.reading("ch_o2", 1, 400.0, "mmHg", seconds(st, 5))], "o2_1")
    assert sent.status_code == 200, sent.text


def one(st: base.Station, definition: str) -> dict[str, Any]:
    items = st.client.get("/v1/warnings?state=all", headers=st.headers("crew_a")).json()["items"]
    return next(i for i in items if i["definition_id"] == definition)


def act(st: base.Station, who: str, wid: str, body: object, key: str, **headers: str) -> Any:
    sent = body if isinstance(body, bytes) else json.dumps(body).encode()
    return st.client.post(
        f"/v1/warnings/{wid}/actions",
        content=sent,
        headers={**st.headers(who, key), "Content-Type": "application/json", **headers},
    )


# --- the three operations over HTTP (6, T-E24-051, 054, 059, 133) -----------------------------------


@pytest.mark.trace("T-E24-051", "T-E24-052", "T-E24-053")
def test_a_person_warning_is_that_persons_alone_and_a_habitat_warning_is_every_crew_screens(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path)
    publish(st, landing(st), dizzy(st), storm(st))
    mine = one(st, "dizzy_after_landing")
    theirs = st.client.get("/v1/warnings", headers=st.headers("crew_b")).json()["items"]
    assert {i["definition_id"] for i in theirs} == {"radiation_storm_shelter"}  # crew scope reaches everyone
    assert (
        st.client.get(f"/v1/warnings/{mine['warning_id']}", headers=st.headers("crew_b")).status_code == 404
    )
    assert (
        st.client.get(f"/v1/warnings/{mine['warning_id']}", headers=st.headers("crew_a")).status_code == 200
    )
    station = st.client.get("/v1/warnings", headers=st.headers("operator")).json()["items"]
    assert [i["definition_id"] for i in station] == ["radiation_storm_shelter"]
    assert station[0]["allowed_actions"] == []  # a role without crew actions sees no action (3.10)


@pytest.mark.trace("T-E24-059", "T-E24-133", "T-E24-050")
def test_resolve_note_and_the_documented_failures(patched: None, tmp_path: Path) -> None:
    st = lunar(tmp_path)
    publish(st, landing(st), dizzy(st))
    w = one(st, "dizzy_after_landing")
    wid = w["warning_id"]
    assert set(w["allowed_actions"]) == {"acknowledge", "add_note", "resolve"}
    assert (
        w["headline"]["fragment"] == "day 1: getting used to gravity"
    )  # `{day_short}` from the mission's labels
    etag = '"1"'
    assert act(st, "crew_a", wid, b"not json", "k1", **{"If-Match": etag}).status_code == 400
    assert act(st, "crew_a", wid, {"action": "explode"}, "k2", **{"If-Match": etag}).status_code == 422
    assert act(st, "crew_a", wid, {"action": "add_note"}, "k3", **{"If-Match": etag}).json()["field_errors"]
    too_long = {"action": "add_note", "note": "x" * 2001}
    assert act(st, "crew_a", wid, too_long, "k4", **{"If-Match": etag}).status_code == 422
    assert act(st, "crew_a", wid, {"action": "acknowledge"}, "k5", **{"If-Match": "nope"}).status_code == 400
    noted = act(
        st, "crew_a", wid, {"action": "add_note", "note": "feeling better"}, "k6", **{"If-Match": etag}
    )
    assert noted.status_code == 200 and noted.json()["notes"][0]["text"] == "feeling better"
    assert noted.headers["ETag"] == '"2"'
    stale_release = act(
        st, "crew_a", wid, {"action": "resolve", "reason": "ok"}, "k7", **{"If-Match": '"2"', "Config-Release": "rel_old"}
    )  # fmt: skip
    assert stale_release.status_code == 409 and stale_release.json()["code"] == "CONFIG_VERSION_MISMATCH"
    ack = act(
        st, "crew_a", wid, {"action": "acknowledge"}, "k8", **{"If-Match": '"2"', "Config-Release": "rel_old"}
    )
    assert ack.status_code == 200  # acknowledge reads no configuration (4.1 step 7)
    done = act(
        st, "crew_a", wid, {"action": "resolve", "reason": "stood up, fine"}, "k9", **{"If-Match": '"3"'}
    )
    assert done.status_code == 200 and done.json()["state"] == "resolved"
    assert done.json()["resolved"]["reason"] == "stood up, fine"
    again = act(st, "crew_a", wid, {"action": "add_note", "note": "late"}, "k10", **{"If-Match": '"4"'})
    assert again.status_code == 409 and again.json()["code"] == "INVALID_TRANSITION"
    publish(st, dizzy(st))  # the same answer delivered again never raises a resolved warning (T-E24-037)
    assert len(rows(st, "SELECT 1 FROM rules_warning WHERE definition_id = 'dizzy_after_landing'")) == 1


@pytest.mark.trace("T-E24-054")
def test_listing_pages_filters_and_malformed_queries(patched: None, tmp_path: Path) -> None:
    st = lunar(tmp_path)
    publish(st, landing(st), dizzy(st), storm(st))
    head = {"headers": st.headers("crew_a")}
    first = st.client.get("/v1/warnings?limit=1", **head).json()
    assert len(first["items"]) == 1 and first["page"]["next_cursor"]
    second = st.client.get(f"/v1/warnings?limit=1&cursor={first['page']['next_cursor']}", **head).json()
    assert second["items"][0]["warning_id"] != first["items"][0]["warning_id"]
    assert second["page"]["next_cursor"] is None
    for bad in ("state=bogus", "limit=0", "limit=abc", "limit=201", "cursor=!!!", "cursor=e30"):
        assert st.client.get("/v1/warnings?" + bad, **head).status_code == 400, bad
    assert st.client.get("/v1/warnings", headers={}).status_code == 401


@pytest.mark.trace("T-E24-050")
def test_an_oxygen_reading_raises_an_act_warning_that_the_station_never_acts_on(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path, "station_leo")
    oxygen_high(st)
    w = one(st, "oxygen_high_act")
    assert (w["level"], w["state"], w["scope"]) == ("act", "raised", "habitat")
    refused = act(st, "operator", w["warning_id"], {"action": "acknowledge"}, "o1", **{"If-Match": '"1"'})
    assert refused.status_code == 403 and refused.json()["code"] == "UNAUTHORIZED_SCOPE"


# --- hold and attention (3.12, V74), off the wire until R-E24-1 --------------------------------------


class Overlays:
    """The overlay owner is the mission module; this port fake stands in for a person's recorded sleep."""

    def __init__(self, found: Sequence[OverlayFact] | Exception) -> None:
        self.found = found

    def active(self, person_ids: Any, at: Any) -> Any:
        return {}

    def intervals(
        self, person_id: str, kinds: Any, since: Any, until: Any, limit: int
    ) -> Sequence[OverlayFact]:
        assert "sleep" in kinds and "eva" in kinds  # the suppressed overlays come from routine-rules
        if isinstance(self.found, Exception):
            raise self.found
        return [f for f in self.found if f.person_id == person_id]


def presenter(st: base.Station, overlays: OverlayPort | None) -> Presenter:
    rt = core.Runtime(
        settings=st.db.settings,
        ids=st.db.ids,
        mission=st.resolver().optional(MissionStatePort),
        binding=None,
        health=None,
    )
    return Presenter(rt, st.resolver().require(ReadSnapshotFactory), overlays)


def sleep_from(start: datetime, person: str = "per_a") -> OverlayFact:
    return OverlayFact("ov_1", person, "sleep", "active", None, start, None)


def session(st: base.Station, who: str) -> SessionContext:
    return st.security.tokens[who]


@pytest.mark.trace("T-E24-080", "T-E24-081", "T-E24-099")
def test_a_sleeper_holds_the_act_warning_but_the_storm_still_interrupts_and_the_station_is_never_held(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path, "station_leo")
    started = st.clock.at - timedelta(hours=1)
    publish(st, storm(st, "station_leo"))
    oxygen_high(st)
    asleep = presenter(st, Overlays([sleep_from(started)]))
    shown = asleep.for_session(session(st, "crew_a"))
    act_id = one(st, "oxygen_high_act")["warning_id"]
    storm_id = one(st, "radiation_storm_shelter")["warning_id"]
    assert set(shown.held) == {act_id}  # emergency is never held
    assert shown.attention[storm_id].interrupt and shown.attention[storm_id].order == 0
    assert not shown.attention[act_id].interrupt  # held: not shown now
    assert shown.emergencies == (storm_id,) and shown.banner == ()
    assert shown.digests[0].warning_ids == (act_id,)
    awake = presenter(st, Overlays([])).for_session(session(st, "crew_a"))
    assert awake.held == {} and awake.banner == (act_id,)
    assert presenter(st, None).for_session(session(st, "crew_a")).held == {}  # no provider: more interruption
    habitat = asleep.for_session(session(st, "operator"))
    assert habitat.held == {} and habitat.digests == ()  # a non-person session is never held


@pytest.mark.trace("T-E24-081")
def test_too_many_overlays_hold_nothing_and_other_faults_are_not_hidden(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path, "station_leo")
    oxygen_high(st)
    big = ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "overlay rows above the bound")
    assert presenter(st, Overlays(big)).for_session(session(st, "crew_a")).held == {}
    with pytest.raises(ProblemError):
        presenter(st, Overlays(ProblemError(ErrorCode.INTERNAL_ERROR, "boom"))).for_session(
            session(st, "crew_a")
        )


@pytest.mark.trace("T-E24-080")
def test_acknowledging_removes_the_act_warning_from_this_persons_banner_only(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path, "station_leo")
    oxygen_high(st)
    wid = one(st, "oxygen_high_act")["warning_id"]
    assert act(st, "crew_a", wid, {"action": "acknowledge"}, "a1", **{"If-Match": '"1"'}).status_code == 200
    quiet = presenter(st, None)
    assert quiet.for_session(session(st, "crew_a")).banner == ()
    assert quiet.for_session(session(st, "crew_b")).banner == (wid,)
    assert rows(st, f"SELECT state FROM rules_warning WHERE warning_id = '{wid}'") == [("acknowledged",)]


# --- replicas (3.15, INV-E24-18) --------------------------------------------------------------------


@pytest.mark.trace("T-E24-125")
def test_a_replica_is_listed_with_its_origin_and_age_and_never_touched_by_the_engine(
    patched: None, tmp_path: Path
) -> None:
    st = lunar(tmp_path, "station_leo")
    oxygen_high(st)
    local_before = rows(st, "SELECT warning_id, state, revision FROM rules_warning")
    rt = core.Runtime(
        settings=st.db.settings,
        ids=st.db.ids,
        mission=st.resolver().optional(MissionStatePort),
        binding=None,
        health=None,
    )
    origin_at = st.clock.at - timedelta(minutes=5)
    crew = ReplicatedWarning(
        "wrn_rover_1", "node_rover", origin_at, 1, "crew", None, json.dumps({"title": "Storm"})
    )
    mine = ReplicatedWarning(
        "wrn_rover_2", "node_rover", origin_at, 1, "person", "per_a", json.dumps({"title": "x"})
    )
    with st.begin() as uow:
        assert apply_replica(rt, uow, crew) and apply_replica(rt, uow, mine)
        assert not apply_replica(rt, uow, crew)  # a repeated delivery changes nothing
        old = ReplicatedWarning("wrn_rover_1", "node_rover", origin_at, 0, "crew", None, "{}")
        assert not apply_replica(rt, uow, old)  # an older version never replaces a newer one
        with pytest.raises(InvariantViolation):
            apply_replica(
                rt, uow, ReplicatedWarning("w", "node_1", origin_at, 1, "crew", None, "{}")
            )  # own node
        with pytest.raises(InvariantViolation):
            apply_replica(rt, uow, ReplicatedWarning("w", "node_rover", origin_at, 1, "person", None, "{}"))
    shown = presenter(st, None).replicas(session(st, "crew_b"))
    assert [(r.warning_id, r.origin_node_id, r.age_seconds) for r in shown] == [
        ("wrn_rover_1", "node_rover", 300)
    ]
    own = presenter(st, None).replicas(session(st, "crew_a"))
    assert {r.warning_id for r in own} == {"wrn_rover_1", "wrn_rover_2"}  # the person's own replica is theirs
    assert (
        rows(st, "SELECT warning_id, state, revision FROM rules_warning") == local_before
    )  # engine untouched


# --- the operator console (5.5) ---------------------------------------------------------------------


def console(who: str = "operator_sim") -> tuple[rules_cli.Console, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return rules_cli.Console(out, err, who, "cli_1"), out, err


def fault_a_storm(st: base.Station, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(core.engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    publish(st, storm(st))
    return str(rows(st, "SELECT pending_id FROM rules_pending")[0][0])


@pytest.mark.trace("T-E24-122")
def test_pending_lists_rows_and_quarantine_needs_a_stuck_row_and_is_audited(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = lunar(tmp_path)
    pending_id = fault_a_storm(st, monkeypatch)
    cons, out, err = console()
    assert rules_cli.run(["pending"], st.db, st.db.ids, cons, limit=100) == 0
    [line] = [json.loads(x) for x in out.getvalue().splitlines()]
    assert (line["pending_id"], line["state"], line["attempts"]) == (pending_id, "pending", 0)
    cons, out, err = console()
    assert (
        rules_cli.run(["pending", "--quarantine", pending_id], st.db, st.db.ids, cons, limit=100) == 1
    )  # not stuck yet
    assert json.loads(err.getvalue()) == {"refused": "not_a_stuck_row"}
    with st.begin() as uow:
        PendingStore().failed(uow.connection, pending_id, "RuntimeError", None, stuck=True)
        assert PendingStore().blocking(uow.connection)  # a stuck row still freezes clears
    cons, out, err = console()
    assert rules_cli.run(["pending", "--quarantine", pending_id], st.db, st.db.ids, cons, limit=100) == 0
    assert json.loads(out.getvalue().splitlines()[0])["state"] == "quarantined"  # the row stays
    with st.begin() as uow:
        assert not PendingStore().blocking(uow.connection)  # clears resume
    assert rows(
        st,
        "SELECT count(*) FROM platform_audit WHERE instr(CAST(envelope AS TEXT), 'rules.pending_quarantined') > 0",
    ) == [(1,)]


@pytest.mark.trace("T-E24-122")
def test_console_usage_errors_exit_two_and_change_nothing(patched: None, tmp_path: Path) -> None:
    st = lunar(tmp_path)
    cons, out, err = console()
    assert rules_cli.run([], st.db, st.db.ids, cons, limit=100) == 2
    assert rules_cli.run(["pending", "--bogus"], st.db, st.db.ids, cons, limit=100) == 2
    assert "usage" in err.getvalue() and out.getvalue() == ""
    assert hub_main(["rules"]) == 2  # `crew-health rules` without a database is unusable input
