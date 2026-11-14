"""Environment simulator: synthetic but physically grounded sensor histories.

Owner: B3. Contract: docs/ENGINE-SPEC.md section 13.1. Used by scenario and end-to-end
tests and by the demo; never by the engine itself.

Truth model per zone (spec 13.1.2): T(t) = setpoint + diurnal sine + Ornstein-Uhlenbeck
noise (exact discretization) + thermal-failure trapezoids; RH likewise and clipped to
[0, 100]; dose rate = GCR base + SPE pulses (linear rise, exponential decay, total dose
normalized to the template); illuminance and UV on a daily on/off cycle when the zone has
light exposure. Measurement model: per-sensor bias, white noise, quantization to the
resolution, Bernoulli dropout, and scripted outages.

Choices the spec leaves open (filed in docs/requests/B3.md, provisional until answered):

* The truth grid is t0 + k dt for k = 0..N with N = ceil((end - start)/dt) (N exact when the
  span is a whole number of steps within 1e-9 steps), so it always reaches mission end.
* Sampling instants are t0 + j dt_sample for every j with t_j <= mission end (same rule).
* Quantization can push a clipped RH of 100 to 100.00000000000001 (100 / 0.1 x 0.1 in
  floating point); readings are clipped again to their physical domain after quantizing.
* Illuminance and UV sensors have no noise or bias parameters in the profiles: their
  readings are the truth quantized at the storage-zone sensor resolution, with dropout and
  outages as for every sensor. Their noise normal and dropout uniform are still drawn, so
  the variate layout is the same for every quantity. Dose sensors draw no bias variate.
* Outages cover the half-open interval [start, start + duration_s). A light_exposure_change
  event needs a duration.
"""

import dataclasses
import hashlib
import math
from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.signal import lfilter

from pharmacy.config import canonical_json_bytes
from pharmacy.constants import SECONDS_PER_DAY, SECONDS_PER_HOUR
from pharmacy.errors import InputError
from pharmacy.types import (
    QUANTITIES,
    AssayObservation,
    EnvironmentProfileDoc,
    EnvironmentProfilesDoc,
    InspectionObservation,
    Quantity,
    ScenarioDoc,
    ScenarioEvent,
    ScenarioZone,
    SensorPoint,
    SensorSeries,
    SensorSpecDoc,
    SpeTemplate,
    StorageZoneDoc,
    StorageZonesDoc,
    ThermalFailureTemplate,
    assay_from_record,
    inspection_from_record,
    iso_to_seconds,
    seconds_to_iso,
)

type FloatArray = NDArray[np.float64]
type BoolArray = NDArray[np.bool_]

UNITS: Mapping[Quantity, str] = {
    "temperature_k": "K",
    "relative_humidity": "percent",
    "dose_rate_gy_per_s": "Gy/s",
    "illuminance_lux": "lx",
    "uv_w_per_m2": "W/m2",
}
"""Unit of every simulated series (canonical units of ``types.CANONICAL_UNITS``)."""

LIGHT_ON_OFFSET_S = 6.0 * SECONDS_PER_HOUR
"""The daily light cycle starts at 06:00 mission time (spec 13.1.2): a structural choice of
the simulator, not a configuration value."""

PERCENT_MAX = 100.0
_GRID_SNAP = 1e-9


@dataclasses.dataclass(frozen=True, slots=True)
class TruthSeries:
    """Noise-free truth of one quantity in one zone on the simulation grid (canonical
    units), for scenario tests that compare engine output with the true history."""

    zone_id: str
    quantity: Quantity
    t_s: tuple[float, ...]
    values: tuple[float, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SimulatedScenario:
    series: tuple[SensorSeries, ...]
    truth: tuple[TruthSeries, ...]
    assays: tuple[AssayObservation, ...]
    inspections: tuple[InspectionObservation, ...]


# =========================================================================================
# Seeds and generators
# =========================================================================================


def zone_seed(mission_seed: int, seed_label: str, sensor_id: str | None) -> int:
    """Per-zone and per-sensor seeds: int.from_bytes(SHA-256(canonical JSON of
    [mission_seed, seed_label, sensor_id])[:16], "big") (spec 13.1.4)."""
    digest = hashlib.sha256(canonical_json_bytes([mission_seed, seed_label, sensor_id])).digest()
    return int.from_bytes(digest[:16], "big")


def _generator(seed: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed)))


# =========================================================================================
# Closed-form event shapes
# =========================================================================================


