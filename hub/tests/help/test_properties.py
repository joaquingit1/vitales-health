"""Hypothesis properties of the help domain (E26 3.8 invariants, AGENTS rule 4)."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from help_support import T0
from hypothesis import given, settings
from hypothesis import strategies as st
from test_domain import DOCS, ILYA, LIMITS, MARA, opened

from crew_health.help.domain.docs import normalize_text
from crew_health.help.domain.errors import HelpError, HelpSessionClosed
from crew_health.help.domain.session import ActionName, HelpCommand, HelpSession, apply, level_for
from crew_health.help.domain.turn_guard import (
    AssistantCandidate,
    GuardContext,
    TextRules,
    check,
)
from crew_health.help.domain.turns import runtime_status, turn_completed, turn_failed, turn_started

FLOW_IDS = sorted(f.id for f in DOCS.flows.flows)
TRIAGE_IDS = sorted(n.id for n in DOCS.flows.triage)
ACTIONS: tuple[ActionName, ...] = (
    "answer",
    "switch_flow",
    "tick_step",
    "untick_step",
    "call_crew",
    "join",
    "leave",
    "start_over",
    "set_body_region",
    "set_place",
    "add_note",
    "ask_assistant",
    "close",
)

command_strategy = st.builds(
    lambda action, node, index, flow, use_flow, region, who, turn: (
        HelpCommand(
            action,
            T0,
            node_id=node,
            answer_index=index,
            flow_id=flow if use_flow else None,
            step_index=index,
            body_region=region,
            place="galley",
            note="note",
            text="question",
            disposition="other",
            help_turn_id=f"hlt_{turn}",
        ),
        who,
    ),
    st.sampled_from(ACTIONS),
    st.sampled_from(TRIAGE_IDS),
    st.integers(min_value=0, max_value=8),
    st.sampled_from(FLOW_IDS),
    st.booleans(),
    st.sampled_from([None, "eyes", "head", "nowhere"]),
    st.sampled_from([MARA, ILYA]),
    st.integers(min_value=0, max_value=3),
)


def _expected_level(session: HelpSession) -> str:
    if session.node_kind == "flow":
        row = DOCS.presentation.flow_level(session.node_id)
        if row is not None:
            return row.level
    return DOCS.presentation.default_flow_level


def _run(
    commands: list[tuple[HelpCommand, object]], start: HelpSession
) -> list[tuple[HelpSession, HelpSession]]:
    session = start
    steps: list[tuple[HelpSession, HelpSession]] = []
    for index, (command, who) in enumerate(commands):
        now = T0 + timedelta(seconds=index)
        before = session
        try:
            session = apply(session, command, who, DOCS, LIMITS, now=now, if_match=session.revision)  # type: ignore[arg-type]
        except HelpSessionClosed:
            assert not before.is_open
            continue
        except HelpError:
            assert before.is_open
            continue
        steps.append((before, session))
    return steps


@pytest.mark.trace("T-E26-014", "I-H5", "I-H7")
@settings(max_examples=150, deadline=None)
@given(st.lists(command_strategy, max_size=25))
def test_every_applied_action_adds_one_revision_and_one_line_and_closed_is_absorbing(
    commands: list[tuple[HelpCommand, object]],
) -> None:
    for before, after in _run(commands, opened()):
        assert before.is_open
        if after == before:
            continue
        assert after.revision == before.revision + 1
        assert after.log[: len(before.log)] == before.log and len(after.log) == len(before.log) + 1
        assert after.log_bytes == before.log_bytes + len(after.log[-1].text.encode())


@pytest.mark.trace("T-E26-015", "I-H6")
@settings(max_examples=150, deadline=None)
@given(st.lists(command_strategy, max_size=25))
def test_level_is_the_lookup_at_every_reachable_node(commands: list[tuple[HelpCommand, object]]) -> None:
    for _, after in _run(commands, opened()):
        assert level_for(after, DOCS)[0] == _expected_level(after)


@pytest.mark.trace("T-E26-019", "I-H12")
@settings(max_examples=150, deadline=None)
@given(st.lists(command_strategy, max_size=25))
def test_a_tick_changes_only_steps_of_its_own_flow(commands: list[tuple[HelpCommand, object]]) -> None:
    for before, after in _run(commands, opened(flow="eye_injury")):
        changed = set(after.ticks) ^ set(before.ticks)
        flows = {t.flow_id for t in changed}
        assert len(flows) <= 1
        if changed:
            assert after.revision == before.revision + 1


_SAFETY_FIELDS = (
    "state",
    "node_kind",
    "node_id",
    "path",
    "ticks",
    "helpers",
    "crew_called_at",
    "broadcast_id",
    "close",
    "closed_at",
    "body_region",
    "place",
    "station_session_ids",
    "ground_message_ids",
)


@pytest.mark.trace("T-E26-055", "rule-4", "I-H13")
@settings(max_examples=150, deadline=None)
@given(
    st.lists(command_strategy, max_size=10),
    st.lists(
        st.tuples(
            st.sampled_from(["status", "started", "failed", "completed"]),
            st.sampled_from(["off", "waiting", "answering", "stopped", "unavailable"]),
            st.integers(min_value=0, max_value=3),
            st.sampled_from([None, "What got in, or what hit the eye?", "Raise the alarm now."]),
        ),
        max_size=10,
    ),
)
def test_assistant_results_never_change_safety_state(
    commands: list[tuple[HelpCommand, object]], events: list[tuple[str, str, int, str | None]]
) -> None:
    steps = _run(commands, opened(flow="eye_injury"))
    session = steps[-1][1] if steps else opened(flow="eye_injury")
    for kind, status, turn, text in events:
        before = session
        turn_id = f"hlt_{turn}"
        if kind == "status":
            session = runtime_status(session, status, DOCS, T0)  # type: ignore[arg-type]
        elif kind == "started":
            start = status if status in ("waiting", "stopped", "unavailable") else "waiting"
            session = turn_started(session, turn_id, start, None, DOCS, T0)  # type: ignore[arg-type]
        elif kind == "failed":
            session = turn_failed(session, turn_id, T0)
        else:
            session, _ = turn_completed(session, turn_id, session.context_epoch, text, (), DOCS, T0)
        for name in _SAFETY_FIELDS:
            assert getattr(session, name) == getattr(before, name), name
        assert level_for(session, DOCS) == level_for(before, DOCS)


_FLOW = DOCS.flows.flow("eye_injury")
_REFERENCES = (*_FLOW.immediate_steps, *_FLOW.questions) if _FLOW is not None else ()
_RULES = TextRules(re.compile(r"\d+\s*(mg|mcg|ml)\b"), frozenset())


@pytest.mark.trace("T-E26-058", "parser")
@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.sampled_from(_REFERENCES), min_size=1, max_size=4), st.sampled_from(["", " ", "\n", "  \t"])
)
def test_segmentation_accepts_concatenations_of_reference_texts(parts: list[str], gap: str) -> None:
    context = GuardContext(_FLOW, _RULES, frozenset(), (), LIMITS.note_max_chars)
    text = (" " + gap).join(parts)
    result = check(AssistantCandidate("hlt_p", text, None, (), ()), context)
    assert result.accepted, result.field_paths
    assert result.display_text == " ".join(parts)


@pytest.mark.trace("T-E26-058", "T-E26-056", "parser")
@settings(max_examples=150, deadline=None)
@given(st.sampled_from(_REFERENCES), st.text(alphabet="abcdefghij", min_size=1, max_size=12))
def test_segmentation_rejects_unsupported_extra_words(reference: str, extra: str) -> None:
    context = GuardContext(_FLOW, _RULES, frozenset(), (), LIMITS.note_max_chars)
    result = check(AssistantCandidate("hlt_p", f"{reference} {extra}", None, (), ()), context)
    assert not result.accepted and result.display_text is None


@pytest.mark.trace("T-E26-058", "parser")
@given(st.text(max_size=200))
def test_normalization_is_idempotent_and_whitespace_invariant(value: str) -> None:
    once = normalize_text(value)
    assert normalize_text(once) == once
    assert normalize_text(f"  {value}\t\n") == once
