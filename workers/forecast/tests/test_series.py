from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_forecast.adapters.settings import default_policy_path
from crew_health_forecast.application.policy import ForecastPolicy, load_policy
from crew_health_forecast.domain.series import QualifiedSeries, RawPoint, Unqualified, parse_series


def points(count: int, value: object = "1.25") -> list[RawPoint]:
    return [{"mission_day": day, "value": value} for day in range(1, count + 1)]


@pytest.mark.trace("E14-03", "R12")
@given(
    st.decimals(min_value="-1000000", max_value="1000000", allow_nan=False, allow_infinity=False, places=5)
)
def test_exact_minimum_qualifies_with_decimal_text(value: Decimal) -> None:
    policy = load_policy(default_policy_path())
    result = parse_series(points(policy.min_context_days, format(value, "f")), "/min", policy)
    assert isinstance(result, QualifiedSeries)
    assert result.context_days == policy.min_context_days
    assert result.points[-1].value == value
    assert result.unit == "/min"


@pytest.mark.trace("E14-03", "R12")
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (1, "malformed_value"),
        (1.5, "malformed_value"),
        (None, "malformed_value"),
        (True, "malformed_value"),
        ("", "malformed_value"),
        ("+1", "malformed_value"),
        ("1e2", "malformed_value"),
        (" 1", "malformed_value"),
        (".2", "malformed_value"),
        ("1.", "malformed_value"),
        ("1\n", "malformed_value"),
        ("１２", "malformed_value"),
        ("bad", "malformed_value"),
        ("NaN", "nonfinite_value"),
        ("sNaN", "nonfinite_value"),
        ("Infinity", "nonfinite_value"),
        ("-Infinity", "nonfinite_value"),
    ],
)
def test_values_are_never_coerced(policy: ForecastPolicy, value: object, reason: str) -> None:
    assert parse_series(points(policy.min_context_days, value), "1", policy) == Unqualified(reason)


@pytest.mark.trace("E14-03", "R12")
@given(st.integers(min_value=0, max_value=13))
def test_short_and_empty_inputs(count: int) -> None:
    policy = load_policy(default_policy_path())
    assert parse_series(points(count), "1", policy) == Unqualified(
        "empty" if count == 0 else "history_too_short"
    )


@pytest.mark.trace("E14-03", "R12")
@given(st.integers(min_value=1, max_value=10))
def test_oversized_inputs(extra: int) -> None:
    policy = load_policy(default_policy_path())
    assert parse_series(points(policy.max_series_points + extra), "1", policy) == Unqualified(
        "too_many_points"
    )


@pytest.mark.trace("E14-03", "R12")
@given(st.integers(min_value=1, max_value=12), st.integers(min_value=1, max_value=20))
def test_gaps_are_never_filled(index: int, gap: int) -> None:
    policy = load_policy(default_policy_path())
    raw = points(policy.min_context_days)
    for point in raw[index:]:
        point["mission_day"] += gap
    assert parse_series(raw, "1", policy) == Unqualified("history_not_contiguous")


@pytest.mark.trace("E14-03", "R12")
@given(st.integers(min_value=1, max_value=13), st.integers(min_value=0, max_value=1))
def test_duplicate_or_reversed_days(index: int, reverse: int) -> None:
    policy = load_policy(default_policy_path())
    raw = points(policy.min_context_days)
    raw[index]["mission_day"] = raw[index - 1]["mission_day"] - reverse
    assert parse_series(raw, "1", policy) == Unqualified("days_not_increasing")


@pytest.mark.trace("E14-03", "R12")
def test_context_truncation_keeps_latest_and_checks_old_history(policy: ForecastPolicy) -> None:
    policy = replace(policy, max_context_days=policy.min_context_days)
    raw = points(policy.min_context_days + 3)
    result = parse_series(raw, "1", policy)
    assert isinstance(result, QualifiedSeries)
    assert result.context_days == policy.max_context_days
    assert result.points[0].mission_day == 4
    assert result.points[-1].mission_day == len(raw)
    raw[0]["value"] = "NaN"
    assert parse_series(raw, "1", policy) == Unqualified("nonfinite_value")


@pytest.mark.trace("E14-03", "R12")
@pytest.mark.parametrize("unit", [None, "", "   "])
def test_missing_unit(policy: ForecastPolicy, unit: str | None) -> None:
    assert parse_series(points(policy.min_context_days), unit, policy) == Unqualified("unit_missing")


@pytest.mark.trace("E14-03", "R12")
@given(st.one_of(st.integers(), st.floats(), st.none(), st.text(alphabet="xyz_+e.", min_size=1, max_size=24)))
def test_malformed_value_property(value: object) -> None:
    policy = load_policy(default_policy_path())
    assert parse_series(points(policy.min_context_days, value), "1", policy) == Unqualified("malformed_value")


@pytest.mark.trace("E14-03", "R12")
@given(st.sampled_from(["NaN", "sNaN", "Infinity", "-Infinity", "+Infinity", "inf", "-inf"]))
def test_nonfinite_value_property(value: str) -> None:
    policy = load_policy(default_policy_path())
    assert parse_series(points(policy.min_context_days, value), "1", policy) == Unqualified("nonfinite_value")