def spe_peak_dose_rate_gy_per_s(template: SpeTemplate, shielding_scale: float | None) -> float:
    """Peak of an SPE pulse: D_total x shielding_scale / (rise_s/2 + tau) (spec 13.1.2), so
    the pulse integral over [t_s, inf) is exactly D_total x shielding_scale. A null
    ``shielding_scale`` means 1."""
    scale = 1.0 if shielding_scale is None else shielding_scale
    rise = template["rise_s"]["value"]
    tau = template["decay_time_constant_s"]["value"]
    if rise < 0.0 or tau <= 0.0 or scale < 0.0 or not math.isfinite(rise + tau + scale):
        raise InputError(f"SPE template {template['template_id']!r}: invalid rise, decay or scale")
    return template["total_absorbed_dose_gy"]["value"] * scale / (rise / 2.0 + tau)


def spe_dose_rate(
    t_s: FloatArray, start_s: float, template: SpeTemplate, shielding_scale: float | None
) -> FloatArray:
    """SPE pulse r(t) in Gy/s: 0 before ``start_s``, peak (t - t_s)/rise on
    [t_s, t_s + rise), peak exp(-(t - t_s - rise)/tau) afterwards (spec 13.1.2)."""
    peak = spe_peak_dose_rate_gy_per_s(template, shielding_scale)
    rise = template["rise_s"]["value"]
    tau = template["decay_time_constant_s"]["value"]
    since = t_s - start_s
    out = np.zeros_like(t_s)
    if rise > 0.0:
        rising = (since >= 0.0) & (since < rise)
        out[rising] = peak * since[rising] / rise
    decaying = since >= rise
    out[decaying] = peak * np.exp(-(since[decaying] - rise) / tau)
    return out


def spe_dose_gy(
    t0_s: float, t1_s: float, start_s: float, template: SpeTemplate, shielding_scale: float | None
) -> float:
    """Closed-form integral of the SPE pulse over [t0_s, t1_s] in Gy (``t1_s`` may be
    ``math.inf``). Over [start_s, inf) it equals D_total x shielding_scale."""
    if t1_s < t0_s:
        raise InputError("integration interval must not be reversed")
    peak = spe_peak_dose_rate_gy_per_s(template, shielding_scale)
    rise = template["rise_s"]["value"]
    tau = template["decay_time_constant_s"]["value"]
    a = max(t0_s - start_s, 0.0)
    b = max(t1_s - start_s, 0.0)
    total = 0.0
    ra, rb = min(a, rise), min(b, rise)
    if rise > 0.0 and rb > ra:
        total += peak * (rb * rb - ra * ra) / (2.0 * rise)
    da, db = max(a, rise) - rise, max(b, rise) - rise
    if db > da:
        tail_b = 0.0 if math.isinf(db) else math.exp(-db / tau)
        total += peak * tau * (math.exp(-da / tau) - tail_b)
    return total


def thermal_failure_weight(t_s: FloatArray, start_s: float, template: ThermalFailureTemplate) -> FloatArray:
    """Trapezoid s(t) of a thermal-control failure (spec 13.1.2): 0 to 1 linearly over
    ramp_s, 1 over hold_s, 1 to 0 over recover_s; NaN outside [start, start + ramp + hold +
    recover) so that callers can tell "not in this event" from "s = 0"."""
    ramp = template["ramp_s"]["value"]
    hold = template["hold_s"]["value"]
    recover = template["recover_s"]["value"]
    if min(ramp, hold, recover) < 0.0:
        raise InputError(f"thermal template {template['template_id']!r}: negative duration")
    since = t_s - start_s
    out = np.full_like(t_s, np.nan)
    if ramp > 0.0:
        ramping = (since >= 0.0) & (since < ramp)
        out[ramping] = since[ramping] / ramp
    holding = (since >= ramp) & (since < ramp + hold)
    out[holding] = 1.0
    if recover > 0.0:
        recovering = (since >= ramp + hold) & (since < ramp + hold + recover)
        out[recovering] = 1.0 - (since[recovering] - ramp - hold) / recover
    return out


# =========================================================================================
# Grid and lookups
# =========================================================================================


def _step_count(span_s: float, step_s: float) -> int:
    ratio = span_s / step_s
    nearest = round(ratio)
    if abs(ratio - nearest) <= _GRID_SNAP * max(1.0, abs(ratio)):
        return int(nearest)
    return math.ceil(ratio)


