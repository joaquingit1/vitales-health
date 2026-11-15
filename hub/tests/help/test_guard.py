"""The help turn guard (E26 3.7): only configured texts are shown, any amount or medicine name is rejected, and
nothing passes when the checks cannot run. Amount pattern and medicine terms stand in for E01's TextRulesPort.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from help_support import Assistant, Driver, Providers, build, dig, json_of, open_body, rows
from test_domain import DOCS, LIMITS

from crew_health.help.application.ports import HelpTurnCandidate, ToolCall
from crew_health.help.domain.turn_guard import (
    AssistantCandidate,
    Citation,
    GuardContext,
    TextRules,
    ToolCallView,
    check,
)

FLOW = DOCS.flows.flow("eye_injury")
assert FLOW is not None
AMOUNT = re.compile(r"\d+(\.\d+)?\s*(mg|mcg|g|ml|units?|tablets?|drops?)\b", re.IGNORECASE)
RULES = TextRules(AMOUNT, frozenset({"ibuprofen", "tetracaine"}))


def ctx(flow: bool = True, rules: TextRules | None = RULES) -> GuardContext:
    return GuardContext(FLOW if flow else None, rules, frozenset(), (), LIMITS.note_max_chars)


def candidate(
    text: str | None = None,
    action: dict[str, object] | None = None,
    calls: tuple[ToolCallView, ...] = (),
    limitations: tuple[str, ...] = (),
) -> AssistantCandidate:
    return AssistantCandidate("hlt_1", text, action, calls, limitations)


@pytest.mark.trace("T-E26-052", "D25")
def test_an_ask_resolving_to_a_flow_question_shows_the_configured_text() -> None:
    question = FLOW.questions[2] if FLOW else ""
    result = check(candidate(action={"kind": "ask", "question": question.upper()}), ctx())
    assert result.accepted
    assert result.display_text == question
    assert result.citations == (Citation("flow_question", "2"),)


@pytest.mark.trace("T-E26-050", "V63", "D25")
@pytest.mark.parametrize(
    "where",
    ["text", "review note", "tool argument", "reason"],
)
def test_an_amount_anywhere_is_rejected_as_dose(where: str) -> None:
    amount = "Give 400 mg now."
    if where == "text":
        made = candidate(text=amount)
    elif where == "review note":
        made = candidate(action={"kind": "request_review", "note": amount})
    elif where == "tool argument":
        made = candidate(
            text=FLOW.immediate_steps[0] if FLOW else "", calls=(ToolCallView("get_plan", {"q": amount}),)
        )
    else:
        made = candidate(
            text=FLOW.immediate_steps[0] if FLOW else "", action={"kind": "ask", "reason": amount}
        )
    result = check(made, ctx())
    assert not result.accepted and "dose" in result.rule_ids and result.display_text is None


@pytest.mark.trace("T-E26-051", "V63")
def test_a_medicine_name_in_displayed_text_is_rejected() -> None:
    result = check(candidate(text="Use tetracaine drops."), ctx())
    assert not result.accepted and "dose" in result.rule_ids


@pytest.mark.trace("T-E26-053", "E26-02")
def test_every_output_is_rejected_at_triage() -> None:
    result = check(candidate(text=FLOW.immediate_steps[0] if FLOW else ""), ctx(flow=False))
    assert not result.accepted and "help_flow" in result.rule_ids


@pytest.mark.trace("T-E26-056", "V63", "H5")
def test_a_valid_reference_with_appended_text_is_rejected() -> None:
    text = f"{FLOW.immediate_steps[0] if FLOW else ''} Then you will be fine."
    result = check(candidate(text=text), ctx())
    assert not result.accepted and "help_flow" in result.rule_ids


@pytest.mark.trace("AGENTS-6-fail-closed", "D25")
def test_without_text_rules_nothing_passes() -> None:
    result = check(candidate(text=FLOW.immediate_steps[0] if FLOW else ""), ctx(rules=None))
    assert not result.accepted and "dose" in result.rule_ids


@pytest.mark.trace("T-E26-057", "H5")
def test_unknown_tools_and_choices_are_rejected() -> None:
    step = FLOW.immediate_steps[0] if FLOW else ""
    assert not check(candidate(text=step, calls=(ToolCallView("set_level", {}),)), ctx()).accepted
    assert not check(
        candidate(action={"kind": "ask", "question": FLOW.questions[0] if FLOW else "", "choices": ["a"]}),
        ctx(),
    ).accepted
    assert not check(candidate(action={"kind": "raise_emergency"}), ctx()).accepted


class _Rules:
    def amount_pattern(self, release_id: str) -> re.Pattern[str]:
        return AMOUNT

    def medicine_terms(self, release_id: str) -> frozenset[str]:
        return RULES.medicine_terms


@pytest.mark.trace("T-E26-054", "D25", "rule-4")
def test_a_rejected_turn_is_audited_and_never_reaches_the_log(tmp_path: Path) -> None:
    assistant = Assistant("waiting")
    harness = build(tmp_path / "help.db", Providers(assistant=assistant, text_rules=_Rules()))
    driver = Driver(harness)
    session = json_of(driver.open("tomas", open_body(flow="chest_pain", entry="red_flag", ref="ep_1")))
    asked = driver.ok("tomas", session, "ask_assistant", text="Anything for the pain?")
    turn = harness.service.reader.snapshot(str(asked["help_session_id"]))
    assert turn is not None
    with harness.store.snapshot() as snap:
        stored = harness.service.deps.sessions.get(snap, str(asked["help_session_id"]))
    assert stored is not None and stored.turn is not None
    bad = HelpTurnCandidate(
        stored.turn.help_turn_id, "Take 400 mg ibuprofen.", None, (ToolCall("get_plan", {}),), ()
    )
    result = harness.service.assistant.turn_completed(stored.turn.help_turn_id, bad)
    assert not result.accepted and "dose" in result.rule_ids
    shown = json_of(driver.get("tomas", asked["help_session_id"]))
    assert all("ibuprofen" not in str(line["text"]) for line in rows(shown, "log"))
    assert dig(shown, "assistant", "last_text") is None
    assert "help.assistant_turn_rejected" in harness.store.audit_types()
    again = harness.service.assistant.turn_completed(stored.turn.help_turn_id, bad)
    assert not again.accepted
    assert "help.assistant_turn_late" in harness.store.audit_types()
