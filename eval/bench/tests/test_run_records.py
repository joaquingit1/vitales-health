"""T-E18-19/28: pure record validation; durability remains a provider acceptance gap."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation, Grade, require_transition

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "T-E18-28")]


def generation() -> Generation:
    at = datetime(2026, 11, 14, tzinfo=UTC)
    return Generation(
        "case",
        0,
        "completed",
        at,
        at,
        "epoch",
        Quantity(Decimal(1), Unit("s")),
        "a" * 64,
        "private/output",
        "private/trace",
        0,
    )


def test_round_trip_preserves_units_and_record_identity() -> None:
    value = generation()
    assert Generation.from_record(value.record("run")) == value
    assert value.record("run")["wall"] == {"value": 1.0, "unit": "s"}
    grade = Grade("case", 0, (("NoFabricatedResult", "pass", ("observation",)),), Decimal("0.5"), 1, 0)
    row = grade.record("run")
    assert row["official_score"] == 0.5
    assert row["outcomes"] == [
        {"check": "NoFabricatedResult", "outcome": "pass", "evidence": ["observation"]}
    ]
    assert Grade("case", 0, (), None, 0, 1).record("run")["official_score"] is None


def test_record_boundaries_reject_invalid_values() -> None:
    value = generation()
    for changes in ({"status": "made_up"}, {"repeat": -1}, {"model_calls": -1}, {"process_epoch": ""}):
        with pytest.raises(BenchError):
            Generation.from_record({**value.record("run"), **changes})
    with pytest.raises(BenchError):
        replace(value, wall=Quantity(Decimal(-1), Unit("s")))
    with pytest.raises(BenchError):
        Generation.from_record({**value.record("run"), "started_at": "invalid"})
    for score in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(BenchError):
            Grade("case", 0, (), score, 0, 0)
    with pytest.raises(BenchError):
        Grade("", 0, (), None, 0, 0)
    with pytest.raises(BenchError):
        Grade("case", 0, (("check", "invented", ()),), None, 0, 0)


def test_state_machine_does_not_skip_commit_before_grade() -> None:
    for previous, following in (
        ("", "planned"),
        ("planned", "generating"),
        ("generating", "generated"),
        ("generated", "grading"),
        ("grading", "graded"),
        ("graded", "reported"),
        ("graded", "invalidated"),
        ("reported", "invalidated"),
    ):
        require_transition(previous, following)
    for previous in ("planned", "generating", "generated", "grading", "graded"):
        require_transition(previous, "aborted")
    for previous, following in (
        ("", "graded"),
        ("generating", "grading"),
        ("reported", "aborted"),
        ("invalidated", "generating"),
        ("planned", "unknown"),
    ):
        with pytest.raises(BenchError):
            require_transition(previous, following)


def test_grade_records_round_trip_and_reject_unparsed_outcomes() -> None:
    value = Grade("case", 0, (("NoFabricatedResult", "pass", ("observation",)),), Decimal("0.5"), 1, 0)
    assert Grade.from_record(value.record("run")) == value
    missing = Grade("case", 0, (), None, 0, 1)
    assert Grade.from_record(missing.record("run")) == missing
    with pytest.raises(BenchError):
        Grade.from_record({**value.record("run"), "outcomes": None})
