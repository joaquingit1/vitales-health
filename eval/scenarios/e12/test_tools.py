"""E12 tool gateway scenarios (docs/build/E12/PLAN.md, test_tools.py): denied calls never execute and leave
only a safe audit; measurement proposals keep reason, decision, capability and body region and write
nothing."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from crew_health.measurement.application.ports import CapabilityStatus, UnavailableReason
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.tool_result import ToolCall
from e12_stack import CREWMATE, capability, stack
from tests.tools.test_gateway import View, scope

PASSAGE = b'{"passage_id":"psg_a","include_parent":false}'
PROPOSAL = {
    "capability_code": "manual_pulse",
    "reason": "Palpitations reported at rest",
    "decision_affected": "Whether a review is needed today",
    "body_region": "heart",
}


def codes(call_id: str, code: str) -> ToolCall:
    return ToolCall(call_id, "get_capabilities", json.dumps({"codes": [code]}).encode())


@pytest.mark.trace("R17", "V25", "T-E12-001", "T-E12-004")
@pytest.mark.parametrize(
    ("call", "phase"),
    [
        (ToolCall("call_a", "delete_file", b"{}"), Phase.INTAKE),
        (
            ToolCall("call_a", "read_source_passage", b'{"passage_id":"psg_a","include_parent":false}'),
            Phase.INTAKE,
        ),
        (ToolCall("call_a", "get_capabilities", b'{"codes":[],"sql":"SELECT 1"}'), Phase.INTAKE),
        (ToolCall("call_a", "get_capabilities", b'{"codes":["' + CREWMATE.encode() + b'"]}'), Phase.INTAKE),
        (codes("call_a", "Ben"), Phase.INTAKE),
    ],
    ids=["unknown", "wrong_stage", "schema", "crewmate_id", "crewmate_name"],
)
def test_unknown_stage_schema_and_cross_person_calls_are_denied_and_audited_before_execution(
    call: ToolCall, phase: Phase
) -> None:
    run = stack()
    view = View()
    result = run.gateway.execute(view, replace(scope(), phase=phase), call)
    assert (result.status, result.error_code) == ("denied", "MODEL_OUTPUT_REJECTED")
    assert result.data is None
    assert [entry.event_type for entry in view.audits] == ["tools.denied"]
    recorded = str(view.audits)
    assert "delete_file" not in recorded
    assert CREWMATE not in recorded
    assert "Ben" not in recorded
    assert "SELECT" not in recorded


@pytest.mark.trace("R45", "D06", "V78", "T-E12-020")
@pytest.mark.parametrize(
    ("status", "reason"), [("available", None), ("unavailable", "not_in_kit")], ids=["ready", "no_kit"]
)
def test_measurement_proposal_keeps_reason_decision_capability_and_heart_region_and_writes_nothing(
    status: CapabilityStatus, reason: UnavailableReason | None
) -> None:
    run = stack()
    run.capabilities.items = (capability("manual_pulse", status, reason),)
    view = View()
    call = ToolCall("call_a", "propose_measurement", json.dumps(PROPOSAL).encode())
    result = run.gateway.execute(view, replace(scope(), phase=Phase.INTAKE), call)
    assert result.status == "ok", result
    assert result.data is not None
    body = json.loads(result.data)
    draft = body["draft"]
    assert {key: draft[key] for key in PROPOSAL} == PROPOSAL
    assert body["capability"] == {"code": "manual_pulse", "status": status, "unavailable_reason": reason}
    assert [entry.event_type for entry in view.audits] == ["tools.executed"]


@pytest.mark.trace("R45", "V78", "T-E12-020")
def test_a_proposal_for_an_unknown_capability_is_rejected_not_invented() -> None:
    run = stack()
    view = View()
    call = ToolCall("call_a", "propose_measurement", json.dumps(PROPOSAL).encode())
    result = run.gateway.execute(view, replace(scope(), phase=Phase.INTAKE), call)
    assert result.status in {"rejected", "denied"}
    assert result.data is None


@pytest.mark.trace("V27", "T-E12-004", "I5")
def test_a_revoked_session_mid_turn_stops_the_next_call_before_any_owner_read() -> None:
    run = stack()
    run.capabilities.items = (capability("manual_pulse", "available", None),)
    view = View()
    first = run.gateway.execute(view, scope(), codes("call_a", "manual_pulse"))
    assert first.status == "ok"
    run.sessions.live = False
    second = run.gateway.execute(view, scope(), codes("call_b", "manual_pulse"))
    assert second.status == "denied"
    assert second.data is None
