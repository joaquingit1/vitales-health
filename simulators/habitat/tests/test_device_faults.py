"""Device faults and range limits: out of range, drift and a lost link, as the device reports them.

AGENTS section 2 asks for range and saturation checks on every input, and section 4 asks the twins to
include out of range, drift and disconnect. These tests pin how each one shows up in the readings.
"""

import random
from dataclasses import replace

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.devices import STATUS_OK, STATUS_SATURATED, STATUS_WARMING_UP, Device
from crew_health_sim_habitat.runner import build_devices, simulate
from crew_health_sim_habitat.scenariocfg import DeviceFault
from crew_health_sim_habitat.world import Truth


def test_a_value_below_the_range_floor_reads_saturated_at_the_floor(loaded: Loaded) -> None:
    kind = loaded.kinds["barometer"]
    spec = kind.output("pressure")
    device = Device("sim_test_baro", kind, (), random.Random(1))
    reading = device.sample(0.0, {"pressure_kpa": spec.range_min / 3.0})[0]
    assert reading.quantity.value == spec.range_min
    assert reading.status == STATUS_SATURATED


def test_noise_around_an_in_range_floor_value_stays_valid(loaded: Loaded) -> None:
    kind = loaded.kinds["ec_co"]
    device = Device("sim_test_co", kind, (), random.Random(1))
    statuses = {
        device.sample(kind.warmup_s + i * kind.interval_s, {"co_ppm": 0.0})[0].status for i in range(500)
    }
    assert statuses == {STATUS_OK}


def test_the_fault_scenario_pushes_the_barometer_out_of_range(loaded: Loaded) -> None:
    scenario = loaded.scenario("station_sensor_faults")
    fault = next(f for f in scenario.faults if f.device_id == "sim_baro_cabin" and f.fault == "offset")
    floor = loaded.kinds["barometer"].output("pressure").range_min
    run = replace(scenario, duration_s=fault.start_s + fault.duration_s + 60.0)
    inside = [
        r
        for tick in simulate(run, build_devices(run, loaded.wiring, loaded.kinds))
        for r in tick.readings
        if r.device_id == "sim_baro_cabin" and r.output == "pressure" and fault.active(r.t_s)
    ]
    assert inside
    assert all(r.status == STATUS_SATURATED and r.quantity.value == floor for r in inside)


def test_drift_adds_its_rate_times_the_elapsed_time(loaded: Loaded) -> None:
    kind = loaded.kinds["temp_rh"]
    spec = kind.output("temperature")
    rate_per_h, start_s = 0.5, 3600.0
    drift = DeviceFault("sim_test_th", "drift", start_s, 4 * 3600.0, rate_per_h, f"{spec.unit}/h")
    clean = Device("sim_test_th", kind, (), random.Random(3))
    drifting = Device("sim_test_th", kind, (drift,), random.Random(3))
    truth: Truth = {"temperature_c": 22.0, "rh_percent": 40.0}
    for hours in (1.0, 2.0, 3.0, 4.5):
        t_s = hours * 3600.0
        a = {r.output: r for r in clean.sample(t_s, truth)}
        b = {r.output: r for r in drifting.sample(t_s, truth)}
        assert b["humidity"].quantity == a["humidity"].quantity
        expected = rate_per_h * (t_s - start_s) / 3600.0
        # Same seed, so the two differ only by the drift and by noise that scales with the value.
        tolerance = 2 * spec.resolution + 4 * spec.noise_rel * expected + 1e-9
        drifted = b["temperature"].quantity.value - a["temperature"].quantity.value
        assert drifted == pytest.approx(expected, abs=tolerance)


def test_a_lost_link_leaves_a_gap_then_the_device_warms_up_again(loaded: Loaded) -> None:
    kind = loaded.kinds["mox_voc"]
    start_s, length_s = 4 * 3600.0, 900.0
    link = DeviceFault("sim_test_voc", "disconnect", start_s, length_s, None)
    device = Device("sim_test_voc", kind, (link,), random.Random(5))
    truth: Truth = {"tvoc_ugm3": 300.0}
    assert device.sample(start_s - kind.interval_s, truth)[0].status == STATUS_OK
    assert device.sample(start_s, truth) == []
    assert device.sample(start_s + length_s - 1.0, truth) == []
    back = start_s + length_s
    assert device.sample(back, truth)[0].status == STATUS_WARMING_UP
    assert device.sample(back + kind.warmup_s, truth)[0].status == STATUS_OK
