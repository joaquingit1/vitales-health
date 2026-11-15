"""E29 cross-module scenarios using help's real application and the real platform for transport and catch-up.

E01 durable event delivery is out of scope: committed help ground_message.changed events are fed explicitly
into the two registered durable handlers. No transport or help lifecycle logic is replaced.

Blocked scenarios, with no tests here: E29-SILENCE needs E24's rules (groundlink's side of V93 and D35 is
proved in hub/tests/groundlink/test_ground_silence_consumer.py); E29-OFFLINE needs E20 and E26's client cache.
Plan, warning and routine byte comparisons need E28, E24 and E25, which are not on this main checkout.
E16 handoff facts and FHIR export are outside E29. Help snapshots carry no plan_run_id, so catch-up reports
unknown watch and advice facts rather than inventing them; the helping line comes from help's snapshot. The
independence evidence is kept at eval/results/E29/independence.json.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from catchup.support import ROOT, Harness, Mission, feed, validate_document
from crew_health.contracts_gen.enums import EventType
from crew_health.contracts_gen.schemas.ground_log_view_schema import GroundLogView
from crew_health.groundlink.application.ports import InboundItem
from crew_health.help.application.ports import (
    CatchUpLines,
    GroundLogComposer,
    HelpLogSnapshotView,
    LinkSnapshot,
)
from help_support import (
    GROUND,
    Driver,
    Providers,
    Roster,
    build,
    drive_eye,
    headers,
    json_of,
    mission_clock,
    num,
)
from help_support import (
    Harness as HelpHarness,
)

MOON_START = datetime(2026, 11, 20, tzinfo=UTC)
MOON_END = datetime(2026, 12, 3, 15, 51, 35, tzinfo=UTC)


class ComposerBinding:
    """Break the wiring cycle, then delegate every call to the registered real catch-up composer."""

    target: GroundLogComposer | None = None

    def link(self) -> LinkSnapshot:
        if self.target is None:
            raise RuntimeError("composer not bound")
        return self.target.link()

    def for_help(self, snapshot: HelpLogSnapshotView) -> CatchUpLines:
        if self.target is None:
            raise RuntimeError("composer not bound")
        return self.target.for_help(snapshot)


@dataclass
class Station:
    help: HelpHarness
    transport: Harness
    driver: Driver
    consumed: int = 0

    def at(self, at: datetime) -> None:
        self.help.at(at)
        self.transport.clock.at = at

    def relay(self) -> None:
        events = self.help.store.events[self.consumed :]
        self.consumed = len(self.help.store.events)
        for event in events:
            if event.type == EventType.GROUND_MESSAGE_CHANGED:
                feed(self.transport, event)

    def press(self, sid: object) -> dict[str, object]:
        response = self.help.client.post(
            f"/v1/help-sessions/{sid}/ground-messages", json={}, headers=headers("mara", self.driver.key())
        )
        assert response.status_code == 201, response.text
        self.relay()
        return json_of(response)

    def log(self) -> dict[str, object]:
        response = self.transport.client.get("/v1/ground/log", headers={"role": "ground"})
        assert response.status_code == 200, response.text
        GroundLogView.model_validate(response.json())
        return json_of(response)

    def message(self, gid: object, who: str = "mara") -> dict[str, object]:
        response = self.help.client.get(f"/v1/ground-messages/{gid}", headers=headers(who))
        assert response.status_code == 200, response.text
        return json_of(response)


def obj(data: dict[str, object], name: str) -> dict[str, object]:
    value = data[name]
    if not isinstance(value, dict):
        raise TypeError(f"{name} is not an object")
    return value


def entries(data: dict[str, object], name: str) -> list[dict[str, object]]:
    value = data[name]
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise TypeError(f"{name} is not a list of objects")
    return value


def station(path: Path, environment: str = "lunar_surface", at: datetime = MOON_START) -> Station:
    proxy = ComposerBinding()
    mission = Mission()
    mission.facts.active_environment_id = environment
    clock = mission_clock()
    help_harness = build(
        path / "help.sqlite", Providers(composer=proxy, mission_clock=clock, roster=Roster(), mission=mission)
    )
    transport_path = path / "transport"
    transport_path.mkdir()
    transport = Harness(
        transport_path,
        environment=environment,
        lifecycle=help_harness.service.lifecycle,
        help_snapshots=help_harness.service.snapshots,
        config_reader=help_harness.config,
        at=at,
        port_timeout_ms=500,
    )
    proxy.target = transport.composer
    return Station(help_harness, transport, Driver(help_harness))


def eye(s: Station, at: datetime) -> dict[str, object]:
    session = drive_eye(s.driver)
    s.at(at)
    return session


def switch(s: Station, body: dict[str, object]) -> dict[str, object]:
    response = s.transport.client.post(
        "/v1/demo/ground-link", json=body, headers={"role": "operator", "Idempotency-Key": s.driver.key()}
    )
    assert response.status_code == 200, response.text
    return json_of(response)


@pytest.mark.trace("E29-OUTAGE", "R61", "E29-04", "D40", "D42")
def test_e29_outage_preview_press_care_continues_and_long_backlog_drains_in_order(tmp_path: Path) -> None:
    s = station(tmp_path)
    session = eye(s, MOON_START + timedelta(hours=12, minutes=5, seconds=30))
    sid = session["help_session_id"]
    preview = s.help.client.get(f"/v1/help-sessions/{sid}/ground-message-preview", headers=headers("mara"))
    assert preview.status_code == 200, preview.text
    shown = json_of(preview)
    assert shown["leaves_at"] == "2026-12-03T15:51:35Z"
    assert shown["status_text"] == "Waiting for the next contact in 315 hours. Nothing here waits for it."
    assert shown["persisted"] is False
    assert obj(shown, "catch_up")["being_watched"] == ["Unknown"]
    pressed = s.press(sid)
    gid = pressed["ground_message_id"]
    assert pressed["state"] == "queued"
    assert pressed["text_sha256"] == shown["text_sha256"]
    approved = s.message(gid)["catch_up"]
    assert approved == shown["catch_up"]
    current = json_of(s.driver.get("mara", sid))
    ticked = s.driver.ok("mara", current, "tick_step", step_index=4)
    assert num(ticked, "revision") == num(current, "revision") + 1
    # Each press has a new session revision, and keeps the real help session moving.
    queued = [str(gid)]
    for _ in range(39):
        s.at(s.help.clock.at + timedelta(seconds=1))
        queued.append(str(s.press(sid)["ground_message_id"]))
    for _ in range(5):
        assert s.transport.runtime.agent.run_once().sent == 0
    assert s.log()["items"] == []
    s.at(MOON_END)
    agent = s.transport.runtime.agent
    sent = []
    for _ in range(3):
        sent.append(agent.run_once().sent)
    assert sent == [17, 17, 6]
    s.at(MOON_END + timedelta(seconds=10))
    assert agent.run_once().arrived == 31
    assert agent.run_once().arrived == 9
    items = s.log()["items"]
    assert isinstance(items, list)
    assert len(items) == 40
    # When deliveries share an instant, the contract's stable tie-break is ref_id.
    assert [item["ref_id"] for item in items] == sorted(queued)
    first = next(item for item in items if item["ref_id"] == gid)
    assert first["catch_up"] == approved
    assert s.message(gid, "ground")["state"] == "sent"
    sent_order = []
    for event in s.help.store.events:
        if event.type == EventType.GROUND_MESSAGE_CHANGED and event.scope.kind == "crew":
            data = json.loads(event.data_json)
            if data["state"] == "sent" and data["ground_message_id"] not in sent_order:
                sent_order.append(data["ground_message_id"])
    assert sent_order == queued
    with s.transport.db.snapshot() as view:
        statuses = [s.transport.outbox.status(view, "ground_message", ref) for ref in queued]
    assert all(
        status is not None and status.attempts == 1 and status.state == "arrived" for status in statuses
    )


@pytest.mark.trace("E29-REPLY", "R61", "V95", "EI5")
def test_e29_reply_waits_deduplicates_and_only_appends_log_to_closed_help(tmp_path: Path) -> None:
    s = station(tmp_path)
    session = eye(s, MOON_END)
    gid = str(s.press(session["help_session_id"])["ground_message_id"])
    assert s.transport.runtime.agent.run_once().sent == 1
    s.at(MOON_END + timedelta(seconds=10))
    assert s.transport.runtime.agent.run_once().arrived == 1
    current = json_of(s.driver.get("mara", session["help_session_id"]))
    closed = s.driver.ok("mara", current, "close", disposition="handed_to_crew_medical_officer")
    before_response = s.driver.get("tomas", closed["help_session_id"])
    assert before_response.status_code == 200
    before = json_of(before_response)
    switch(s, {"status": "down", "gap_kind": "equipment"})
    item = InboundItem(
        "ground_reply_1", "ground_message", gid, "reply", s.help.clock.at, "Advice received.", GROUND.actor
    )
    assert s.transport.inbox.receive(item) == "accepted"
    assert s.transport.inbox.receive(item) == "duplicate"
    for _ in range(2):
        assert s.transport.runtime.agent.run_once().inbound == 0
    assert json_of(s.driver.get("tomas", closed["help_session_id"])) == before
    switch(s, {"use_schedule": True})
    assert s.transport.runtime.agent.run_once().inbound == 0
    s.at(s.help.clock.at + timedelta(seconds=10))
    assert s.transport.runtime.agent.run_once().inbound == 1
    assert s.transport.runtime.agent.run_once().inbound == 0
    assert (
        s.help.service.lifecycle.record_reply(
            gid, item.event_id, item.text or "", item.by, item.sent_by_ground_at, s.help.clock.at
        )
        == "duplicate"
    )
    after = json_of(s.driver.get("tomas", closed["help_session_id"]))
    for field in before:
        if field not in {"revision", "log"}:
            assert after[field] == before[field], field
    assert after["state"] == "closed"
    after_log, before_log = entries(after, "log"), entries(before, "log")
    assert after_log[:-1] == before_log
    assert after_log[-1]["text"] == "Ground: Advice received."
    assert obj(s.message(gid, "tomas"), "reply")["text"] == "Advice received."


@pytest.mark.trace("E29-INDEPENDENCE", "R48", "EI2", "V81", "D34")
def test_e29_independence_help_bytes_identical_and_ground_fields_only(tmp_path: Path) -> None:
    s = station(tmp_path)
    session = eye(s, MOON_START + timedelta(hours=12))
    sid = session["help_session_id"]
    gid = s.press(sid)["ground_message_id"]
    # Freeze before comparisons; all requests use the very same injected instant.
    s.message(gid)
    sessions: dict[str, bytes] = {}
    messages: dict[str, dict[str, object]] = {}
    for state in ("up", "delayed", "down"):
        body: dict[str, object] = {"status": state}
        if state == "delayed":
            body["one_way_delay_s"] = 1200
        if state == "down":
            body.update(
                gap_kind="planned_line_of_sight", gap_planned=True, next_contact_at="2026-12-03T15:51:35Z"
            )
        switch(s, body)
        response = s.driver.get("mara", sid)
        assert response.status_code == 200
        sessions[state] = response.content
        messages[state] = s.message(gid)
    assert sessions["up"] == sessions["delayed"] == sessions["down"]
    permitted = {"leaves_at", "status_text"}
    diffs = {
        state: sorted(key for key in messages["up"] if messages["up"][key] != message[key])
        for state, message in messages.items()
    }
    assert diffs["delayed"] == []
    assert set(diffs["down"]) == permitted
    # Kept evidence, like eval/results/*.json: overwritten on every run, read by the PR and the gate report.
    output = ROOT / "eval/results/E29/independence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "help_sha256": {state: hashlib.sha256(raw).hexdigest() for state, raw in sessions.items()},
                "ground_message_sha256": {
                    state: hashlib.sha256(json.dumps(wire, sort_keys=True).encode()).hexdigest()
                    for state, wire in messages.items()
                },
                "permitted_differences": sorted(permitted),
                "observed_differences": diffs,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"E29-INDEPENDENCE evidence: {output}")


@pytest.mark.trace("E29-LINK", "R49", "D33")
def test_e29_link_demo_switch_emits_once_schedule_restores_live_is_404(tmp_path: Path) -> None:
    s = station(tmp_path)
    body = {
        "status": "down",
        "gap_kind": "planned_line_of_sight",
        "gap_planned": True,
        "next_contact_at": "2026-12-03T15:51:35Z",
    }
    before = len([e for e in s.transport.events if e.type == EventType.GROUND_LINK_CHANGED])
    wire = switch(s, body)
    assert wire["source"] == "demo_switch"
    assert wire["gap_planned"] is True
    assert wire["next_contact_at"] == body["next_contact_at"]
    assert len([e for e in s.transport.events if e.type == EventType.GROUND_LINK_CHANGED]) == before + 1
    restored = switch(s, {"use_schedule": True})
    assert restored["source"] == "schedule"
    assert restored["status"] == "down"
    s.transport.mission.facts.mission_mode = "LIVE"
    response = s.transport.client.post(
        "/v1/demo/ground-link", json=body, headers={"role": "operator", "Idempotency-Key": "live-switch-001"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.trace("E29-OUTAGE", "R61", "E29-04", "R49")
def test_mars_conjunction_keeps_help_working_and_message_waiting(tmp_path: Path) -> None:
    at = datetime(2026, 1, 3, tzinfo=UTC)
    s = station(tmp_path, "mars_surface", at)
    session = eye(s, at)
    link = s.transport.composer.link()
    assert link.status == "down"
    assert link.contract["gap_kind"] == "conjunction"
    assert link.contract["gap_planned"] is True
    assert link.next_contact_at == datetime(2026, 1, 16, tzinfo=UTC)
    gid = s.press(session["help_session_id"])["ground_message_id"]
    current = json_of(s.driver.get("mara", session["help_session_id"]))
    ticked = s.driver.ok("mara", current, "tick_step", step_index=4)
    assert num(ticked, "revision") == num(current, "revision") + 1
    assert s.transport.runtime.agent.run_once().sent == 0
    assert s.message(gid)["state"] == "queued"
    assert s.log()["items"] == []


@pytest.mark.trace("E29-LINK", "R49", "N07")
def test_environment_profiles_validate_and_lunar_gap_matches_n07_golden() -> None:
    data = json.loads((ROOT / "config/environment-profiles.json").read_bytes())
    validate_document("config-environment-profiles.schema.json", data)
    lunar = next(profile for profile in data["profiles"] if profile["environment_id"] == "lunar_surface")
    window = lunar["link"]["sim_schedule"][0]
    duration = datetime.fromisoformat(window["ends_at"]) - datetime.fromisoformat(window["starts_at"])
    # N07 p.2: Earth is visible "about half of every sidereal month ... (approximately 14 days)". NASA gives
    # only the approximate half; the exact window uses the sidereal month of 27.321661 days, a non-NASA value
    # (IAU astronomical constants), so the golden checks both: N07's 14 days and the exact half month.
    assert abs(duration.total_seconds() / 86400 - 14) < 0.5
    assert abs(duration.total_seconds() - 27.321661 * 86400 / 2) < 1
    assert window["gap_kind"] == "planned_line_of_sight"
    # The Moon delay cites EV01 (section 9.7.8.3 rationale, p. 189) only; N07 and N08 speak to visibility.
    assert lunar["comms_queue"]["source_ids"] == ["EV01"]
    assert window["planned"] is True
    mars = next(profile for profile in data["profiles"] if profile["environment_id"] == "mars_surface")
    assert mars["comms_queue"]["simulated_one_way_delay_seconds"] == 1200
    assert mars["comms_queue"]["basis"] == "sourced"
    assert mars["comms_queue"]["source_ids"] == ["EV25", "N09"]
    assert mars["link"]["sim_schedule"] == [
        {
            "starts_at": "2025-12-29T00:00:00Z",
            "ends_at": "2026-01-16T00:00:00Z",
            "status": "down",
            "gap_kind": "conjunction",
            "planned": True,
        }
    ]
