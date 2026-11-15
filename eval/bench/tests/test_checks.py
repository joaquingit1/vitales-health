"""T-E18-24: deterministic checks require evidence before passing."""

import pytest
from crew_health_bench.domain.checks import (
    NoCanaryLeak,
    NoFabricatedResult,
    NoRequestFor,
    NoValueForCode,
    RequestedCapabilityIn,
    ToolCallsWithin,
    recall_at,
)
from crew_health_bench.domain.errors import BenchError
from hypothesis import given
from hypothesis import strategies as st

pytestmark = [pytest.mark.e18, pytest.mark.trace("V23", "V25", "V26", "V34")]


@given(st.lists(st.text(min_size=1, max_size=10), unique=True, max_size=20))
def test_t_e18_24_no_fabricated_result(ids: list[str]) -> None:
    check = NoFabricatedResult()
    assert check.evaluate(tuple(ids), tuple(ids)).outcome == "pass"
    assert check.evaluate(tuple(ids), None).outcome == "not_graded"
    if ids:
        assert check.evaluate(tuple(ids), tuple(ids[1:])).outcome == "fail"


def test_t_e18_24_privacy_and_unsupplied_value() -> None:
    check = NoCanaryLeak(("PRIVATE_VALUE",))
    assert check.evaluate(("allowed",)).outcome == "pass"
    assert check.evaluate(("private_value",)).outcome == "fail"
    assert check.evaluate(None).outcome == "not_graded"
    result = NoValueForCode("unsupplied", (r"\b42\b",))
    assert result.evaluate(("result is unavailable",)).outcome == "pass"
    assert result.evaluate(("result is 42",)).outcome == "fail"
    assert result.evaluate(None).outcome == "not_graded"


def test_t_e18_24_tool_and_measurement_checks() -> None:
    tools = ToolCallsWithin(("allowed",), 1)
    assert tools.evaluate(("allowed",)).outcome == "pass"
    assert tools.evaluate(("denied",)).outcome == "fail"
    assert tools.evaluate(("allowed", "allowed")).outcome == "fail"
    assert tools.evaluate(None).outcome == "not_graded"
    assert NoRequestFor("absent").evaluate((("absent", "unavailable"),)).outcome == "pass"
    assert NoRequestFor("absent").evaluate((("absent", "awaiting_acceptance"),)).outcome == "fail"
    assert NoRequestFor("absent").evaluate(None).outcome == "not_graded"
    assert RequestedCapabilityIn(("present",)).evaluate(("present",)).outcome == "pass"
    assert RequestedCapabilityIn(("present",)).evaluate(()).outcome == "fail"
    assert RequestedCapabilityIn(("present",)).evaluate(None).outcome == "not_graded"


def test_t_e18_24_recall_counts_all_relevant_passages() -> None:
    assert recall_at(1, ("a", "b"), ("a", "b")) == 0.5
    assert recall_at(2, ("a", "b"), ("a", "b")) == 1
    assert recall_at(1, (), ("a",)) is None
    with pytest.raises(BenchError):
        recall_at(0, (), ())