def _mission_window(scenario: ScenarioDoc) -> tuple[float, float]:
    start = iso_to_seconds(scenario["mission"]["start_utc"])
    end = iso_to_seconds(scenario["mission"]["end_utc"])
    if not end > start:
        raise InputError("mission end must be after mission start")
    return start, end


def _uniform_times(start_s: float, end_s: float, step_s: float, *, through_end: bool) -> FloatArray:
    if not (math.isfinite(step_s) and step_s > 0.0):
        raise InputError(f"step must be positive and finite, got {step_s!r}")
    span = end_s - start_s
    if through_end:
        n = _step_count(span, step_s)
    else:
        n = _step_count(span, step_s)
        if start_s + n * step_s > end_s:
            n -= 1
    return start_s + step_s * np.arange(n + 1, dtype=np.float64)


def _profile(profiles: EnvironmentProfilesDoc, profile_id: str) -> EnvironmentProfileDoc:
    for profile in profiles["profiles"]:
        if profile["profile_id"] == profile_id:
            return profile
    raise InputError(f"unknown environment profile {profile_id!r}")


def _storage_zone(zones: StorageZonesDoc, zone_id: str) -> StorageZoneDoc:
    for zone in zones["zones"]:
        if zone["zone_id"] == zone_id:
            return zone
    raise InputError(f"zone {zone_id!r} not in storage-zones")


def _thermal_template(profiles: EnvironmentProfilesDoc, template_id: str | None) -> ThermalFailureTemplate:
    for template in profiles["thermal_failure_templates"]:
        if template["template_id"] == template_id:
            return template
    raise InputError(f"unknown thermal-failure template {template_id!r}")


def _spe_template(profiles: EnvironmentProfilesDoc, template_id: str | None) -> SpeTemplate:
    for template in profiles["spe_templates"]:
        if template["template_id"] == template_id:
            return template
    raise InputError(f"unknown SPE template {template_id!r}")


def _zone_events(scenario: ScenarioDoc, zone_ids: set[str]) -> dict[str, list[ScenarioEvent]]:
    by_zone: dict[str, list[ScenarioEvent]] = {zone_id: [] for zone_id in zone_ids}
    for event in scenario["events"]:
        if event["zone_id"] not in zone_ids:
            raise InputError(f"event {event['event_id']!r} names zone {event['zone_id']!r}, not simulated")
        by_zone[event["zone_id"]].append(event)
    for events in by_zone.values():
        events.sort(key=lambda e: (iso_to_seconds(e["start_utc"]), e["event_id"]))
    return by_zone


def _sorted_zones(scenario: ScenarioDoc) -> list[ScenarioZone]:
    zones = sorted(scenario["zones"], key=lambda z: z["zone_id"])
    ids = [z["zone_id"] for z in zones]
    if len(set(ids)) != len(ids):
        raise InputError("scenario zones must be unique")
    return zones


# =========================================================================================
# Truth model
# =========================================================================================


def _ou_path(
    initial_normal: float, innovations: FloatArray, sd: float, time_constant_s: float, step_s: float
) -> FloatArray:
    """Exact OU discretization: X(t0) = sd z0; X(t + dt) = X(t) e^(-dt/theta) +
    sd sqrt(1 - e^(-2 dt/theta)) xi."""
    if not (time_constant_s > 0.0 and sd >= 0.0):
        raise InputError("OU time constant must be positive and the sd non-negative")
    decay = math.exp(-step_s / time_constant_s)
    gain = sd * math.sqrt(-math.expm1(-2.0 * step_s / time_constant_s))
    out = np.empty(innovations.size + 1, dtype=np.float64)
    out[0] = sd * initial_normal
    if innovations.size:
        filtered, _ = lfilter([gain], [1.0, -decay], innovations, zi=[decay * out[0]])
        out[1:] = filtered
    return out


