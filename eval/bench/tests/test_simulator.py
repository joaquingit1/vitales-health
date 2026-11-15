"""T-E18-27, T-E18-31, T-E18-33: no fabrication or inferred time."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.units import Quantity, Unit
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.application.ports import Simulator
from crew_health_bench.domain.cases import (
    CapabilityMatch,
    QuestionMatch,
    RevealTable,
    SimAnswer,
    SimMeasurement,
)
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.labels import check_preconditions
from crew_health_bench.domain.simulator import LookupSimulator
from crew_health_bench.domain.timing import elapsed

pytestmark = [pytest.mark.e18, pytest.mark.trace("R05", "R22", "V34")]


@pytest.mark.v34
@given(st.text(max_size=80))
def test_t_e18_27_empty_table_never_invents(question: str) -> None:
    simulator = LookupSimulator(RevealTable((), ()))
    assert simulator.answer(question, ()).state == "unknown"
    assert simulator.simulator_unmatched == 1
    assert simulator.measure(question).outcome == "unavailable"


def test_t_e18_27_order_negation_and_quantity() -> None:
    answer = SimAnswer("present")
    value = Quantity(Decimal(1), Unit("1"))
    table = RevealTable(
        (QuestionMatch(("known",), ("not",), answer),),
        (CapabilityMatch("test", SimMeasurement("accepted", value)),),
    )
    simulator: Simulator = LookupSimulator(table)
    assert simulator.answer("KNOWN", ()) == answer
    assert simulator.answer("not known", ()).state == "unknown"
    assert simulator.measure("test").value == value
    with pytest.raises(BenchError):
        replace(answer, state="declined", value=value)
    with pytest.raises(BenchError):
        SimMeasurement("accepted")
    with pytest.raises(BenchError):
        SimMeasurement("failed")


def test_t_e18_33_relative_time_uses_epoch_and_monotonic_only() -> None:
    clock = MemoryClock(datetime(2026, 1, 1, tzinfo=UTC), 0, "epoch")
    start = (clock.process_epoch(), clock.monotonic_ns())
    clock.at += timedelta(days=1)
    clock.monotonic = 1000000000
    assert elapsed(start, clock).value == Decimal(1)
    clock.epoch = "new_epoch"
    with pytest.raises(BenchError):
        elapsed(start, clock)


def test_t_e18_31_no_default_for_missing_supply_limit() -> None:
    from crew_health_bench.domain.cases import ConfigPrecondition

    required = (ConfigPrecondition("warning-rules", "/below_uses", "set"),)
    assert not check_preconditions(required, {"warning-rules": {"below_uses": None}})
    assert check_preconditions(required, {"warning-rules": {"below_uses": 1}})
    with pytest.raises(BenchError, match="config_reference_missing"):
        check_preconditions(required, {})
