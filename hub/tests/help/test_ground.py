"""Tell the ground (E26 3.3, CONTRACTS 16 and 20): the message is drafted from the log, queued on the press and
never waited for. Expected texts are the package examples (contracts/api/examples/help), not derived here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from help_support import (
    Composer,
    Driver,
    FailingPort,
    Harness,
    Providers,
    Roster,
    build,
    dig,
    drive_eye,
    example_value,
    headers,
    json_of,
    mission_clock,
    num,
    rows,
)

from crew_health.contracts_gen.schemas.ground_message_schema import GroundMessage
from crew_health.kernel.uow import Actor

QUEUED_AT = datetime(2026, 11, 13, 12, 5, 30, tzinfo=UTC)
SENT_AT = datetime(2026, 11, 14, 11, 20, 41, tzinfo=UTC)
ARRIVED_AT = datetime(2026, 11, 14, 11, 20, 51, tzinfo=UTC)
ACK_AT = datetime(2026, 11, 14, 11, 30, 41, tzinfo=UTC)


def _eye(
    tmp_path: Path, composer: Composer | FailingPort | None
) -> tuple[Harness, Driver, dict[str, object]]:
    providers = Providers(roster=Roster(), mission_clock=mission_clock(), composer=composer)
    harness = build(tmp_path / "help.db", providers)
    driver = Driver(harness)
    session = drive_eye(driver)
    harness.at(QUEUED_AT)
    return harness, driver, session


def _queue(harness: Harness, who: str, session_id: object, key: str, **body: object) -> dict[str, object]:
    response = harness.client.post(
        f"/v1/help-sessions/{session_id}/ground-messages", json=body, headers=headers(who, key)
    )
    assert response.status_code == 201, response.text
    wire = json_of(response)
    GroundMessage.model_validate(wire)
    return wire


def _lines(name: str) -> list[str]:
    lines = example_value(f"help/{name}.json")["lines"]
    assert isinstance(lines, list)
    return [str(x) for x in lines]


@pytest.mark.trace("T-E26-060", "R61", "D26", "I-H9", "D-E26-1")
def test_preview_and_press_give_the_example_lines_and_the_same_sha(tmp_path: Path) -> None:
    harness, _, session = _eye(tmp_path, Composer("down"))
    sid = session["help_session_id"]
    preview = harness.client.get(f"/v1/help-sessions/{sid}/ground-message-preview", headers=headers("mara"))
    assert preview.status_code == 200, preview.text
    shown = json_of(preview)
    expected = _lines("ground-message-preview")
    lines = shown["lines"]
    assert isinstance(lines, list)
    assert lines[:3] == expected[:3]
    # D-E26-1: the person who pressed for Tomas is the first helper, so both names are listed (example lists Ilya).
    assert lines[3] == "Crew called at 12:02. Mara Okafor, Ilya Brandt helping."
    assert shown["persisted"] is False
    queued = _queue(harness, "mara", sid, "tell-ground-001", previewed_sha256=shown["text_sha256"])
    assert queued["text_sha256"] == shown["text_sha256"] and queued["text"] == shown["text"]
    assert queued["lines"] == shown["lines"] and queued["state"] == "queued"
    status = example_value("help/ground-message-queued.json")["status_text"]
    assert queued["status_text"] == status
    assert queued["leaves_at"] == "2026-11-14T11:20:40Z"
    log = json_of(harness.client.get(f"/v1/help-sessions/{sid}", headers=headers("mara")))["log"]
    assert isinstance(log, list) and log[-1]["kind"] == "ground"


@pytest.mark.trace("T-E26-062", "V68", "D26", "AGENTS-3")
def test_press_works_without_the_ground_link_module(tmp_path: Path) -> None:
    harness, _, session = _eye(tmp_path, None)
    sid = session["help_session_id"]
    preview = harness.client.get(f"/v1/help-sessions/{sid}/ground-message-preview", headers=headers("mara"))
    assert preview.status_code == 500 and json_of(preview)["retryable"] is True
    queued = _queue(harness, "mara", sid, "tell-ground-002")
    assert (
        queued["status_text"]
        == "Queued at 12:05. It goes out when the link allows. Keep going: nothing here waits for it."
    )
    assert queued["leaves_at"] is None
    catch = queued["catch_up"]
    assert isinstance(catch, dict)
    assert catch["what_happened"] == _lines("ground-message-preview")[:2]
    assert catch["being_watched"] == ["Unknown"] and catch["advice_welcome"] == ["Unknown"]


@pytest.mark.trace("T-E26-075", "CONTRACTS-26")
def test_composer_failure_falls_back_and_keeps_what_was_done(tmp_path: Path) -> None:
    harness, _, session = _eye(tmp_path, FailingPort())
    queued = _queue(harness, "mara", session["help_session_id"], "tell-ground-003")
    catch = queued["catch_up"]
    assert isinstance(catch, dict)
    done = catch["what_was_done"]
    assert isinstance(done, list) and len(done) == 4
    assert done[0].endswith("Do not rub the eye.")
    snapshot = harness.service.snapshots.for_message(str(queued["ground_message_id"]))
    assert snapshot is not None and snapshot.session_revision == queued["session_revision"]


@pytest.mark.trace("T-E26-061", "T-E26-069", "I-H9", "I-H18", "EI5")
def test_lifecycle_sent_arrival_receipt_replies_and_ground_visibility(tmp_path: Path) -> None:
    harness, driver, session = _eye(tmp_path, Composer("down"))
    sid = session["help_session_id"]
    queued = _queue(harness, "mara", sid, "tell-ground-004")
    gid = str(queued["ground_message_id"])
    url = f"/v1/ground-messages/{gid}"
    assert harness.client.get(url, headers=headers("ground")).status_code == 404
    assert harness.client.get(url, headers=headers("noor")).status_code == 403
    life = harness.service.lifecycle
    harness.at(SENT_AT)
    assert life.mark_sent(gid, "ev_sent", SENT_AT, 10.0, True) == "applied"
    assert life.mark_sent(gid, "ev_sent", SENT_AT, 10.0, True) == "duplicate"
    sent = json_of(harness.client.get(url, headers=headers("mara")))
    assert sent["status_text"] == example_value("help/ground-message-sent.json")["status_text"]
    assert harness.client.get(url, headers=headers("ground")).status_code == 404
    harness.at(ARRIVED_AT)
    assert life.record_arrival(gid, "ev_arrived", ARRIVED_AT) == "applied"
    assert json_of(harness.client.get(url, headers=headers("ground")))["state"] == "sent"
    harness.at(ACK_AT)
    assert life.record_receipt(gid, "ev_receipt", ACK_AT, ARRIVED_AT) == "applied"
    acknowledged = json_of(harness.client.get(url, headers=headers("ground")))
    assert acknowledged["state"] == "acknowledged"
    assert (
        acknowledged["status_text"] == example_value("help/ground-message-acknowledged.json")["status_text"]
    )
    before = json_of(driver.get("mara", sid))
    surgeon = Actor("ground", "grd_flight_surgeon")
    assert life.record_reply(gid, "ev_reply_1", "First reply.", surgeon, ACK_AT, ACK_AT) == "applied"
    assert life.record_reply(gid, "ev_reply_2", "Second reply.", surgeon, ACK_AT, ACK_AT) == "applied"
    assert life.record_reply(gid, "ev_reply_2", "Second reply.", surgeon, ACK_AT, ACK_AT) == "duplicate"
    replied = json_of(harness.client.get(url, headers=headers("mara")))
    assert dig(replied, "reply", "text") == "Second reply."
    after = json_of(driver.get("mara", sid))
    texts = [line["text"] for line in rows(after, "log")]
    assert texts.count("Ground: First reply.") == 1 and texts.count("Ground: Second reply.") == 1
    for field in ("node", "level", "helpers", "state", "crew_called_at"):
        assert after[field] == before[field], field
    assert any(e.scope.kind == "ground" for e in harness.store.events)


@pytest.mark.trace("T-E26-063", "T-E26-064", "D26", "EI5")
def test_ticks_after_the_press_are_logged_and_delivery_after_close_never_reopens(tmp_path: Path) -> None:
    harness, driver, session = _eye(tmp_path, Composer("up"))
    sid = session["help_session_id"]
    queued = _queue(harness, "mara", sid, "tell-ground-005")
    current = json_of(driver.get("mara", sid))
    ticked = driver.ok("mara", current, "tick_step", step_index=4)
    assert "Done: Call the CMO." in [line["text"] for line in rows(ticked, "log")]
    closed = driver.ok("mara", ticked, "close", disposition="handed_to_crew_medical_officer")
    harness.at(SENT_AT)
    gid = str(queued["ground_message_id"])
    assert harness.service.lifecycle.mark_sent(gid, "ev_sent_c", SENT_AT, 10.0, True) == "applied"
    shown = json_of(driver.get("tomas", sid))
    assert shown["state"] == "closed" and shown["revision"] == num(closed, "revision") + 1
    assert dig(shown, "log", -1, "kind") == "system"
    again = harness.client.post(
        f"/v1/help-sessions/{sid}/ground-messages", json={}, headers=headers("tomas", "tell-ground-006")
    )
    assert again.status_code == 409 and json_of(again)["code"] == "SESSION_CLOSED"


@pytest.mark.trace("T-E26-092", "I-H16")
def test_retried_press_queues_one_message_also_after_restart(tmp_path: Path) -> None:
    harness, _, session = _eye(tmp_path, None)
    first = _queue(harness, "mara", session["help_session_id"], "tell-ground-007")
    restarted = harness.restart()
    second = _queue(restarted, "mara", session["help_session_id"], "tell-ground-007")
    assert first["ground_message_id"] == second["ground_message_id"]
    assert restarted.store.count("help_ground_message") == 1


@pytest.mark.trace("T-E26-061", "I-H9")
def test_receipt_before_the_local_sent_is_buffered_then_applied(tmp_path: Path) -> None:
    harness, _, session = _eye(tmp_path, Composer("down"))
    gid = str(_queue(harness, "mara", session["help_session_id"], "tell-ground-008")["ground_message_id"])
    life = harness.service.lifecycle
    assert life.record_receipt(gid, "ev_early_receipt", ACK_AT, ARRIVED_AT) == "buffered"
    assert (
        json_of(harness.client.get(f"/v1/ground-messages/{gid}", headers=headers("mara")))["state"]
        == "queued"
    )
    harness.at(ACK_AT)
    assert life.mark_sent(gid, "ev_late_sent", SENT_AT, 10.0, True) == "applied"
    shown = json_of(harness.client.get(f"/v1/ground-messages/{gid}", headers=headers("mara")))
    assert shown["state"] == "acknowledged"
    assert shown["acknowledged_at"] == "2026-11-14T11:30:41Z"