def _zone_truth(
    zone: ScenarioZone,
    profile: EnvironmentProfileDoc,
    events: Sequence[ScenarioEvent],
    profiles: EnvironmentProfilesDoc,
    mission_seed: int,
    t0_s: float,
    grid: FloatArray,
    step_s: float,
) -> dict[Quantity, FloatArray]:
    n_steps = grid.size - 1
    gen = _generator(zone_seed(mission_seed, zone["seed_label"], None))
    normals = gen.standard_normal(2 + 2 * n_steps)
    temp, rh = profile["temperature"], profile["relative_humidity"]
    x_t = _ou_path(
        float(normals[0]),
        normals[2::2],
        temp["ou_sd_k"]["value"],
        temp["ou_time_constant_s"]["value"],
        step_s,
    )
    x_rh = _ou_path(
        float(normals[1]),
        normals[3::2],
        rh["ou_sd_percent"]["value"],
        rh["ou_time_constant_s"]["value"],
        step_s,
    )
    phase = np.sin(2.0 * math.pi * (grid - t0_s) / SECONDS_PER_DAY)
    base_t = temp["setpoint_k"]["value"] + temp["diurnal_amplitude_k"]["value"] * phase + x_t
    base_rh = rh["setpoint_percent"]["value"] + rh["diurnal_amplitude_percent"]["value"] * phase + x_rh

    weight = np.zeros_like(grid)
    target_t = np.zeros_like(grid)
    weight_rh = np.zeros_like(grid)
    target_rh = np.zeros_like(grid)
    dose = np.full_like(
        grid, profile["radiation"]["absorbed_dose_rate_gy_per_day"]["value"] / SECONDS_PER_DAY
    )
    light = profile["light"]
    if light is not None:
        time_of_day = np.mod(grid - t0_s - LIGHT_ON_OFFSET_S, SECONDS_PER_DAY)
        on = time_of_day < light["hours_on_per_day"]["value"] * SECONDS_PER_HOUR
    else:
        on = np.zeros(grid.size, dtype=bool)

    for event in events:
        start = iso_to_seconds(event["start_utc"])
        if event["kind"] == "thermal_control_failure":
            template = _thermal_template(profiles, event["template_id"])
            s = thermal_failure_weight(grid, start, template)
            inside = ~np.isnan(s)
            weight[inside] = s[inside]
            target_t[inside] = template["target_temperature_k"]["value"]
            target = template["target_rh_percent"]
            weight_rh[inside] = s[inside] if target is not None else 0.0
            target_rh[inside] = target["value"] if target is not None else 0.0
        elif event["kind"] == "solar_particle_event":
            template_spe = _spe_template(profiles, event["template_id"])
            dose = dose + spe_dose_rate(grid, start, template_spe, event["shielding_scale"])
        elif event["kind"] == "light_exposure_change":
            if light is None:
                raise InputError(
                    f"event {event['event_id']!r}: profile {profile['profile_id']!r} has no light"
                )
            if event["duration_s"] is None or event["duration_s"] < 0.0:
                raise InputError(f"event {event['event_id']!r}: light change needs a duration")
            on = on | ((grid >= start) & (grid < start + event["duration_s"]))

    temperature = (1.0 - weight) * base_t + weight * target_t
    humidity = np.clip((1.0 - weight_rh) * base_rh + weight_rh * target_rh, 0.0, PERCENT_MAX)
    lux = np.where(on, light["illuminance_lux"]["value"] if light is not None else 0.0, 0.0)
    uv = np.where(on, light["uv_w_per_m2"]["value"] if light is not None else 0.0, 0.0)
    return {
        "temperature_k": temperature,
        "relative_humidity": humidity,
        "dose_rate_gy_per_s": dose,
        "illuminance_lux": lux.astype(np.float64),
        "uv_w_per_m2": uv.astype(np.float64),
    }


def simulate_truth(
    scenario: ScenarioDoc,
    profiles: EnvironmentProfilesDoc,
    zones: StorageZonesDoc,
    grid_s: float,
) -> tuple[TruthSeries, ...]:
    """Truth series for every zone and quantity of the scenario on a uniform grid of step
    ``grid_s`` from mission start to end (spec 13.1.2)."""
    t0, t_end = _mission_window(scenario)
    grid = _uniform_times(t0, t_end, grid_s, through_end=True)
    scenario_zones = _sorted_zones(scenario)
    events = _zone_events(scenario, {z["zone_id"] for z in scenario_zones})
    grid_tuple = tuple(grid.tolist())
    out: list[TruthSeries] = []
    for zone in scenario_zones:
        _storage_zone(zones, zone["zone_id"])
        values = _zone_truth(
            zone,
            _profile(profiles, zone["profile_id"]),
            events[zone["zone_id"]],
            profiles,
            scenario["mission"]["seed"],
            t0,
            grid,
            grid_s,
        )
        out.extend(
            TruthSeries(zone_id=zone["zone_id"], quantity=q, t_s=grid_tuple, values=tuple(values[q].tolist()))
            for q in QUANTITIES
        )
    return tuple(out)


