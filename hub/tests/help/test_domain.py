"""Help domain rules. Golden expectations were read from docs/product/RESPONDER-FLOWS.md and CONTRACTS.md 16 by
hand, not derived from the configuration under test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from help_support import ASSISTANT_IN_CREW, T0, Providers, Roster, build, config_raw, limits, pinned_docs

from crew_health.help.application.support import who_of
from crew_health.help.domain.broadcast import compose_broadcast
from crew_health.help.domain.docs import parse_flows
from crew_health.help.domain.errors import (
    HelpActionNotAllowed,
    HelpError,
    HelpSessionClosed,
    HelpStaleVersion,
)
from crew_health.help.domain.replay import ClientFlows, OfflineAnswer, replay
from crew_health.help.domain.session import (
    ActionName,
    ActorRef,
    HelpCommand,
    HelpSession,
    Opening,
    Who,
    apply,
    level_for,
    open_session,
)
from crew_health.help.domain.turns import runtime_status, turn_completed

DOCS = pinned_docs()
LIMITS = limits()
MARA = Who(ActorRef("person", "mara", "Mara Okafor"), "mara")
ILYA = Who(ActorRef("person", "ilya", "Ilya Brandt"), "ilya")
ASSISTANT = Who(ActorRef("assistant", "assistant", None), None)


def opened(subject: str | None = "tomas", flow: str | None = None) -> HelpSession:
    return open_session(
        Opening(
            help_session_id="hlp_1",
            broadcast_id="brd_1",
            now=T0,
            client_opened_at=T0,
            opener=MARA,
            opener_session_id="ses_mara",
            entry_kind="button" if flow is None else "red_flag",
            entry_ref_id=None if flow is None else "ep_1",
            subject_kind="crewmate" if subject is not None else "unknown",
            subject_person_id=subject,
            subject_display_name="Tomas Lind" if subject is not None else None,
            place="airlock",
            flow_id=flow,
            mission_id=None,
            origin_mode=None,
            environment_id=None,
        ),
        DOCS,
        LIMITS,
    )


def step(session: HelpSession, action: ActionName, who: Who = MARA, **fields: object) -> HelpSession:
    command = HelpCommand(action, T0 + timedelta(seconds=session.revision), **fields)  # type: ignore[arg-type]
    return apply(
        session,
        command,
        who,
        DOCS,
        LIMITS,
        now=T0 + timedelta(seconds=session.revision),
        if_match=session.revision,
    )


def answers(session: HelpSession, path: tuple[tuple[str, int], ...]) -> HelpSession:
    for node_id, index in path:
        session = step(session, "answer", node_id=node_id, answer_index=index)
    return session


@pytest.mark.trace("T-E26-012", "I-H3", "V62")
def test_golden_vector_reaches_eye_injury() -> None:
    session = answers(
        opened(), (("air_safe", 0), ("responding", 0), ("severe_bleeding_check", 1), ("what_happened", 6))
    )
    assert (session.node_kind, session.node_id) == ("flow", "eye_injury")
    flow = DOCS.flows.flow("eye_injury")
    assert flow is not None
    assert flow.title == "Eye injury or something in the eye"
    assert flow.immediate_steps[0] == "Do not rub the eye."
    assert flow.immediate_steps[4] == "Call the CMO."
    assert session.body_region == "eyes"
    assert level_for(session, DOCS) == ("act", "team default")
    assert [line.text for line in session.log[1:]] == [
        "Air safe",
        "Tomas Lind responding",
        "No heavy bleeding",
        "Eye injury, chemical or dust in the eye",
    ]
    assert session.revision == 5 and len(session.path) == 4


@pytest.mark.trace("T-E26-013", "I-H3", "V62")
def test_golden_vector_reaches_not_breathing_in_three_answers() -> None:
    session = answers(opened(), (("air_safe", 0), ("responding", 1), ("breathing", 1)))
    assert (session.node_kind, session.node_id) == ("flow", "not_breathing")
    flow = DOCS.flows.flow("not_breathing")
    assert flow is not None
    assert flow.title == "Not responding and not breathing normally"
    assert flow.immediate_steps[1] == "Start chest compressions now. If you are in doubt, start anyway."
    assert session.body_region is None  # heart and lungs: more than one region stays null (I-H17)
    assert level_for(session, DOCS)[0] == "emergency"


@pytest.mark.trace("T-E26-055", "rule-4")
@pytest.mark.parametrize(
    ("action", "fields"),
    [
        ("answer", {"node_id": "air_safe", "answer_index": 1}),
        ("switch_flow", {"flow_id": "not_breathing"}),
        ("tick_step", {"flow_id": "eye_injury", "step_index": 0}),
        ("call_crew", {}),
        ("start_over", {}),
        ("close", {"disposition": "false_alarm"}),
        ("set_body_region", {"body_region": None}),
    ],
)
def test_the_assistant_actor_can_never_act(
    tmp_path: Path, action: ActionName, fields: dict[str, object]
) -> None:
    """Through the application mapping: E13 inside a crew person's session stays the assistant (rule 4)."""
    harness = build(tmp_path / "help.db", Providers(roster=Roster()))
    mapped = who_of(harness.service.deps, ASSISTANT_IN_CREW, T0)
    assert mapped.ref.kind == "assistant" and mapped.person_id is None
    session = opened(flow="eye_injury")
    for who in (ASSISTANT, mapped):
        with pytest.raises(HelpActionNotAllowed):
            step(session, action, who, **fields)


