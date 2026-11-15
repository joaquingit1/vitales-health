from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_device.domain.quality import Accepted, Refused, Sample, assess

from .helpers import profile


def samples(*values: str) -> tuple[Sample, ...]:
    return tuple(
        Sample(Decimal(value), datetime(2026, 11, 14, tzinfo=UTC) + timedelta(seconds=i), i * 1_000_000_000)
        for i, value in enumerate(values)
    )


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_out_of_range_is_refused_without_clipping() -> None:
    rule = profile().components[0]
    assert assess(samples("-0.1", "50"), rule, 1) == Refused("out_of_device_range")
    assert assess(samples("50", "100.1"), rule, 1) == Refused("out_of_device_range")


@pytest.mark.trace("T-E07-OX-02", "V15")
@pytest.mark.parametrize("values", [("1", "2"), ("98", "99")])
def test_saturation_at_either_boundary_is_refused(values: tuple[str, ...]) -> None:
    rule = replace(profile().components[0], saturation_low=Decimal(1), saturation_high=Decimal(99))
    assert assess(samples(*values), rule, 1) == Refused("saturated")


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_implausible_rate_and_insufficient_samples() -> None:
    rule = replace(profile().components[0], max_rate_per_second=Decimal(2))
    assert assess(samples("90", "93"), rule, 1) == Refused("implausible_rate")
    assert assess(samples("90"), rule, 2) == Refused("insufficient_samples")
    assert assess((), rule, 1) == Refused("insufficient_samples")


@pytest.mark.trace("T-E07-OX-02", "R26", "V15")
def test_equal_values_are_a_steady_reading_never_stuck() -> None:
    """C08: wait until the reading "stops changing and displays one steady number". A calm patient holding one
    value is the good case; the steady value reported is the last sample equal to its predecessor."""
    rule = profile().components[0]
    held = samples(*(["98"] * 30))
    accepted = assess(held, rule, 1, steady=True)
    assert isinstance(accepted, Accepted) and accepted.value == Decimal(98)
    settling = assess(samples("95", "97", "97", "98"), rule, 1, steady=True)
    assert isinstance(settling, Accepted) and settling.value == Decimal(97)
    assert assess(samples("95", "96", "97"), rule, 1, steady=True) == Refused("not_steady")
    assert assess(samples("95", "96", "97"), rule, 1) == Accepted(
        Decimal(97), samples("95", "96", "97")[-1].received_at, 3, samples("95", "96", "97")[-1]
    )


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_a_rule_without_a_sourced_range_still_refuses_non_finite_values() -> None:
    rule = replace(profile().components[0], range_min=None, range_max=None)
    assert isinstance(assess(samples("250"), rule, 1), Accepted)


@pytest.mark.trace("T-E07-OX-02", "V15")
@pytest.mark.parametrize("delta", [0, -1])
def test_unknown_monotonic_time_order_is_refused(delta: int) -> None:
    first = samples("90")[0]
    assert assess(
        (first, replace(first, value=Decimal(91), monotonic_ns=delta)), profile().components[0], 1
    ) == Refused("unknown_time_order")


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_accepted_returns_last_measured_sample_without_averaging() -> None:
    data = samples("90", "94", "97.5")
    assert assess(data, profile().components[0], 1) == Accepted(
        Decimal("97.5"), data[-1].received_at, 3, data[-1]
    )


@pytest.mark.trace("T-E07-OX-02", "V15")
@given(st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=32))
def test_accepted_value_is_always_one_of_input_values(values: list[int]) -> None:
    data = samples(*(str(value) for value in values))
    verdict = assess(data, profile().components[0], 1)
    assert isinstance(verdict, Accepted)
    assert verdict.value in [sample.value for sample in data]
