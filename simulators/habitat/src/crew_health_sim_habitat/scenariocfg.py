"""Cabin environments, crew activities and scenarios from config/scenarios.json."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from crew_health_sim_habitat.config import (
    JsonObject,
    load_json,
    number,
    obj,
    objs,
    opt_obj,
    param,
    seconds,
    text,
    value,
)
from crew_health_sim_habitat.errors import ConfigError

DAY_S = 86400.0

# Release rates: the world quantity a release feeds and the unit its rate must carry.
RELEASE_UNITS: Mapping[str, str] = {
    "co_ppm": "mol/min",
    "nh3_ppm": "mol/min",
    "tvoc_ugm3": "mg/min",
    "pm1_ugm3": "mg/min",
    "pm2_5_ugm3": "mg/min",
    "pm10_ugm3": "mg/min",
    "temperature_c": "Cel/min",
}
EVENT_KINDS = ("scrubber_efficiency", "ventilation_off", "release", "leak", "humidity_target", "spe")
FAULT_KINDS = ("stuck", "dropout", "offset", "drift", "disconnect")


@dataclass(frozen=True, slots=True)
class Activity:
    name: str
    o2_kg_per_s: float
    co2_kg_per_s: float
    exercise: bool


@dataclass(frozen=True, slots=True)
class Environment:
    environment_id: str
    volume_m3: float
    total_pressure_kpa: float
    o2_fraction: float
    temperature_c: float
    relative_humidity: float
    thermal_tau_s: float
    scrubber_kg_per_s_per_mmhg: float
    scrubber_units: float
    o2_makeup_tau_s: float
    trace_removal_per_s: float
    filtration_per_s: float
    dust_total_mg_per_s: float
    dust_respirable_mg_per_s: float
    pm10_fraction_of_total: float
    pm1_fraction_of_respirable: float
    exercise_dust_factor: float
    nh3_mol_per_s: float
    voc_mg_per_s: float
    noise_bands_db: Mapping[str, float]
    noise_margin_db: float
    exercise_noise_boost_db: float
    dose_rate_ugy_h: float | None
    dose_rate_variability: float | None
    dose_rate_correlation_s: float | None


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    start_s: float
    duration_s: float
    params: Mapping[str, float]

    def active(self, t_s: float) -> bool:
        return self.start_s <= t_s < self.start_s + self.duration_s


@dataclass(frozen=True, slots=True)
class DeviceFault:
    device_id: str
    fault: str
    start_s: float
    duration_s: float
    offset: float | None  # offset: added value; drift: added value per hour since the fault started
    # The value's unit: the fault acts only on outputs in that unit (in that unit per hour for drift).
    unit: str | None = None

    def acts_on(self, output_unit: str) -> bool:
        if self.unit is None:
            return True
        return self.unit == (f"{output_unit}/h" if self.fault == "drift" else output_unit)

    def active(self, t_s: float) -> bool:
        return self.start_s <= t_s < self.start_s + self.duration_s


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    environment: Environment
    crew_count: int
    day_plan: tuple[tuple[Activity, float], ...]
    crew_offsets_s: tuple[float, ...]
    duration_s: float
    seed: int
    note: str
    events: tuple[Event, ...]
    faults: tuple[DeviceFault, ...]


def _per_s(data: JsonObject, key: str, unit_per_min: str) -> float:
    return value(data, key, unit_per_min) / 60.0


def _environment(environment_id: str, raw: JsonObject) -> Environment:
    rated = value(raw, "scrubber_rated_removal", "kg/d") / DAY_S
    rated_pp = value(raw, "scrubber_rated_ppco2", "mmHg")
    if rated_pp <= 0:
        raise ConfigError("scrubber_rated_ppco2 must be positive")
    bands_raw = obj(obj(raw, "noise_reference"), "bands")
    dose = None if raw.get("dose_rate") is None else value(raw, "dose_rate", "µGy/h")
    variability = (
        None if raw.get("dose_rate_variability") is None else value(raw, "dose_rate_variability", "1")
    )
    correlation = (
        None if raw.get("dose_rate_correlation_time") is None else seconds(raw, "dose_rate_correlation_time")
    )
    if dose is not None and (variability is None or correlation is None or correlation <= 0):
        raise ConfigError(
            f"{environment_id}: a dose rate needs its variability and a positive correlation time"
        )
    return Environment(
        environment_id=environment_id,
        volume_m3=value(raw, "volume", "m3"),
        total_pressure_kpa=value(raw, "total_pressure", "kPa"),
        o2_fraction=value(raw, "o2_fraction", "1"),
        temperature_c=value(raw, "temperature", "Cel"),
        relative_humidity=value(raw, "relative_humidity", "%"),
        thermal_tau_s=seconds(raw, "thermal_time_constant"),
        scrubber_kg_per_s_per_mmhg=rated / rated_pp,
        scrubber_units=value(raw, "scrubber_units", "1"),
        o2_makeup_tau_s=seconds(raw, "o2_makeup_time_constant"),
        trace_removal_per_s=value(raw, "trace_removal_rate", "1/h") / 3600.0,
        filtration_per_s=value(raw, "filtration_rate", "1/min") / 60.0,
        dust_total_mg_per_s=_per_s(raw, "dust_generation_total", "mg/min"),
        dust_respirable_mg_per_s=_per_s(raw, "dust_generation_respirable", "mg/min"),
        pm10_fraction_of_total=value(raw, "pm10_fraction_of_total", "1"),
        pm1_fraction_of_respirable=value(raw, "pm1_fraction_of_respirable", "1"),
        exercise_dust_factor=value(raw, "exercise_dust_factor", "1"),
        nh3_mol_per_s=_per_s(raw, "nh3_metabolic_generation", "mol/min"),
        voc_mg_per_s=_per_s(raw, "voc_offgas_generation", "mg/min"),
        noise_bands_db={band: value(bands_raw, band, "dB") for band in sorted(bands_raw)},
        noise_margin_db=value(raw, "noise_margin", "dB"),
        exercise_noise_boost_db=value(raw, "exercise_noise_boost", "dB"),
        dose_rate_ugy_h=dose,
        dose_rate_variability=variability,
        dose_rate_correlation_s=correlation,
    )


def _event_params(kind: str, raw: JsonObject) -> dict[str, float]:
    params = obj(raw, "params")
    if kind == "release":
        unknown = sorted(set(params) - set(RELEASE_UNITS))
        if unknown or not params:
            raise ConfigError(f"release names unknown or no species: {unknown}")
        return {k: value(params, k, RELEASE_UNITS[k]) / 60.0 for k in sorted(params)}
    if kind == "scrubber_efficiency":
        return {"final_efficiency": value(params, "final_efficiency", "1")}
    if kind == "leak":
        return {"time_constant_s": seconds(params, "time_constant")}
    if kind == "humidity_target":
        return {"relative_humidity": value(params, "relative_humidity", "%")}
    if kind == "spe":
        return {
            "added_dose_ugy": value(params, "added_dose", "µGy"),
            "rise_s": seconds(params, "rise"),
            "fall_s": seconds(params, "fall"),
        }
    return {}


def _event(raw: JsonObject) -> Event:
    kind = text(raw, "kind")
    if kind not in EVENT_KINDS:
        raise ConfigError(f"unknown event kind {kind!r}")
    event = Event(kind, seconds(raw, "start"), seconds(raw, "duration"), _event_params(kind, raw))
    if kind == "spe":
        ramps = event.params["rise_s"] + event.params["fall_s"]
        if min(event.params.values()) < 0 or ramps <= 0 or ramps > event.duration_s:
            raise ConfigError("spe: rise and fall must be positive and fit inside the event duration")
    return event


def _fault(raw: JsonObject) -> DeviceFault:
    fault = text(raw, "fault")
    if fault not in FAULT_KINDS:
        raise ConfigError(f"unknown device fault {fault!r}")
    offset_raw = opt_obj(raw, "value")
    unit = None if offset_raw is None else text(offset_raw, "unit")
    offset = None if offset_raw is None or unit is None else param(raw, "value", unit).value
    if fault in ("offset", "drift") and offset is None:
        raise ConfigError(f"a {fault} fault needs a value")
    if fault == "drift" and (unit is None or not unit.endswith("/h")):
        raise ConfigError("a drift fault's value is a rate per hour (unit ending in /h)")
    start, duration = seconds(raw, "start"), seconds(raw, "duration")
    return DeviceFault(text(raw, "device_id"), fault, start, duration, offset, unit)


def _activities(doc: JsonObject) -> dict[str, Activity]:
    table = obj(doc, "activities")
    result: dict[str, Activity] = {}
    for name in sorted(table):
        raw = obj(table, name)
        exercise = raw.get("exercise")
        if not isinstance(exercise, bool):
            raise ConfigError(f"activity {name!r}: exercise must be true or false")
        result[name] = Activity(name, _per_s(raw, "o2", "kg/min"), _per_s(raw, "co2", "kg/min"), exercise)
    return result


def _day_plans(
    doc: JsonObject, activities: Mapping[str, Activity]
) -> dict[str, tuple[tuple[Activity, float], ...]]:
    plans: dict[str, tuple[tuple[Activity, float], ...]] = {}
    table = obj(doc, "day_plans")
    for plan_id in sorted(table):
        blocks: list[tuple[Activity, float]] = []
        for block in objs(obj(table, plan_id), "blocks"):
            name = text(block, "activity")
            if name not in activities:
                raise ConfigError(f"day plan {plan_id!r} names unknown activity {name!r}")
            blocks.append((activities[name], number(block, "hours") * 3600.0))
        if abs(sum(seconds_ for _, seconds_ in blocks) - DAY_S) > 1e-6:
            raise ConfigError(f"day plan {plan_id!r} must cover 24 hours")
        plans[plan_id] = tuple(blocks)
    return plans


type DayPlan = tuple[tuple[Activity, float], ...]


def _scenario(
    raw: JsonObject, environments: Mapping[str, Environment], plans: Mapping[str, DayPlan]
) -> Scenario:
    env_id = text(raw, "environment_id")
    plan_id = text(raw, "day_plan")
    if env_id not in environments or plan_id not in plans:
        raise ConfigError(f"scenario names unknown environment {env_id!r} or day plan {plan_id!r}")
    crew = value(raw, "crew_count", "1")
    offsets = tuple(seconds({"o": o}, "o") for o in objs(raw, "crew_offsets"))
    if crew != int(crew) or crew < 0 or len(offsets) != int(crew):
        raise ConfigError("crew_count must be a whole number with one offset per crew member")
    seed = number(raw, "seed")
    plan = plans[plan_id]
    return Scenario(
        scenario_id=text(raw, "scenario_id"),
        environment=environments[env_id],
        crew_count=int(crew),
        day_plan=plan,
        crew_offsets_s=offsets,
        duration_s=seconds(raw, "duration"),
        seed=int(seed),
        note=text(raw, "note"),
        events=tuple(_event(e) for e in objs(raw, "events")),
        faults=tuple(_fault(f) for f in objs(raw, "device_faults")),
    )


def load_scenarios(path: Path) -> dict[str, Scenario]:
    doc = load_json(path)
    activities = _activities(doc)
    plans = _day_plans(doc, activities)
    env_table = obj(doc, "environments")
    environments = {e: _environment(e, obj(env_table, e)) for e in sorted(env_table)}
    scenarios: dict[str, Scenario] = {}
    for raw in objs(doc, "scenarios"):
        scenario = _scenario(raw, environments, plans)
        if scenario.scenario_id in scenarios:
            raise ConfigError(f"duplicate scenario {scenario.scenario_id!r}")
        scenarios[scenario.scenario_id] = scenario
    return scenarios