# =========================================================================================
# Measurement model
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _SensorModel:
    bias_sd: float | None
    noise_sd: float
    relative_noise: bool
    resolution: float


def _sensor_model(profile: EnvironmentProfileDoc, spec: SensorSpecDoc) -> _SensorModel:
    defaults = profile["sensor_defaults"]
    quantity = spec["quantity"]
    if quantity == "temperature_k":
        return _SensorModel(
            bias_sd=defaults["sensor_bias_temperature_sd_k"]["value"],
            noise_sd=defaults["temperature_noise_sd_k"]["value"],
            relative_noise=False,
            resolution=defaults["temperature_resolution_k"]["value"],
        )
    if quantity == "relative_humidity":
        return _SensorModel(
            bias_sd=defaults["sensor_bias_rh_sd_percent"]["value"],
            noise_sd=defaults["rh_noise_sd_percent"]["value"],
            relative_noise=False,
            resolution=defaults["rh_resolution_percent"]["value"],
        )
    if quantity == "dose_rate_gy_per_s":
        return _SensorModel(
            bias_sd=None,
            noise_sd=defaults["dose_rate_noise_relative_sd"]["value"],
            relative_noise=True,
            resolution=defaults["dose_rate_resolution_gy_per_s"]["value"],
        )
    return _SensorModel(
        bias_sd=None, noise_sd=0.0, relative_noise=False, resolution=spec["resolution"]["value"]
    )


def _truth_lookup(truth: Sequence[TruthSeries]) -> dict[tuple[str, Quantity], TruthSeries]:
    return {(t.zone_id, t.quantity): t for t in truth}


def _outage_mask(times: FloatArray, events: Sequence[ScenarioEvent], sensor_id: str) -> BoolArray:
    mask = np.zeros(times.size, dtype=bool)
    for event in events:
        if event["kind"] != "sensor_outage" or sensor_id not in event["sensor_ids"]:
            continue
        if event["duration_s"] is None or event["duration_s"] < 0.0:
            raise InputError(f"event {event['event_id']!r}: an outage needs a non-negative duration")
        start = iso_to_seconds(event["start_utc"])
        mask |= (times >= start) & (times < start + event["duration_s"])
    return mask


def _measure_sensor(
    truth_t: FloatArray,
    truth_v: FloatArray,
    times: FloatArray,
    model: _SensorModel,
    quantity: Quantity,
    dropout_probability: float,
    gen: np.random.Generator,
    outage: BoolArray,
) -> tuple[FloatArray, FloatArray]:
    bias = 0.0 if model.bias_sd is None else model.bias_sd * float(gen.standard_normal())
    n = times.size
    noise = np.empty(n, dtype=np.float64)
    drop = np.empty(n, dtype=np.float64)
    normal, uniform = gen.standard_normal, gen.random
    for j in range(n):
        noise[j] = normal()
        drop[j] = uniform()
    true_values = np.interp(times, truth_t, truth_v)
    if model.relative_noise:
        values = np.maximum(true_values * (1.0 + model.noise_sd * noise), 0.0)
    else:
        values = true_values + bias + model.noise_sd * noise
    if quantity == "relative_humidity":
        values = np.clip(values, 0.0, PERCENT_MAX)
    if not (model.resolution > 0.0 and math.isfinite(model.resolution)):
        raise InputError(f"sensor resolution must be positive, got {model.resolution!r}")
    values = np.round(values / model.resolution) * model.resolution
    if quantity == "relative_humidity":
        values = np.clip(values, 0.0, PERCENT_MAX)
    elif quantity != "temperature_k":
        values = np.maximum(values, 0.0)
    keep = (drop >= dropout_probability) & ~outage
    return times[keep], values[keep]


