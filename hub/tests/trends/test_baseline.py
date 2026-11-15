"""Independent numerical cases for E14 baseline selection and arithmetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.units import Quantity, Unit
from crew_health.trends.domain.baseline import (
    baseline,
    comparable,
    compare,
    effective,
    readings,
    select,
)
from crew_health.trends.domain.errors import TrendsInvariant
from crew_health.trends.domain.model import (
    BaselinePolicy,
    BaselineSummary,
    Comparison,
    InsufficientBaseline,
    Partition,
    Period,
    Sample,
    Selection,
)

AT = datetime(2026, 11, 14, 12, tzinfo=UTC)
UNIT = Unit("{score}")
PARTITION = Partition("per_1", "mis_1", "SIM")


def sample(number: int, value: str = "10", *, day: int | None = None) -> Sample:
    observed_at = AT - timedelta(days=number)
    return Sample(
        observation_id=f"obs_{number}",
        patient_id="per_1",
        operator_id="per_1",
        mission_id="mis_1",
        episode_id=None,
        origin_mode="SIM",
        code="score",
        quantity=Quantity(Decimal(value), UNIT),
        quality_status="accepted",
        acquisition_method="self_report",
        device_id=None,
        context=(),
        supersedes=None,
        observed=TimeInput(observed_at, "clk_1", "seg_1", 1, "hub_corrected", "SIM"),
        observed_at=observed_at,
        time_state="known",
        mission_day=20 - number if day is None else day,
    )


# Independent numerical fixture: an exact comparison with no claimed source.
EXACT = Comparison(Decimal(0), "team default", (), (), "clinical review")


def policy(*, method: Literal["median", "mean"] = "median", minimum: int = 3) -> BaselinePolicy:
    return BaselinePolicy(method, 7, minimum, "independent numerical fixture", "catalog_test", 1, EXACT)


def calculate(
    samples: tuple[Sample, ...], *, minimum: int = 3, method: Literal["median", "mean"] = "median"
) -> BaselineSummary | InsufficientBaseline:
    selected = select(samples, PARTITION, AT)
    today, _ = readings(selected, 20)
    assert today is not None
    return baseline(
        selected, today, policy(method=method, minimum=minimum), (), exclusion_policy="periods.v1"
    )


@pytest.mark.trace("E14-01", "R11", "R12", "V07")
@pytest.mark.parametrize(
    ("values", "method", "expected"),
    [
        (("9", "1", "7", "3", "5"), "median", "5"),
        (("2", "8", "4", "6"), "median", "5"),
        (("1", "2", "6"), "mean", "3"),
    ],
)
def test_golden_aggregation_excludes_current_reading(
    values: tuple[str, ...],
    method: Literal["median", "mean"],
    expected: str,
) -> None:
    rows = (sample(0, "999"),) + tuple(sample(index + 1, value) for index, value in enumerate(values))
    result = calculate(rows, method=method)
    assert isinstance(result, BaselineSummary)
    assert result.value == Quantity(Decimal(expected), UNIT)
    assert result.sample_count == len(values)
    assert "obs_0" not in result.included_ids
    assert ("obs_0", "current_reading") in {(item.observation_id, item.reason) for item in result.exclusions}


@pytest.mark.trace("E14-01", "V07")
def test_minimum_boundary_never_returns_a_default() -> None:
    rows = (sample(0), sample(1), sample(2))
    insufficient = calculate(rows)
    assert isinstance(insufficient, InsufficientBaseline)
    assert insufficient.sample_count == 2
    assert not hasattr(insufficient, "value")
    sufficient = calculate(rows + (sample(3),))
    assert isinstance(sufficient, BaselineSummary)
    assert sufficient.sample_count == 3


@pytest.mark.trace("V09", "V47", "R11")
@pytest.mark.parametrize(
    "other",
    [
        replace(sample(1), origin_mode="LIVE"),
        replace(sample(1), patient_id="per_2"),
        replace(sample(1), mission_id="mis_2"),
    ],
)
def test_other_partitions_cannot_replace_or_contaminate_a_reading(other: Sample) -> None:
    rows = (sample(0), sample(1, "1"), sample(2, "3"), sample(3, "5"))
    before = calculate(rows)
    other = replace(
        other, observation_id="obs_foreign", supersedes="obs_0", quantity=Quantity(Decimal("900"), UNIT)
    )
    after = calculate(rows + (other,))
    assert isinstance(before, BaselineSummary)
    assert isinstance(after, BaselineSummary)
    assert before.value == after.value == Quantity(Decimal("3"), UNIT)
    assert ("obs_foreign", "other_partition") in {
        (item.observation_id, item.reason) for item in after.exclusions
    }


@pytest.mark.trace("E14-01", "R11")
@pytest.mark.parametrize(
    "other",
    [
        replace(sample(1), acquisition_method="device"),
        replace(sample(1), device_id="dev_1"),
        replace(sample(1), operator_id="per_2"),
        replace(sample(1), context=(("position", "seated"),)),
    ],
)
def test_exact_context_excludes_each_cohort_mismatch(other: Sample) -> None:
    result = calculate((sample(0), replace(other, observation_id="obs_mismatch")), minimum=1)
    assert isinstance(result, InsufficientBaseline)
    assert result.sample_count == 0
    assert ("obs_mismatch", "cohort_mismatch") in {
        (item.observation_id, item.reason) for item in result.exclusions
    }


@pytest.mark.trace("E14-01", "R11")
def test_window_unit_episode_and_period_exclusions_have_reasons() -> None:
    current = sample(0)
    rows = (
        current,
        sample(8),
        replace(sample(1), quantity=Quantity(Decimal("10"), Unit("s"))),
        replace(sample(2), episode_id="eps_1"),
        sample(3),
        sample(4),
        sample(5),
        replace(sample(6), observed_at=AT),
    )
    selected = select(rows, PARTITION, AT)
    result = baseline(
        selected,
        current,
        policy(minimum=1),
        (Period(AT - timedelta(days=4), AT - timedelta(days=3)), Period(AT - timedelta(days=3), None)),
        exclusion_policy="episodes.v1",
    )
    assert isinstance(result, BaselineSummary)
    assert result.included_ids == ("obs_5",)
    assert {(item.observation_id, item.reason) for item in result.exclusions} >= {
        ("obs_8", "outside_window"),
        ("obs_1", "unit_mismatch"),
        ("obs_2", "episode_linked"),
        ("obs_3", "episode_period"),
        ("obs_4", "episode_period"),
        ("obs_6", "outside_window"),
    }
    assert result.exclusion_policy == "episodes.v1"


@pytest.mark.trace("D02", "V31")
def test_supersession_chain_preserves_lineage_and_correction_wins() -> None:
    band = replace(sample(0), observation_id="obs_band", acquisition_method="device")
    first = replace(sample(0, "11"), observation_id="obs_first", supersedes="obs_band")
    corrected = replace(sample(0, "12"), observation_id="obs_corrected", supersedes="obs_first")
    result = effective((corrected, band, first))
    assert len(result.samples) == 1
    assert result.samples[0].observation_id == "obs_corrected"
    assert result.samples[0].quantity.value == Decimal("12")
    assert result.samples[0].superseded_ids == ("obs_first", "obs_band")
    assert {(item.observation_id, item.reason) for item in result.exclusions} == {
        ("obs_band", "superseded"),
        ("obs_first", "superseded"),
    }


@pytest.mark.trace("D02", "AGENTS-3.2")
def test_supersession_cycles_terminate_and_do_not_publish_a_reading() -> None:
    a = replace(sample(1), supersedes="obs_2")
    b = replace(sample(2), supersedes="obs_1")
    selected = effective((a, b))
    assert selected.samples == ()
    assert len(selected.exclusions) == 2


@pytest.mark.trace("V14", "R11")
def test_future_unknown_and_unaccepted_samples_never_become_current() -> None:
    rows = (
        sample(1),
        replace(sample(0), observation_id="obs_unknown", time_state="unknown", observed_at=None),
        replace(sample(0), observation_id="obs_future", observed_at=AT + timedelta(seconds=1)),
        replace(sample(0), observation_id="obs_replay", time_state="replay"),
        replace(sample(0), observation_id="obs_bad", quality_status="uncertain"),
    )
    selected = select(rows, PARTITION, AT)
    today, latest = readings(selected, 20)
    assert today is None
    assert latest == sample(1)
    assert latest.observed_at == AT - timedelta(days=1)
    assert {(item.observation_id, item.reason) for item in selected.exclusions} == {
        ("obs_unknown", "time_unknown"),
        ("obs_future", "future_time"),
        ("obs_replay", "time_unknown"),
        ("obs_bad", "not_accepted"),
    }


@pytest.mark.trace("V14", "V47")
def test_replay_time_is_usable_only_in_replay_partition() -> None:
    replay = replace(sample(0), origin_mode="REPLAY", time_state="replay")
    selected = select((replay,), Partition("per_1", "mis_1", "REPLAY"), AT)
    assert selected.samples == (replay,)


@pytest.mark.trace("R12", "V31")
@pytest.mark.parametrize(
    ("today", "usual", "decimals", "expected"),
    [
        ("10.04", "10", 1, "within_usual"),
        ("10.05", "10", 1, "within_usual"),
        ("10.06", "10", 1, "above_usual"),
        ("9.94", "10", 1, "below_usual"),
    ],
)
def test_comparison_uses_half_even_at_display_precision(
    today: str, usual: str, decimals: int, expected: str
) -> None:
    state = compare(Quantity(Decimal(today), UNIT), Quantity(Decimal(usual), UNIT), decimals, EXACT)
    assert state == expected


@pytest.mark.trace("R12", "V31")
@pytest.mark.parametrize(
    ("today", "expected"),
    [("61", "within_usual"), ("62", "within_usual"), ("63", "above_usual"), ("57", "below_usual")],
)
def test_a_configured_band_widens_within_usual_symmetrically(today: str, expected: str) -> None:
    band = replace(EXACT, band=Decimal(2))
    assert compare(Quantity(Decimal(today), UNIT), Quantity(Decimal("60"), UNIT), 0, band) == expected


@pytest.mark.trace("R12", "AGENTS-3.5")
@pytest.mark.parametrize(
    "comparison",
    [
        replace(EXACT, band=Decimal("-1")),
        replace(EXACT, band=Decimal("0.5")),
        replace(EXACT, band=Decimal("NaN")),
        replace(EXACT, basis="sourced"),
    ],
)
def test_negative_finer_than_display_or_uncited_sourced_band_is_a_typed_configuration_error(
    comparison: Comparison,
) -> None:
    with pytest.raises(TrendsInvariant) as caught:
        compare(Quantity(Decimal("60"), UNIT), Quantity(Decimal("60"), UNIT), 0, comparison)
    assert isinstance(caught.value, ProblemError) and caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.reason == "comparison_band_invalid"


@pytest.mark.trace("R12", "V09", "V47")
def test_comparable_keeps_only_the_newest_unlinked_readings_context_unit_and_episode_rules() -> None:
    rest = sample(4, "60")
    exercise = replace(sample(3, "150"), observation_id="obs_exercise", context=(("activity", "exercise"),))
    linked = replace(sample(2, "61"), observation_id="obs_linked", episode_id="episode_1")
    newest = sample(1, "62")
    kept = comparable(Selection((rest, exercise, linked, newest), ()), ())
    assert {s.observation_id for s in kept.samples} == {rest.observation_id, newest.observation_id}
    assert {(e.observation_id, e.reason) for e in kept.exclusions} == {
        ("obs_exercise", "cohort_mismatch"),
        ("obs_linked", "episode_linked"),
    }
    only_linked = comparable(Selection((linked,), ()), ())
    assert only_linked.samples == () and only_linked.exclusions[0].reason == "episode_linked"


@pytest.mark.trace("R11", "V31")
@given(st.permutations((0, 1, 2, 3, 4)))
def test_permuting_input_preserves_result(order: list[int]) -> None:
    rows = tuple(sample(index, str(index)) for index in range(5))
    assert calculate(tuple(rows[index] for index in order)) == calculate(rows)


@pytest.mark.trace("R11", "V09", "V47")
@given(st.integers(min_value=-100000, max_value=100000), st.booleans())
def test_adding_other_origin_or_cohort_never_changes_usual(value: int, origin: bool) -> None:
    rows = (sample(0), sample(1, "1"), sample(2, "3"), sample(3, "5"))
    foreign = (
        replace(sample(4, str(value)), origin_mode="LIVE")
        if origin
        else replace(sample(4, str(value)), device_id="dev_other")
    )
    before, after = calculate(rows), calculate(rows + (foreign,))
    assert isinstance(before, BaselineSummary) and isinstance(after, BaselineSummary)
    assert after.value == before.value


@pytest.mark.trace("R11", "AGENTS-1.5", "AGENTS-1.8")
@given(st.lists(st.integers(min_value=-10000, max_value=10000), max_size=6))
def test_insufficient_has_no_value_and_sufficient_preserves_decimal_and_unit(values: list[int]) -> None:
    rows = (sample(0),) + tuple(sample(index + 1, str(value)) for index, value in enumerate(values))
    result = calculate(rows)
    if len(values) < 3:
        assert isinstance(result, InsufficientBaseline)
        assert not hasattr(result, "value")
    else:
        assert isinstance(result, BaselineSummary)
        assert isinstance(result.value.value, Decimal)
        assert result.value.unit == UNIT


@pytest.mark.trace("AGENTS-1.5", "R11")
def test_zero_is_a_real_baseline() -> None:
    result = calculate((sample(0), sample(1, "0"), sample(2, "0"), sample(3, "0")))
    assert isinstance(result, BaselineSummary)
    assert result.value.value == Decimal("0")


@pytest.mark.trace("E14-01", "R11", "V31")
def test_exclusion_evidence_is_globally_ordered_by_observation_time() -> None:
    rows = (
        sample(0),
        replace(sample(2), acquisition_method="device"),
        replace(sample(5), quality_status="rejected"),
        sample(8),
    )
    result = calculate(rows, minimum=1)
    assert tuple(item.observation_id for item in result.exclusions) == ("obs_8", "obs_5", "obs_2", "obs_0")


@pytest.mark.trace("E14-01", "R11", "V07")
def test_window_start_is_inclusive_and_period_end_is_exclusive() -> None:
    rows = (sample(0), sample(7), sample(2))
    selected = select(rows, PARTITION, AT)
    result = baseline(
        selected,
        rows[0],
        policy(minimum=1),
        (Period(AT - timedelta(days=3), AT - timedelta(days=2)),),
        exclusion_policy="episodes.v1",
    )
    assert isinstance(result, BaselineSummary)
    assert result.included_ids == ("obs_7", "obs_2")


@pytest.mark.trace("V14", "R11")
def test_known_time_without_an_observation_instant_is_not_a_reading() -> None:
    row = replace(sample(0), observed_at=None)
    selected = select((row,), PARTITION, AT)
    assert selected.samples == ()
    assert selected.exclusions[0].reason == "future_time"


@pytest.mark.trace("R11", "V31")
def test_latest_current_day_uses_observation_id_to_break_equal_time_ties() -> None:
    rows = (replace(sample(0), observation_id="obs_a"), replace(sample(0), observation_id="obs_z"))
    today, latest = readings(select(rows, PARTITION, AT), 20)
    assert today is not None and today.observation_id == "obs_z"
    assert latest == today


@pytest.mark.trace("R11", "AGENTS-3.5")
@pytest.mark.parametrize(
    ("policy", "reason", "code"),
    [
        (
            BaselinePolicy("median", 14, 0, "team default", "catalog_1", 0, EXACT),
            "baseline_policy_invalid",
            "CONFIG_INVALID",
        ),
        (
            BaselinePolicy("median", 0, 1, "team default", "catalog_1", 0, EXACT),
            "baseline_policy_invalid",
            "CONFIG_INVALID",
        ),
    ],
)
def test_invalid_baseline_policy_is_a_typed_configuration_problem(
    policy: BaselinePolicy, reason: str, code: str
) -> None:
    rows = select((sample(1), sample(0)), PARTITION, AT)
    with pytest.raises(TrendsInvariant) as caught:
        baseline(rows, rows.samples[-1], policy, (), exclusion_policy="episodes.v1")
    assert caught.value.reason == reason and caught.value.code is ErrorCode(code)


@pytest.mark.trace("R12", "AGENTS-3.5")
def test_comparing_different_units_is_a_typed_internal_problem() -> None:
    with pytest.raises(TrendsInvariant) as caught:
        compare(Quantity(Decimal(1), UNIT), Quantity(Decimal(1), Unit("/min")), 0, EXACT)
    assert caught.value.reason == "comparison_input_invalid" and caught.value.code is ErrorCode.INTERNAL_ERROR
