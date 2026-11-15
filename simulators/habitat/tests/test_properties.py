"""Properties: no NaN or infinity, units always attached, monotonic timestamps, bounded drift."""

import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.devicecfg import DeviceKind
from crew_health_sim_habitat.devices import DEVICE_STATUSES, Device, DeviceReading
from crew_health_sim_habitat.errors import NonFiniteValueError
from crew_health_sim_habitat.payload import format_instant
from crew_health_sim_habitat.runner import build_devices, simulate
from crew_health_sim_habitat.units import Quantity
from crew_health_sim_habitat.world import Truth

QUANTITIES = (
    "co2_ppm",
    "pressure_kpa",
    "ppo2_kpa",
    "co_ppm",
    "nh3_ppm",
    "tvoc_ugm3",
    "pm1_ugm3",
    "pm2_5_ugm3",
    "pm10_ugm3",
    "temperature_c",
    "rh_percent",
    "laeq_dba",
    "dose_rate_ugy_h",
    *(f"band_{b}_db" for b in ("63", "125", "250", "500", "1k", "2k", "4k", "8k")),
)
finite = st.floats(min_value=0.0, max_value=1.0e6, allow_nan=False, allow_infinity=False)


@st.composite
def truths(draw: st.DrawFn) -> Truth:
    values: Truth = {q: draw(finite) for q in QUANTITIES}
    values["rh_percent"] = draw(st.floats(min_value=0.0, max_value=100.0))
    values["pressure_kpa"] = draw(st.floats(min_value=1.0, max_value=200.0))
    if draw(st.booleans()):
        values["dose_rate_ugy_h"] = None
    return values


def _check(kind: DeviceKind, readings: list[DeviceReading]) -> None:
    for item in readings:
        quantity = item.quantity
        spec = kind.output(item.output)
        assert math.isfinite(quantity.value)
        assert quantity.unit == spec.unit
        assert spec.range_min <= quantity.value <= spec.range_max
        assert item.status in DEVICE_STATUSES


@settings(max_examples=150, deadline=None)
@given(truth=truths(), t_s=st.floats(min_value=0.0, max_value=400 * 86400.0), seed=st.integers(0, 2**32))
def test_every_device_output_is_finite_with_its_unit(
    loaded: Loaded, truth: Truth, t_s: float, seed: int
) -> None:
    for kind in loaded.kinds.values():
        device = Device("sim_prop", kind, (), random.Random(seed))
        for step in range(3):
            _check(kind, device.sample(t_s + step * kind.interval_s, truth))


@settings(max_examples=40, deadline=None)
@given(
    age_days=st.floats(min_value=0.0, max_value=3650.0), true_value=st.floats(min_value=0.0, max_value=5000.0)
)
def test_drift_and_sensitivity_loss_are_bounded(loaded: Loaded, age_days: float, true_value: float) -> None:
    for kind in loaded.kinds.values():
        for spec in kind.outputs:
            if spec.quantity in (
                "pressure_rate",
                "voc_signal",
                "geiger_counts",
                "geiger_dose_estimate",
                "o2_percent",
            ):
                continue
            quiet = replace(
                kind,
                outputs=(replace(spec, noise_abs=0.0, noise_rel=0.0, resolution=0.0),),
                warmup_s=0.0,
                cross_sensitivity={},
                extras=kind.extras,
            )
            device = Device("sim_prop", quiet, (), random.Random(1))
            truth: Truth = {"rh_percent": 0.0, spec.quantity: true_value}
            got = device.sample(age_days * 86400.0, truth)[0].quantity.value
            drift = spec.offset_drift_per_day * age_days
            gain_low = max(0.0, 1.0 - spec.sensitivity_loss_per_day * age_days)
            low = min(spec.range_max, max(spec.range_min, true_value * gain_low - drift))
            high = min(spec.range_max, max(spec.range_min, true_value + drift))
            assert low - 1e-9 <= got <= high + 1e-9, spec.name


@settings(max_examples=10, deadline=None)
@given(seed=st.integers(0, 2**31), scenario_index=st.integers(0, 9))
def test_timestamps_increase_and_truth_stays_finite(loaded: Loaded, seed: int, scenario_index: int) -> None:
    scenario_id = sorted(loaded.scenarios)[scenario_index]
    scenario = replace(loaded.scenario(scenario_id), seed=seed, duration_s=1800.0)
    last: dict[tuple[str, str], float] = {}
    for tick in simulate(scenario, build_devices(scenario, loaded.wiring, loaded.kinds)):
        assert all(v is None or math.isfinite(v) for v in tick.truth.values())
        for reading in tick.readings:
            key = (reading.device_id, reading.output)
            assert reading.t_s > last.get(key, -1.0)
            assert reading.quantity.unit
            last[key] = reading.t_s


@given(value=st.sampled_from([math.nan, math.inf, -math.inf]))
def test_a_quantity_refuses_non_finite_values(value: float) -> None:
    with pytest.raises(NonFiniteValueError):
        Quantity(value, "ppm")


def test_a_quantity_needs_a_unit() -> None:
    with pytest.raises(NonFiniteValueError):
        Quantity(1.0, "")


@given(offset=st.integers(min_value=0, max_value=10**10), micro=st.integers(0, 999_999))
def test_instants_round_trip_as_utc_z(offset: int, micro: int) -> None:
    moment = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=offset, microseconds=micro)
    text = format_instant(moment)
    assert text.endswith("Z")
    assert datetime.fromisoformat(text.replace("Z", "+00:00")) == moment