def measure(
    truth: tuple[TruthSeries, ...],
    scenario: ScenarioDoc,
    profiles: EnvironmentProfilesDoc,
    zones: StorageZonesDoc,
) -> tuple[SensorSeries, ...]:
    """Sensor series from truth through the measurement model (spec 13.1.3). Timestamps
    carry both ISO and float seconds; temperature series are reported in K."""
    t0, t_end = _mission_window(scenario)
    lookup = _truth_lookup(truth)
    scenario_zones = _sorted_zones(scenario)
    events = _zone_events(scenario, {z["zone_id"] for z in scenario_zones})
    out: list[SensorSeries] = []
    for zone in scenario_zones:
        profile = _profile(profiles, zone["profile_id"])
        configured = {s["sensor_id"]: s for s in _storage_zone(zones, zone["zone_id"])["sensors"]}
        for sensor in sorted(zone["sensors"], key=lambda s: s["sensor_id"]):
            spec = configured.get(sensor["sensor_id"])
            if spec is None or spec["quantity"] != sensor["quantity"]:
                raise InputError(
                    f"sensor {sensor['sensor_id']!r} is not configured in zone {zone['zone_id']!r} "
                    f"for {sensor['quantity']}"
                )
            series_truth = lookup.get((zone["zone_id"], sensor["quantity"]))
            if series_truth is None:
                raise InputError(f"no truth for zone {zone['zone_id']!r} quantity {sensor['quantity']}")
            times = _uniform_times(t0, t_end, sensor["sampling_interval_s"], through_end=False)
            truth_t = np.asarray(series_truth.t_s, dtype=np.float64)
            if truth_t.size == 0 or truth_t[0] > times[0] or truth_t[-1] < times[-1]:
                raise InputError(f"truth of zone {zone['zone_id']!r} does not cover the sampling window")
            kept_t, kept_v = _measure_sensor(
                truth_t,
                np.asarray(series_truth.values, dtype=np.float64),
                times,
                _sensor_model(profile, spec),
                sensor["quantity"],
                profile["sensor_defaults"]["dropout_probability"]["value"],
                _generator(zone_seed(scenario["mission"]["seed"], zone["seed_label"], sensor["sensor_id"])),
                _outage_mask(times, events[zone["zone_id"]], sensor["sensor_id"]),
            )
            points = tuple(
                SensorPoint(t_utc=seconds_to_iso(t), t_s=t, value=v)
                for t, v in zip(kept_t.tolist(), kept_v.tolist(), strict=True)
            )
            out.append(
                SensorSeries(
                    series_id=f"{scenario['mission']['mission_id']}:{sensor['sensor_id']}",
                    sensor_id=sensor["sensor_id"],
                    zone_id=zone["zone_id"],
                    quantity=sensor["quantity"],
                    unit=UNITS[sensor["quantity"]],
                    points=points,
                )
            )
    return tuple(out)


def simulate_scenario(
    scenario: ScenarioDoc,
    profiles: EnvironmentProfilesDoc,
    zones: StorageZonesDoc,
    grid_s: float | None = None,
) -> SimulatedScenario:
    """Truth, measured series and the scenario's observations. ``grid_s`` defaults to the
    smallest sensor sampling interval of the scenario."""
    if grid_s is None:
        intervals = [s["sampling_interval_s"] for z in scenario["zones"] for s in z["sensors"]]
        if not intervals:
            raise InputError("the scenario has no sensors; give grid_s explicitly")
        grid_s = min(intervals)
    truth = simulate_truth(scenario, profiles, zones, grid_s)
    return SimulatedScenario(
        series=measure(truth, scenario, profiles, zones),
        truth=truth,
        assays=tuple(assay_from_record(a) for a in scenario["assays"]),
        inspections=tuple(inspection_from_record(i) for i in scenario["inspections"]),
    )


def summary_statistics(series: tuple[SensorSeries, ...]) -> Mapping[str, Mapping[str, float]]:
    """Per series: mean, sd, min, max, coverage fraction (for envsim self-tests, spec 15.4).

    Keys per series_id: ``n`` (readings), ``mean``, ``sd`` (sample, divisor n - 1; 0 for a
    single reading), ``min``, ``max`` and ``coverage_fraction`` = n / (number of sampling
    instants from the first to the last reading at the smallest spacing). An empty series
    reports n = 0 and coverage 0 only."""
    out: dict[str, Mapping[str, float]] = {}
    for item in series:
        values = np.asarray([p.value for p in item.points], dtype=np.float64)
        times = np.asarray([p.t_s for p in item.points], dtype=np.float64)
        n = values.size
        if n == 0:
            out[item.series_id] = {"n": 0.0, "coverage_fraction": 0.0}
            continue
        if n > 1:
            spacing = float(np.min(np.diff(times)))
            expected = _step_count(float(times[-1] - times[0]), spacing) + 1
            sd = float(np.std(values, ddof=1))
        else:
            expected, sd = 1, 0.0
        out[item.series_id] = {
            "n": float(n),
            "mean": float(np.mean(values)),
            "sd": sd,
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "coverage_fraction": n / expected,
        }
    return out
