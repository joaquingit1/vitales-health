"""End to end over HTTP: the help router on real SQLite, kernel fakes and fakes of the optional ports.

Covers Someone needs help with the model stopped and the failure cases E26 names.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from help_support import (
    EPOCH,
    STATION,
    T0,
    Assistant,
    Driver,
    Harness,
    Observations,
    Providers,
    Roster,
    all_failing,
    all_slow,
    build,
    config_raw,
    crew,
    dig,
    drive_eye,
    headers,
    json_of,
    mission_clock,
    num,
    open_body,
    release,
    rows,
)

from crew_health.contracts_gen.schemas.help_session_schema import HelpSession
from crew_health.help.application.opening import OpenRequest
from crew_health.help.application.ports import ReadingView
from crew_health.kernel.testing import MemoryConfig


def _harness(tmp_path: Path, providers: Providers | None = None) -> tuple[Harness, Driver]:
    harness = build(tmp_path / "help.db", providers)
    return harness, Driver(harness)


def _stopped_text() -> str:
    text = json.loads(config_raw("help-presentation"))["assistant_line"]["stopped_text"]
    assert isinstance(text, str)
    return text


def _log_texts(session: dict[str, object]) -> list[str]:
    log = session["log"]
    assert isinstance(log, list)
    return [str(line["text"]) for line in log]


@pytest.mark.trace("T-E26-001", "R15", "V01", "D15", "D41", "I-H2")
@pytest.mark.parametrize("providers", ["absent", "failing", "slow"])
def test_help_works_with_every_optional_part_absent_or_failing(tmp_path: Path, providers: str) -> None:
    chosen = {"absent": Providers(), "failing": all_failing(), "slow": all_slow(2.0)}[providers]
    harness, driver = _harness(tmp_path, chosen)
    started = time.monotonic()
    opened = driver.open("mara", open_body("crewmate", "tomas", place="airlock"))
    assert opened.status_code == 201, opened.text
    assert time.monotonic() - started < 1.0  # a hanging provider is cut at help.port_timeout_ms
    session = json_of(opened)
    HelpSession.model_validate(session)
    node = session["node"]
    assert isinstance(node, dict)
    assert node["kind"] == "triage" and node["id"] == "air_safe"
    assert node["question"] and node["answers"]
    assert [b["id"] for b in rows(session, "fixed_buttons")] == ["call_crew", "tell_ground", "start_over"]
    for node_id, index in (("air_safe", 0), ("responding", 1), ("breathing", 1)):
        session = driver.ok("mara", session, "answer", node_id=node_id, answer_index=index)
    session = driver.ok("mara", session, "tick_step", step_index=0)
    session = driver.ok("mara", session, "call_crew")
    asked = driver.ok("mara", session, "ask_assistant", text="What next?")
    assert time.monotonic() - started < 2.0  # every command committed and returned without waiting
    harness.drain()
    after = json_of(driver.get("mara", asked["help_session_id"]))
    assert dig(after, "node", "id") == "not_breathing"
    assert after["level"] == "emergency"
    assert dig(after, "assistant", "status") == "unavailable"
    assert after["crew_called_at"] is not None
    assert harness.store.count("help_session") == 1


@pytest.mark.trace("T-E26-055", "D15", "D41", "V01", "UC16", "rule-4")
def test_model_stopped_shows_stopped_text_once_and_steps_still_work(tmp_path: Path) -> None:
    assistant = Assistant("stopped")
    harness, driver = _harness(tmp_path, Providers(assistant=assistant, roster=Roster()))
    session = drive_eye(driver)
    before = session
    asked = driver.ok("mara", session, "ask_assistant", text="Is rinsing enough?")
    harness.drain()
    shown = json_of(driver.get("mara", asked["help_session_id"]))
    stopped = _stopped_text()
    assert "Assistant: stopped" in stopped and "Works without the assistant" in stopped
    assert _log_texts(shown).count(stopped) == 1
    assert dig(shown, "assistant", "status") == "stopped"
    for field in ("node", "level", "helpers", "crew_called_at", "state", "broadcast_id"):
        assert shown[field] == before[field], field
    again = driver.ok("mara", shown, "ask_assistant", text="And now?")
    harness.drain()
    again = json_of(driver.get("mara", again["help_session_id"]))
    assert _log_texts(again).count(stopped) == 1
    ticked = driver.ok("mara", again, "tick_step", step_index=4)
    assert dig(ticked, "node", "flow", "immediate_steps", 4, "ticked_at") is not None
    assert len(assistant.started) == 2


@pytest.mark.trace("T-E26-055", "rule-4")
def test_runtime_stop_event_logs_stopped_text_once_on_every_open_session(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    first = json_of(driver.open("mara", open_body()))
    second = json_of(driver.open("tomas", open_body()))
    harness.service.assistant.runtime_status("stopped")
    harness.service.assistant.runtime_status("stopped")
    for who, session in (("mara", first), ("tomas", second)):
        shown = json_of(driver.get(who, session["help_session_id"]))
        assert _log_texts(shown).count(_stopped_text()) == 1
        assert shown["node"] == session["node"]
        assert shown["level"] == session["level"]


@pytest.mark.trace("T-E26-092", "T-E26-094", "I-H16", "R15")
def test_retried_start_with_same_key_returns_the_one_session_also_after_restart(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    body = open_body("crewmate", "tomas")
    first = driver.open("mara", body, key="press-0001")
    assert first.status_code == 201
    retried = driver.open("mara", body, key="press-0001")
    restarted = harness.restart()
    restarted.advance(30 * 3600)
    after_restart = Driver(restarted).open("mara", body, key="press-0001")
    ids = {json_of(r)["help_session_id"] for r in (first, retried, after_restart)}
    assert len(ids) == 1
    assert restarted.store.count("help_session") == 1
    assert restarted.store.count("help_action") == 1


@pytest.mark.trace("T-E26-093", "CONTRACTS-5")
def test_same_key_with_a_different_body_is_a_duplicate_command(tmp_path: Path) -> None:
    _, driver = _harness(tmp_path)
    assert driver.open("mara", open_body(), key="press-0002").status_code == 201
    clash = driver.open("mara", open_body(place="galley"), key="press-0002")
    assert clash.status_code == 409
    assert json_of(clash)["code"] == "DUPLICATE_COMMAND"


@pytest.mark.trace("T-E26-020", "V71", "I-H4", "D-E26-1")
def test_two_pressers_for_one_subject_share_one_session_and_both_help(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    first = json_of(driver.open("mara", open_body("crewmate", "tomas", place="airlock")))
    second = json_of(driver.open("ilya", open_body("crewmate", "tomas")))
    assert first["help_session_id"] == second["help_session_id"]
    assert [h["person_id"] for h in rows(second, "helpers")] == ["mara", "ilya"]
    assert harness.store.count("help_session") == 1
    third = json_of(driver.open("tomas", open_body()))
    assert third["help_session_id"] == first["help_session_id"]
    assert third["helpers"] == second["helpers"]


@pytest.mark.trace("T-E26-021", "I-H4")
def test_concurrent_presses_for_one_subject_yield_one_session(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    request = OpenRequest("crewmate", "tomas", "button", None, None, T0, (), None, None)
    results: list[str] = []
    errors: list[BaseException] = []

    def press(person: str) -> None:
        try:
            wire, _ = harness.service.opening.open(
                crew(person),
                request,
                key=f"press-{person}-1",
                operation="openHelpSession",
                correlation_id="c1",
            )
            results.append(str(wire["help_session_id"]))
        except BaseException as error:  # noqa: BLE001 - collected and asserted below
            errors.append(error)

    threads = [threading.Thread(target=press, args=(p,)) for p in ("mara", "ilya", "noor")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    assert len(set(results)) == 1 and len(results) == 3
    assert harness.store.count("help_session") == 1


@pytest.mark.trace("T-E26-040", "V64", "D24", "SYSTEM-8.3")
def test_privacy_between_crew_members_while_open(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    sid = session["help_session_id"]
    for outsider in ("noor", "ground"):
        denied = driver.get(outsider, sid)
        assert denied.status_code == 403 and json_of(denied)["code"] == "UNAUTHORIZED_SCOPE"
    for member in ("mara", "tomas"):
        assert driver.get(member, sid).status_code == 200
    listed = json_of(harness.client.get("/v1/help-sessions", headers=headers("noor")))
    assert listed["items"] == []
    stale = driver.act("noor", session, "add_note", note="hello")
    assert stale.status_code == 403
    joined = driver.ok("noor", session, "join")
    assert driver.get("noor", sid).status_code == 200
    assert [h["person_id"] for h in rows(joined, "helpers")] == ["mara", "noor"]
    ground_list = harness.client.get("/v1/help/broadcasts", headers=headers("ground"))
    assert ground_list.status_code == 403


@pytest.mark.trace("T-E26-041", "T-E26-042", "T-E26-043", "V64", "D24", "I-H8")
def test_session_is_not_a_record_grant_and_closure_ends_helper_reads(tmp_path: Path) -> None:
    reading = ReadingView("obs_1", "pulse", "Pulse", "72 /min", T0, T0, "SIM", "Watch (Sim)", "wrist")
    observations = Observations([reading])
    harness, driver = _harness(tmp_path, Providers(roster=Roster(), observations=observations))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    sid = session["help_session_id"]
    harness.advance(60)
    summary = harness.client.get(f"/v1/help-sessions/{sid}/subject", headers=headers("mara"))
    assert summary.status_code == 200, summary.text
    body = json_of(summary)
    assert set(body) == {"help_session_id", "subject", "as_of", "readings", "allergies", "medicines"}
    assert body["allergies"] == {"state": "unknown", "items": []}
    assert summary.headers["Cache-Control"] == "no-store"
    assert harness.store.audit_types().count("help.subject_viewed") == 1
    outsider = harness.client.get(f"/v1/help-sessions/{sid}/subject", headers=headers("noor"))
    assert outsider.status_code == 403
    assert observations.asked == ["tomas"]
    closed = driver.ok("mara", json_of(driver.get("mara", sid)), "close", disposition="resolved_on_board")
    assert closed["state"] == "closed"
    assert driver.get("mara", sid).status_code == 403
    assert driver.get("tomas", sid).status_code == 200
    after = harness.client.get(f"/v1/help-sessions/{sid}/subject", headers=headers("mara"))
    assert after.status_code == 403
    assert not harness.service.reader.readable_by(crew("mara"), str(sid))
    assert harness.service.reader.readable_by(crew("tomas"), str(sid))


@pytest.mark.trace("T-E26-042", "T-E26-044", "I-H8")
def test_subject_summary_failures_are_loud_never_empty(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    absent = harness.client.get(
        f"/v1/help-sessions/{session['help_session_id']}/subject", headers=headers("mara")
    )
    assert absent.status_code == 500 and json_of(absent)["retryable"] is True
    unknown = json_of(driver.open("ilya", open_body("unknown")))
    missing = harness.client.get(
        f"/v1/help-sessions/{unknown['help_session_id']}/subject", headers=headers("ilya")
    )
    assert missing.status_code == 404


@pytest.mark.trace("T-E26-016", "I-H7")
def test_every_action_on_a_closed_session_is_session_closed(tmp_path: Path) -> None:
    _, driver = _harness(tmp_path)
    session = json_of(driver.open("tomas", open_body()))
    closed = driver.ok("tomas", session, "close", disposition="false_alarm")
    for action, fields in (
        ("call_crew", {}),
        ("add_note", {"note": "x"}),
        ("start_over", {}),
        ("answer", {"node_id": "air_safe", "answer_index": 0}),
        ("close", {"disposition": "other"}),
    ):
        response = driver.act("tomas", closed, action, **fields)
        assert response.status_code == 409, action
        error = json_of(response)
        assert error["code"] == "SESSION_CLOSED"
        assert isinstance(error["current_state"], dict) and error["current_state"]["state"] == "closed"


@pytest.mark.trace("T-E26-017", "CONTRACTS-16-concurrency")
def test_versions_required_stale_rejected_commuting_applied(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    session = json_of(driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1")))
    sid = session["help_session_id"]
    missing = harness.client.post(
        f"/v1/help-sessions/{sid}/actions",
        json={"action": "call_crew", "client_time": "2026-11-13T12:02:00Z"},
        headers=headers("tomas", "key-missing-if-match"),
    )
    assert missing.status_code == 428 and json_of(missing)["code"] == "VERSION_REQUIRED"
    moved = driver.ok("tomas", session, "add_note", note="first")
    stale = driver.act("tomas", session, "switch_flow", flow_id="burns")
    assert stale.status_code == 409 and json_of(stale)["code"] == "STALE_VERSION"
    assert dig(json_of(stale), "current_state", "revision") == moved["revision"]
    commuted = driver.act("tomas", session, "tick_step", flow_id="chest_pain", step_index=1)
    assert commuted.status_code == 200
    assert json_of(commuted)["revision"] == num(moved, "revision") + 1


@pytest.mark.trace("T-E26-110", "T-E26-111", "H12", "rule-6")
def test_storage_failure_rolls_back_and_is_retryable(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    harness.store.fail_commit = True
    failed = driver.open("mara", open_body(), key="press-disk-1")
    assert failed.status_code == 500 and json_of(failed)["retryable"] is True
    assert harness.store.count("help_session") == 0
    assert harness.store.count("platform_command_ledger") == 0
    harness.store.fail_commit = False
    retried = driver.open("mara", open_body(), key="press-disk-1")
    assert retried.status_code == 201
    assert harness.store.count("help_session") == 1


@pytest.mark.trace("CONTRACTS-11", "AGENTS-config-reject")
def test_missing_or_corrupt_configuration_is_rejected(tmp_path: Path) -> None:
    corrupt = release("rel_bad_1", presentation=b"{not json")
    harness, driver = _harness(tmp_path)
    harness.config.releases[corrupt.release_id] = corrupt
    harness.config.active_id = corrupt.release_id
    response = driver.open("mara", open_body())
    assert response.status_code == 422 and json_of(response)["code"] == "CONFIG_INVALID"
    assert harness.store.count("help_session") == 0
    unknown_flow = json.loads(config_raw("help-presentation"))
    unknown_flow["flow_levels"][0]["flow_id"] = "no_such_flow"
    broken = release("rel_bad_2", presentation=json.dumps(unknown_flow).encode())
    harness.config.releases[broken.release_id] = broken
    harness.config.active_id = broken.release_id
    assert json_of(driver.open("mara", open_body(), key="press-cfg-2"))["code"] == "CONFIG_INVALID"


@pytest.mark.trace("HTTP-boundary", "D5")
def test_boundary_rejects_malformed_and_unauthenticated_requests(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    no_key = harness.client.post("/v1/help-sessions", json=open_body(), headers=headers("mara"))
    assert no_key.status_code == 400 and json_of(no_key)["code"] == "MALFORMED_REQUEST"
    bad = harness.client.post(
        "/v1/help-sessions", json={"subject": {"kind": "me"}}, headers=headers("mara", "press-bad-1")
    )
    assert bad.status_code == 400
    anonymous = harness.client.post(
        "/v1/help-sessions", json=open_body(), headers={"Idempotency-Key": "anon-0001"}
    )
    assert anonymous.status_code == 401
    ground = driver.open("ground", open_body("unknown"))
    assert ground.status_code == 403
    assert harness.store.count("help_session") == 0


@pytest.mark.trace("SYSTEM-8.8-W1.6", "I-H14", "H11")
def test_offline_answers_replay_into_one_session_once(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    offline = (
        ("air_safe", 0, datetime(2026, 11, 13, 12, 1, 50, tzinfo=UTC)),
        ("responding", 1, datetime(2026, 11, 13, 12, 1, 54, tzinfo=UTC)),
        ("breathing", 1, datetime(2026, 11, 13, 12, 1, 58, tzinfo=UTC)),
    )
    body = open_body(at=datetime(2026, 11, 13, 12, 1, 45, tzinfo=UTC), offline=offline)
    first = json_of(driver.open("tomas", body, key="offline-press-1"))
    assert dig(first, "node", "id") == "not_breathing"
    assert len(rows(first, "path")) == 3
    restarted = harness.restart()
    again = json_of(Driver(restarted).open("tomas", body, key="offline-press-1"))
    assert again["help_session_id"] == first["help_session_id"]
    assert again["path"] == first["path"] and again["revision"] == first["revision"]
    assert restarted.store.count("help_session") == 1


@pytest.mark.trace("T-E26-018", "I-H14")
def test_offline_replay_stops_at_a_node_mismatch_and_audits(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    offline = (("air_safe", 0, T0), ("breathing", 1, T0))
    session = json_of(driver.open("tomas", open_body(offline=offline)))
    assert dig(session, "node", "id") == "responding"
    assert len(rows(session, "path")) == 1
    assert "help.replay_stopped" in harness.store.audit_types()


@pytest.mark.trace("T-E26-072", "V61", "R-E11-4", "SYSTEM-8.1")
def test_red_flag_entry_opens_on_the_mapped_flow_and_joins_later_presses(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    entry = harness.service.entry
    sid = entry.open_from_red_flag("ep_9", "ans_3", "tomas", "chest_pain", T0, "redflag-ep9-1")
    again = entry.open_from_red_flag("ep_9", "ans_3", "tomas", "chest_pain", T0, "redflag-ep9-2")
    assert sid == again
    shown = json_of(driver.get("tomas", sid))
    assert dig(shown, "node", "id") == "chest_pain" and dig(shown, "entry", "kind") == "red_flag"
    assert dig(shown, "opened_by", "kind") == "engine"
    press = json_of(driver.open("tomas", open_body()))
    assert press["help_session_id"] == sid
    with pytest.raises(Exception, match="not a red flag flow"):
        entry.open_from_red_flag("ep_10", "ans_1", "ilya", "eye_injury", T0, "redflag-ep10-1")


@pytest.mark.trace("T-E26-020", "CONTRACTS-16-station")
def test_station_press_attaches_the_display_and_urgent_alias_opens(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path)
    alias = harness.client.post(
        "/v1/urgent-events",
        json=open_body("unknown", place="galley"),
        headers=headers("station", "urgent-0001"),
    )
    assert alias.status_code == 201, alias.text
    assert dig(json_of(alias), "entry", "kind") == "urgent_event_alias"
    tomas = json_of(driver.open("tomas", open_body()))
    attached = json_of(driver.open("station", open_body("crewmate", "tomas")))
    assert attached["help_session_id"] == tomas["help_session_id"]
    assert attached["helpers"] == [] and attached["revision"] == tomas["revision"]
    assert driver.get("station", tomas["help_session_id"]).status_code == 200
    assert harness.service.reader.readable_by(STATION, str(tomas["help_session_id"]))


@pytest.mark.trace("T-E26-030", "T-E26-031", "T-E26-023", "UC18", "V71", "I-H15")
def test_broadcast_lists_names_place_and_level_never_readings(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas", place="airlock")))
    listed = json_of(harness.client.get("/v1/help/broadcasts", headers=headers("noor")))
    items = listed["items"]
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    assert item["text"] == "Tomas Lind needs help at the airlock. Mara Okafor helping."
    assert set(item) == {
        "broadcast_id",
        "help_session_id",
        "state",
        "level",
        "subject",
        "helpers",
        "roles",
        "place",
        "text",
        "started_at",
        "ended_at",
    }
    left = driver.ok("mara", session, "leave")
    shown = json_of(harness.client.get("/v1/help/broadcasts", headers=headers("noor")))
    assert dig(shown, "items", 0, "text") == "Tomas Lind needs help at the airlock. Nobody has joined yet."
    driver.ok("tomas", left, "close", disposition="resolved_on_board")
    assert json_of(harness.client.get("/v1/help/broadcasts", headers=headers("noor")))["items"] == []
    for event in harness.store.events:
        assert b"readings" not in event.data_json and b"72 /min" not in event.data_json


@pytest.mark.trace("T-E26-085", "R43", "V75", "AGENTS-1")
def test_configuration_edit_changes_new_sessions_only(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    old = json_of(
        driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1", place="airlock"))
    )
    flows = json.loads(config_raw("responder-flows"))
    chest = next(f for f in flows["flows"] if f["id"] == "chest_pain")
    original = chest["immediate_steps"][0]
    chest["immediate_steps"][0] = "Edited first step for the configuration test."
    presentation = json.loads(config_raw("help-presentation"))
    presentation["broadcast"]["template"] = "{subject_name} at {place} needs a hand."
    edited = release(
        "rel_test_2", flows=json.dumps(flows).encode(), presentation=json.dumps(presentation).encode()
    )
    harness.config.releases[edited.release_id] = edited
    harness.config.active_id = edited.release_id
    new = json_of(
        driver.open("mara", open_body(flow="chest_pain", entry="red_flag", ref="ep_2", place="lab"))
    )
    edited_text = dig(new, "node", "flow", "immediate_steps", 0, "text")
    assert edited_text == "Edited first step for the configuration test."
    kept = json_of(driver.get("tomas", old["help_session_id"]))
    assert dig(kept, "node", "flow", "immediate_steps", 0, "text") == original
    assert kept["config_pins"] == old["config_pins"] != new["config_pins"]
    listed = json_of(harness.client.get("/v1/help/broadcasts", headers=headers("mara")))
    texts = {str(i["help_session_id"]): str(i["text"]) for i in rows(listed, "items")}
    assert texts[str(new["help_session_id"])] == "Mara Okafor at lab needs a hand. Nobody has joined yet."
    assert texts[str(old["help_session_id"])].startswith("Tomas Lind needs help at the airlock.")


@pytest.mark.trace("T-E26-059", "I-H13", "rule-4")
def test_assistant_turn_lifecycle_never_touches_safety_state(tmp_path: Path) -> None:
    assistant = Assistant("waiting")
    harness, driver = _harness(tmp_path, Providers(assistant=assistant))
    session = json_of(driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1")))
    asked = driver.ok("tomas", session, "ask_assistant", text="Should I rinse?")
    harness.drain()
    assert assistant.started and dig(asked, "assistant", "status") in ("waiting", "off")
    running = json_of(driver.get("tomas", asked["help_session_id"]))
    assert dig(running, "assistant", "status") == "answering"
    switched = driver.ok("tomas", running, "switch_flow", flow_id="burns")
    harness.drain()
    assert assistant.cancelled
    harness.service.assistant.turn_failed("hlt_unknown", "MODEL_TIMEOUT")
    shown = json_of(driver.get("tomas", switched["help_session_id"]))
    assert dig(shown, "node", "id") == "burns" and shown["level"] == switched["level"]
    assert "help.assistant_turn_late" not in harness.store.audit_types()


@pytest.mark.trace("T-E26-001", "SYSTEM-4.4")
def test_help_session_reader_names_match_system_4_4(tmp_path: Path) -> None:
    import inspect

    harness, driver = _harness(tmp_path)
    reader = harness.service.reader
    assert list(inspect.signature(reader.context).parameters) == ["session", "help_session_id"]
    assert list(inspect.signature(reader.readable_by).parameters) == ["session", "help_session_id"]
    sid = str(json_of(driver.open("tomas", open_body(), key="press-reader-1"))["help_session_id"])
    assert reader.context(crew("tomas"), sid).visible
    assert not reader.context(crew("noor"), sid).visible
    assert reader.snapshot(sid) is not None
    assert [r.help_session_id for r in reader.qualifying_sessions()] == [sid]


@pytest.mark.trace("T-E26-001", "SYSTEM-4.2")
def test_mission_clock_and_failing_mission_clock_do_not_change_entry(tmp_path: Path) -> None:
    for name, providers in (("clock", Providers(mission_clock=mission_clock())), ("broken", all_failing())):
        harness = build(
            tmp_path / f"{name}.db",
            providers,
            config=MemoryConfig([release("rel_test_1")], active_id="rel_test_1"),
        )
        response = Driver(harness).open("tomas", open_body())
        assert response.status_code == 201
    assert EPOCH.tzinfo is UTC
