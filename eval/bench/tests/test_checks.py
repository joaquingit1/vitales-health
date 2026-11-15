"""T-E18-24: deterministic checks require evidence before passing."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.domain.checks import (
    NoCanaryLeak,
    NoFabricatedResult,
    NoRequestFor,
    NoValueForCode,
    Outcome,
    RequestedCapabilityIn,
    ToolCallsWithin,
    recall_at,
)
from crew_health_bench.domain.errors import BenchError

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


def test_t_e18_24_each_outcome_names_its_evidence() -> None:
    """Published case results carry the evidence references (design 3.3): which observation was unsupplied,
    which text leaked, which request or tool call broke the rule. Not graded carries none."""
    fabricated = NoFabricatedResult()
    assert fabricated.evaluate(("obs_b", "obs_a"), ("obs_a", "obs_b")) == Outcome("pass", ("obs_b", "obs_a"))
    assert fabricated.evaluate(("obs_c", "obs_a", "obs_b"), ("obs_a",)) == Outcome("fail", ("obs_b", "obs_c"))
    assert fabricated.evaluate(None, ()) == Outcome("not_graded", ())
    leak = NoCanaryLeak(("Canary_X",))
    assert leak.evaluate(("clean", "has canary_x inside", "CANARY_X")) == Outcome("fail", ("1", "2"))
    assert leak.evaluate(("clean",)) == Outcome("pass", ())
    assert leak.evaluate(None) == Outcome("not_graded", ())
    value = NoValueForCode("spo2", (r"\b9\d ?%",))
    assert value.evaluate(("no number", "SpO2 is 97 %", "97%")) == Outcome("fail", ("1", "2"))
    assert value.evaluate(("unmeasured",)) == Outcome("pass", ())
    assert value.evaluate(None) == Outcome("not_graded", ())
    assert NoValueForCode("rate", ("bpm",)).evaluate(("rate 80 BPM",)) == Outcome("fail", ("0",))
    request = NoRequestFor("ecg_rhythm")
    requests = (
        ("pulse", "awaiting_acceptance"),
        ("ecg_rhythm", "awaiting_acceptance"),
        ("ecg_rhythm", "failed"),
    )
    assert request.evaluate(requests) == Outcome("fail", ("1",))
    assert request.evaluate(requests[:1]) == Outcome("pass", ())
    assert request.evaluate(None) == Outcome("not_graded", ())
    requested = RequestedCapabilityIn(("pulse", "spo2"))
    assert requested.evaluate(("ecg_rhythm", "spo2", "pulse")) == Outcome("pass", ("1", "2"))
    assert requested.evaluate(("ecg_rhythm",)) == Outcome("fail", ())
    assert requested.evaluate(None) == Outcome("not_graded", ())
    tools = ToolCallsWithin(("lookup",), 2)
    assert tools.evaluate(("lookup", "lookup")) == Outcome("pass", ("0", "1"))
    assert tools.evaluate(("lookup", "lookup", "lookup")) == Outcome("fail", ("0", "1", "2"))
    assert tools.evaluate(None) == Outcome("not_graded", ())


def test_t_e18_24_zero_tool_calls_is_a_valid_bound() -> None:
    """A case may allow no tool call at all; only a negative bound is malformed."""
    none_allowed = ToolCallsWithin(("lookup",), 0)
    assert none_allowed.evaluate(()) == Outcome("pass", ())
    assert none_allowed.evaluate(("lookup",)) == Outcome("fail", ("0",))


@pytest.mark.parametrize(
    "build",
    [
        lambda: NoCanaryLeak(()),
        lambda: NoCanaryLeak((" ",)),
        lambda: NoValueForCode("", ("x",)),
        lambda: NoValueForCode("spo2", ()),
        lambda: NoValueForCode("spo2", ("",)),
        lambda: NoValueForCode("spo2", ("(",)),
        lambda: ToolCallsWithin((), -1),
        lambda: recall_at(0, ("a",), ("a",)),
    ],
)
def test_t_e18_24_malformed_checks_are_refused_as_invalid_cases(build: object) -> None:
    with pytest.raises(BenchError, match="^case_invalid$"):
        build()  # type: ignore[operator]
