"""Regressions from the independent review of E26: each test failed on the code the review read.

Optional providers never hold up a command or the hub (E26 4.5, I-H2, AGENTS rule 3); the assistant never acts
(rule 4); a late event never overwrites a newer state and a retry replays its recorded outcome (AGENTS 6,
SYSTEM 5.8, E26 3.2).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

import httpx
import pytest
from help_support import (
    T0,
    Assistant,
    Composer,
    Driver,
    Harness,
    Providers,
    Roster,
    all_slow,
    build,
    dig,
    headers,
    json_of,
    open_body,
    pinned_docs,
    rows,
)

from crew_health.help.application.ports import AssistantStart, HelpTurnCandidate
from crew_health.kernel.uow import Actor


def _harness(tmp_path: Path, providers: Providers) -> tuple[Harness, Driver]:
    harness = build(tmp_path / "help.db", providers)
    return harness, Driver(harness)


@dataclass(frozen=True, slots=True)
class _Role:
    role_assignment_id: str
    role_id: str
    label: str
    person_id: str
    display_name: str
    state: Literal["proposed", "accepted", "changed"]


class _SlowRoles:
    """E31 fake: answers after `delay_s` with one proposed role."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.calls = 0

    def roles_for_session(self, help_session_id: str) -> tuple[_Role, ...]:
        self.calls += 1
        time.sleep(self.delay_s)
        return (_Role("rol_1", "first_responder", "First responder", "ilya", "Ilya Brandt", "proposed"),)


@dataclass
class _SlowAssistant(Assistant):
    delay_s: float = 1.5

    def start_help_turn(
        self, help_session_id: str, help_turn_id: str, actor: Actor, text: str, conversation_id: str | None
    ) -> AssistantStart:
        time.sleep(self.delay_s)
        return super().start_help_turn(help_session_id, help_turn_id, actor, text, conversation_id)

    def cancel_help_turn(self, help_turn_id: str) -> None:
        time.sleep(self.delay_s)
        super().cancel_help_turn(help_turn_id)


class _Rules:
    def amount_pattern(self, release_id: str) -> re.Pattern[str]:
        return re.compile(r"\d+\s*(mg|ml|tablets?)\b", re.IGNORECASE)

    def medicine_terms(self, release_id: str) -> frozenset[str]:
        return frozenset({"ibuprofen"})