@pytest.mark.trace("T-E26-016", "I-H7")
def test_closed_is_absorbing() -> None:
    closed = step(opened(), "close", disposition="resolved_on_board")
    for action, fields in (("call_crew", {}), ("join", {}), ("close", {"disposition": "other"})):
        with pytest.raises(HelpSessionClosed):
            step(closed, action, ILYA, **fields)  # type: ignore[arg-type]


@pytest.mark.trace("T-E26-017", "I-H12", "H2")
def test_a_stale_tick_without_its_flow_never_lands_on_another_flow() -> None:
    at_eye = opened(flow="eye_injury")
    seen = at_eye.revision
    switched = step(at_eye, "switch_flow", flow_id="burns")
    stale = HelpCommand("tick_step", T0, step_index=0)
    with pytest.raises(HelpStaleVersion):
        apply(switched, stale, MARA, DOCS, LIMITS, now=T0, if_match=seen)
    named = HelpCommand("tick_step", T0, flow_id="eye_injury", step_index=0)
    ticked = apply(switched, named, MARA, DOCS, LIMITS, now=T0, if_match=seen)
    assert [(t.flow_id, t.step_index) for t in ticked.ticks] == [("eye_injury", 0)]
    back = step(step(ticked, "start_over"), "switch_flow", flow_id="eye_injury")
    assert back.is_ticked("eye_injury", 0) is not None


@pytest.mark.trace("T-E26-022", "I-H17")
def test_body_region_person_choice_stands_and_start_over_resets_derived() -> None:
    eye = opened(flow="eye_injury")
    assert eye.body_region == "eyes"
    chosen = step(eye, "set_body_region", body_region=None)
    assert (chosen.body_region, chosen.body_region_source) == (None, "person")
    moved = step(chosen, "switch_flow", flow_id="burns")
    assert (moved.body_region, moved.body_region_source) == (None, "person")
    fresh = step(eye, "start_over")
    assert (fresh.body_region, fresh.body_region_source) == (None, "none")


@pytest.mark.trace("T-E26-023", "V71", "D-E26-1")
def test_opener_for_someone_else_is_first_helper_and_can_leave_and_rejoin() -> None:
    session = opened()
    assert [h.person_id for h in session.helpers] == ["mara"]
    left = step(session, "leave")
    assert left.helpers == ()
    assert compose_broadcast(left, DOCS, ()).text.endswith("Nobody has joined yet.")
    back = step(left, "join")
    assert [h.person_id for h in back.helpers] == ["mara"]
    with pytest.raises(HelpActionNotAllowed):
        step(back, "join")


@pytest.mark.trace("T-E26-030", "UC18")
def test_broadcast_ends_on_close_without_roles() -> None:
    closed = step(opened(), "close", disposition="resolved_on_board")
    view = compose_broadcast(closed, DOCS, ())
    assert view.state == "ended" and view.roles == () and view.ended_at is not None


@pytest.mark.trace("T-E26-018", "I-H14")
def test_replay_stops_at_a_release_mismatch_and_applies_nothing_after() -> None:
    flows = json.loads(config_raw("responder-flows"))
    flows["triage"][0]["answers"].reverse()
    client = ClientFlows(False, parse_flows(json.dumps(flows).encode()))
    path = (OfflineAnswer("air_safe", 0, T0), OfflineAnswer("responding", 0, T0))
    result = replay(opened(), path, MARA, DOCS, LIMITS, client=client, now=T0)
    assert result.stop_reason == "release_mismatch" and result.applied_count == 0
    assert result.session.node_id == "air_safe"
    same = replay(opened(), path, MARA, DOCS, LIMITS, client=ClientFlows(True, None), now=T0)
    assert same.stop_reason is None and same.session.node_id == "severe_bleeding_check"
    unknown = replay(opened(), path, MARA, DOCS, LIMITS, client=ClientFlows(False, None), now=T0)
    assert unknown.stop_reason == "release_unreadable" and unknown.applied_count == 0


@pytest.mark.trace("T-E26-055", "T-E26-059", "rule-4", "I-H13")
def test_turn_results_change_only_the_assistant_line() -> None:
    session = opened(flow="eye_injury")
    stopped = runtime_status(session, "stopped", DOCS, T0)
    assert stopped.log[-1].text == DOCS.presentation.assistant_stopped_text
    assert runtime_status(stopped, "stopped", DOCS, T0) == stopped
    asked = step(session, "ask_assistant", text="Rinse how long?", help_turn_id="hlt_1")
    moved = step(asked, "switch_flow", flow_id="burns")
    result, outcome = turn_completed(moved, "hlt_1", 1, "x", (), DOCS, T0)
    assert outcome in ("late", "stale") and result == moved
    shown, applied = turn_completed(
        asked, "hlt_1", asked.context_epoch, "What got in, or what hit the eye?", (), DOCS, T0
    )
    assert applied == "applied"
    assert shown.log[-1].text == "Assistant: What got in, or what hit the eye?"
    for name in ("node_id", "ticks", "helpers", "state", "crew_called_at", "path", "close"):
        assert getattr(shown, name) == getattr(asked, name), name


@pytest.mark.trace("I-H5", "bounds")
def test_log_caps_refuse_notes_but_never_block_close() -> None:
    session = replace(opened(), log_bytes=LIMITS.log_hard_cap_bytes)
    with pytest.raises(HelpError):
        step(session, "add_note", note="more")
    assert step(session, "close", disposition="other").state == "closed"
