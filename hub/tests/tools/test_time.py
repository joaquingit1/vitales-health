from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from crew_health.tools.domain.tool_result import ToolCall
from tests.tools.test_gateway import View, clock_of, gateway, scope  # type: ignore[import-not-found]


@pytest.mark.trace("T-E12-005", "T-E12-023", "V27", "I6")
def test_expired_anchor_prevents_handler_execution() -> None:
    gate, handler, _ = gateway()
    target = scope()
    expired = replace(
        target.deadline, anchor=replace(target.deadline.anchor, original_at=View.now - timedelta(days=1))
    )
    result = gate.execute(
        View(), replace(target, deadline=expired), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    )
    assert result.status == "denied"
    assert handler.calls == 0


@pytest.mark.trace("T-E12-005", "T-E12-065", "I2")
def test_pinned_policy_limits_calls_below_hub_ceiling() -> None:
    gate, handler, _ = gateway()
    for _ in range(8):
        assert (
            gate.execute(View(), scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status
            == "ok"
        )
    assert (
        gate.execute(View(), scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status
        == "denied"
    )
    assert handler.calls == 8


@pytest.mark.trace("T-E12-023", "V27", "I6")
def test_accumulated_elapsed_time_is_not_renewed_per_call() -> None:
    gate, handler, _ = gateway()
    view = View()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    clock_of(gate).at = View.now + timedelta(milliseconds=600)
    assert gate.execute(view, scope(), call).status == "ok"
    clock_of(gate).at = View.now + timedelta(milliseconds=1100)
    assert gate.execute(view, scope(), call).status == "denied"
    assert handler.calls == 1


@pytest.mark.trace("T-E12-023", "V27", "I6")
@pytest.mark.parametrize("fault", ["unknown_segment", "restart", "backward"])
def test_unknown_time_and_process_epoch_prevent_execution(fault: str) -> None:
    gate, handler, _ = gateway()
    target = scope()
    if fault == "unknown_segment":
        target = replace(
            target,
            deadline=replace(
                target.deadline, anchor=replace(target.deadline.anchor, segment_id="segment_missing")
            ),
        )
    elif fault == "restart":
        clock_of(gate).epoch = "epoch_b"
    else:
        clock_of(gate).at = View.now - timedelta(seconds=1)
    assert (
        gate.execute(View(), target, ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status
        == "denied"
    )
    assert handler.calls == 0


@pytest.mark.trace("T-E12-023", "I6")
@pytest.mark.parametrize("offset_ms", [-1000, 1000])
def test_shared_correction_preserves_relative_elapsed_deadline(offset_ms: int) -> None:
    from crew_health.kernel.clock import ClockCorrection

    gate, handler, _ = gateway()
    view = View()
    assert gate._corrections is not None
    gate._corrections.apply(
        view,
        ClockCorrection(
            "correction_b",
            "clock_a",
            "segment_a",
            2,
            View.now - timedelta(days=2),
            None,
            offset_ms,
            0,
            View.now,
            "operator",
        ),
    )
    clock_of(gate).at = View.now + timedelta(milliseconds=400)
    assert gate.execute(view, scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status == "ok"
    clock_of(gate).at = View.now + timedelta(milliseconds=1200)
    assert (
        gate.execute(view, scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status
        == "denied"
    )
    assert handler.calls == 1