@pytest.mark.trace("T-E26-001", "T-E26-002", "I-H2", "R15", "rule-3")
def test_a_slow_optional_provider_never_holds_up_help_or_other_requests(tmp_path: Path) -> None:
    roles = _SlowRoles(2.0)
    harness, driver = _harness(tmp_path, Providers(roster=Roster(), roles=roles))
    app = harness.client.app

    async def main() -> tuple[float, float, int, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
            started = time.monotonic()
            press = asyncio.create_task(
                client.post(
                    "/v1/help-sessions",
                    json=open_body("crewmate", "tomas"),
                    headers=headers("mara", "press-slow-0001"),
                )
            )
            await asyncio.sleep(0.01)
            read_started = time.monotonic()
            read = await client.get("/v1/help/broadcasts", headers=headers("ilya"))
            read_took = time.monotonic() - read_started
            opened = await press
            return time.monotonic() - started, read_took, opened.status_code, read.status_code

    open_took, read_took, open_status, read_status = asyncio.run(main())
    assert open_status == 201 and read_status == 200
    assert open_took < 1.0, open_took
    assert read_took < 1.0, read_took
    assert roles.calls == 0  # E31 is called by the help worker after commit, never by the command
    roles.delay_s = 0.0
    harness.drain()
    listed = json_of(harness.client.get("/v1/help/broadcasts", headers=headers("mara")))
    assert [str(dig(r, "label")) for r in rows(listed, "items", 0, "roles")] == ["First responder"]


@pytest.mark.trace("T-E26-001", "I-H2", "rule-3")
def test_every_optional_provider_hanging_keeps_the_press_fast(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, all_slow(2.0))
    started = time.monotonic()
    opened = driver.open("mara", open_body("crewmate", "tomas", place="airlock"))
    assert opened.status_code == 201, opened.text
    session = json_of(opened)
    for node, index in (
        ("air_safe", 0),
        ("responding", 0),
        ("severe_bleeding_check", 1),
        ("what_happened", 6),
    ):
        session = driver.ok("mara", session, "answer", node_id=node, answer_index=index)
    pressed = harness.client.post(
        f"/v1/help-sessions/{session['help_session_id']}/ground-messages",
        json={},
        headers=headers("mara", "tell-slow-0001"),
    )
    assert pressed.status_code == 201, pressed.text
    assert time.monotonic() - started < 3.0
    assert dig(session, "node", "id") == "eye_injury"


@pytest.mark.trace("T-E26-001", "I-H2", "rule-3")
def test_the_ground_composer_is_read_after_the_press_commits(tmp_path: Path) -> None:
    composer = Composer()
    harness, driver = _harness(tmp_path, Providers(roster=Roster(), composer=composer))
    session = json_of(driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1")))
    pressed = harness.client.post(
        f"/v1/help-sessions/{session['help_session_id']}/ground-messages",
        json={},
        headers=headers("tomas", "tell-ground-0001"),
    )
    assert pressed.status_code == 201, pressed.text
    queued = [e for e in harness.store.events if e.type.value == "ground_message.changed"]
    assert len(queued) == 1
    # The outbox copy is the snapshot fallback; E29's catch-up only shapes the response, read after commit.
    assert len(composer.snapshots) == 1


@pytest.mark.trace("T-E26-002", "I-H2", "R15", "rule-3")
def test_answer_commits_and_returns_without_waiting_for_the_assistant(tmp_path: Path) -> None:
    assistant = _SlowAssistant("waiting")
    harness, driver = _harness(tmp_path, Providers(roster=Roster(), assistant=assistant))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    session = driver.ok("mara", session, "ask_assistant", text="what now")  # held at triage
    started = time.monotonic()
    session = driver.ok("mara", session, "answer", node_id="air_safe", answer_index=1)
    assert time.monotonic() - started < 1.0
    assert dig(session, "node", "id") == "toxic_exposure"
    assert assistant.started == []
    assistant.delay_s = 0.0
    harness.drain()
    assert [text for _, text in assistant.started] == ["what now"]
    shown = json_of(driver.get("mara", session["help_session_id"]))
    started = time.monotonic()
    driver.ok("mara", shown, "switch_flow", flow_id="burns")
    assert time.monotonic() - started < 1.0
    assert assistant.cancelled == []
    harness.drain()
    assert assistant.cancelled == assistant.turn_ids


@pytest.mark.trace("T-E26-055", "I-H13", "rule-4")
def test_the_assistant_in_a_crew_session_cannot_answer_or_close(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas", place="airlock")))
    answer = driver.act("assistant", session, "answer", node_id="air_safe", answer_index=1)
    assert answer.status_code == 403 and json_of(answer)["code"] == "ACTION_NOT_ALLOWED"
    close = driver.act("assistant", session, "close", disposition="false_alarm")
    assert close.status_code == 403 and json_of(close)["code"] == "ACTION_NOT_ALLOWED"
    pressed = driver.open("assistant", open_body("crewmate", "ilya"))
    assert pressed.status_code == 403
    shown = json_of(driver.get("mara", session["help_session_id"]))
    assert shown["state"] == "open" and dig(shown, "node", "id") == "air_safe"
    assert shown["revision"] == session["revision"]


@pytest.mark.trace("T-E26-017", "I-H12", "AGENTS-6")
def test_a_late_offline_untick_never_clears_a_newer_tick(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    for node, index in (
        ("air_safe", 0),
        ("responding", 0),
        ("severe_bleeding_check", 1),
        ("what_happened", 6),
    ):
        session = driver.ok("mara", session, "answer", node_id=node, answer_index=index)
    session = driver.ok("ilya", session, "join")
    session = driver.ok("ilya", session, "tick_step", flow_id="eye_injury", step_index=0)
    ilya_view = session
    harness.advance(60)
    session = driver.ok("mara", session, "untick_step", flow_id="eye_injury", step_index=0)
    harness.advance(10)
    session = driver.ok("mara", session, "tick_step", flow_id="eye_injury", step_index=0)
    late = driver.post_action(
        "ilya",
        ilya_view,
        "untick_step",
        {"flow_id": "eye_injury", "step_index": 0},
        at=T0 + timedelta(seconds=10),
    )
    assert late.status_code == 200
    assert dig(json_of(late), "node", "flow", "immediate_steps", 0, "ticked_at") is not None
    assert json_of(late)["revision"] == session["revision"]
    late_tick = driver.post_action(
        "ilya",
        session,
        "tick_step",
        {"flow_id": "eye_injury", "step_index": 1},
        at=T0 + timedelta(seconds=20),
    )
    assert late_tick.status_code == 200
    harness.advance(5)
    undone = driver.ok("mara", json_of(late_tick), "untick_step", flow_id="eye_injury", step_index=1)
    older = driver.post_action(
        "ilya", undone, "tick_step", {"flow_id": "eye_injury", "step_index": 1}, at=T0 + timedelta(seconds=30)
    )
    assert dig(json_of(older), "node", "flow", "immediate_steps", 1, "ticked_at") is None


@pytest.mark.trace("T-E26-018", "T-E26-020", "I-H4", "I-H14")
def test_a_second_press_replays_its_offline_path_and_audits_a_stop(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    first = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    second = json_of(driver.open("ilya", open_body("crewmate", "tomas", offline=(("air_safe", 1, T0),))))
    assert second["help_session_id"] == first["help_session_id"]
    assert dig(second, "node", "id") == "toxic_exposure"
    assert [str(dig(h, "person_id")) for h in rows(second, "helpers")] == ["mara", "ilya"]
    assert "help.replay_stopped" not in harness.store.audit_types()
    third = json_of(driver.open("noor", open_body("crewmate", "tomas", offline=(("air_safe", 0, T0),))))
    assert dig(third, "node", "id") == "toxic_exposure"
    stopped = [a for a in harness.store.audits if a.event_type == "help.replay_stopped"]
    assert len(stopped) == 1
    assert stopped[0].safe_metadata["reason"] == "node_mismatch" and stopped[0].safe_metadata["index"] == 0
    assert stopped[0].safe_metadata["node_id"] == "air_safe"


@pytest.mark.trace("T-E26-092", "I-H16", "SYSTEM-5.8")
def test_a_retried_committed_leave_replays_its_outcome(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    session = driver.ok("ilya", session, "join")
    first = driver.post_action("ilya", session, "leave", {}, key="key_leave_0001")
    assert first.status_code == 200
    again = driver.post_action("ilya", session, "leave", {}, key="key_leave_0001")
    assert again.status_code == 200, again.text
    assert json_of(again)["revision"] == json_of(first)["revision"]
    closed = driver.ok("mara", json_of(first), "close", disposition="false_alarm")
    after_close = driver.post_action("ilya", session, "leave", {}, key="key_leave_0001")
    assert after_close.status_code == 403  # closure ended the grant: no replay of the session
    assert closed["state"] == "closed"


@pytest.mark.trace("T-E26-092", "T-E26-094", "I-H16")
def test_a_rejected_command_is_recorded_and_replayed_after_the_state_changed(tmp_path: Path) -> None:
    harness, driver = _harness(tmp_path, Providers(roster=Roster()))
    session = json_of(driver.open("mara", open_body("crewmate", "tomas")))
    before = harness.store.count("platform_command_ledger")
    wrong = driver.post_action(
        "mara", session, "answer", {"node_id": "responding", "answer_index": 0}, key="key_wrong_01"
    )
    assert wrong.status_code == 409 and json_of(wrong)["code"] == "INVALID_TRANSITION"
    assert harness.store.count("platform_command_ledger") == before + 1
    assert "help.command_rejected" in harness.store.audit_types()
    moved = driver.ok("mara", session, "answer", node_id="air_safe", answer_index=0)
    assert dig(moved, "node", "id") == "responding"
    retried = driver.post_action(
        "mara", session, "answer", {"node_id": "responding", "answer_index": 0}, key="key_wrong_01"
    )
    assert retried.status_code == 409 and json_of(retried)["code"] == "INVALID_TRANSITION"
    shown = json_of(driver.get("mara", session["help_session_id"]))
    assert dig(shown, "node", "id") == "responding" and shown["revision"] == moved["revision"]


@pytest.mark.trace("T-E26-059", "I-H13", "rule-4")
def test_e13_receives_the_help_turn_id_and_can_complete_the_turn(tmp_path: Path) -> None:
    assistant = Assistant("waiting")
    harness, driver = _harness(tmp_path, Providers(assistant=assistant, text_rules=_Rules()))
    session = json_of(driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1")))
    driver.ok("tomas", session, "ask_assistant", text="What first?")
    harness.drain()
    assert len(assistant.turn_ids) == 1
    turn_id = assistant.turn_ids[0]
    assert assistant.actors == [Actor("person", "tomas")]
    flow = pinned_docs().flows.flow("chest_pain")
    assert flow is not None
    step = flow.immediate_steps[0]
    result = harness.service.assistant.turn_completed(turn_id, HelpTurnCandidate(turn_id, step, None, (), ()))
    assert result.accepted and result.display_text == step
    shown = json_of(driver.get("tomas", session["help_session_id"]))
    assert dig(shown, "assistant", "last_text") == step
    assert dig(shown, "assistant", "status") == "waiting"
