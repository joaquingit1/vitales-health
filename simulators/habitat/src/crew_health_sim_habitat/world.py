"""The true cabin state that the simulated devices sample.

A well-mixed cabin: gas amounts in moles, particles and VOCs as mass, temperature and humidity
as state. Crew metabolism follows the configured day plan; scenario events change generation,
removal, pressure and dose rate. Every rate comes from config/scenarios.json with its source.
"""

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass

from crew_health_sim_habitat.errors import NonFiniteValueError
from crew_health_sim_habitat.scenariocfg import Activity, Event, Scenario
from crew_health_sim_habitat.units import (
    CELSIUS_OFFSET,
    GAS_CONSTANT,
    MMHG_PER_KPA,
    MOLAR_MASS_CO2,
    MOLAR_MASS_O2,
    saturation_vapor_pressure_kpa,
)

# IEC 61672-1:2013 Table 3, A-weighting at the octave band centre frequencies, dB.
A_WEIGHTING_DB: Mapping[str, float] = {
    "63": -26.2,
    "125": -16.1,
    "250": -8.6,
    "500": -3.2,
    "1k": 0.0,
    "2k": 1.2,
    "4k": 1.0,
    "8k": -1.1,
}
# NASA-STD-3001 Vol 2 Rev F, Table 6.2-1 page 51: PIO2 = (PB - 47) * FIO2, PB and 47 in mmHg.
LUNG_WATER_VAPOR_MMHG = 47.0

type Truth = dict[str, float | None]


@dataclass(slots=True)
class CabinState:
    n_o2: float
    n_n2: float
    n_co2: float
    n_co: float
    n_nh3: float
    tvoc_mg: float
    pm1_mg: float
    pm2_5_mg: float
    pm10_mg: float
    temperature_c: float
    rh_percent: float
    dose_factor: float


class CabinWorld:
    """Steps the cabin forward in fixed time steps from t = 0 (scenario start)."""

    def __init__(self, scenario: Scenario, rng: random.Random) -> None:
        self._s = scenario
        self._env = scenario.environment
        self._rng = rng
        self.t_s = 0.0
        env = self._env
        self._kelvin = env.temperature_c + CELSIUS_OFFSET
        self._dry_setpoint_kpa = env.total_pressure_kpa - self._vapor_kpa(
            env.temperature_c, env.relative_humidity
        )
        n_dry = self._moles(self._dry_setpoint_kpa)
        n_co2 = self._moles(self._steady_ppco2_mmhg() / MMHG_PER_KPA)
        n_o2 = env.o2_fraction * n_dry
        self.state = CabinState(
            n_o2=n_o2,
            n_n2=n_dry - n_o2 - n_co2,
            n_co2=n_co2,
            n_co=0.0,
            n_nh3=env.nh3_mol_per_s * scenario.crew_count / env.trace_removal_per_s,
            tvoc_mg=env.voc_mg_per_s / env.trace_removal_per_s,
            pm1_mg=0.0,
            pm2_5_mg=0.0,
            pm10_mg=0.0,
            temperature_c=env.temperature_c,
            rh_percent=env.relative_humidity,
            dose_factor=1.0,
        )
        self._o2_target = n_o2
        self._n2_target = n_dry - n_o2 - n_co2
        self._start_particles_steady()

    def _start_particles_steady(self) -> None:
        """Start particle mass where filtration balances the crew's day-average generation."""
        env, s = self._env, self.state
        exercise_s = sum(d for a, d in self._s.day_plan if a.exercise)
        weight = self._s.crew_count * (1.0 + (env.exercise_dust_factor - 1.0) * exercise_s / 86400.0)
        respirable = env.dust_respirable_mg_per_s * weight
        s.pm1_mg = respirable * env.pm1_fraction_of_respirable / env.filtration_per_s
        s.pm2_5_mg = respirable / env.filtration_per_s
        s.pm10_mg = (
            max(env.dust_total_mg_per_s * weight * env.pm10_fraction_of_total, respirable)
            / env.filtration_per_s
        )

    def _moles(self, pressure_kpa: float) -> float:
        return pressure_kpa * 1000.0 * self._env.volume_m3 / (GAS_CONSTANT * self._kelvin)

    def _kpa(self, moles: float) -> float:
        return moles * GAS_CONSTANT * self._kelvin / self._env.volume_m3 / 1000.0

    @staticmethod
    def _vapor_kpa(temperature_c: float, rh_percent: float) -> float:
        return saturation_vapor_pressure_kpa(temperature_c) * rh_percent / 100.0

    def _steady_ppco2_mmhg(self) -> float:
        """ppCO2 where the scrubber removes the crew's day-average CO2 output."""
        day_kg = sum(a.co2_kg_per_s * d for a, d in self._s.day_plan) * self._s.crew_count
        removal = self._env.scrubber_kg_per_s_per_mmhg * self._env.scrubber_units * 86400.0
        return day_kg / removal if removal > 0 else 0.0

    def activities(self, t_s: float) -> list[Activity]:
        """What each crew member is doing at time t."""
        result: list[Activity] = []
        for offset in self._s.crew_offsets_s:
            clock = (t_s - offset) % 86400.0
            elapsed = 0.0
            for activity, duration in self._s.day_plan:
                if clock < elapsed + duration:
                    result.append(activity)
                    break
                elapsed += duration
        return result

    def _events(self, kind: str) -> list[Event]:
        return [e for e in self._s.events if e.kind == kind]

    def _ventilation_on(self) -> bool:
        return not any(e.active(self.t_s) for e in self._events("ventilation_off"))

    def _scrubber_efficiency(self) -> float:
        efficiency = 1.0
        for event in self._events("scrubber_efficiency"):
            if self.t_s < event.start_s:
                continue
            progress = min(1.0, (self.t_s - event.start_s) / max(event.duration_s, 1.0))
            efficiency = 1.0 + (event.params["final_efficiency"] - 1.0) * progress
        return efficiency

    def _leak_rate(self) -> float:
        rates = [1.0 / e.params["time_constant_s"] for e in self._events("leak") if e.active(self.t_s)]
        return sum(rates)

    def _release(self, species: str) -> float:
        return sum(e.params.get(species, 0.0) for e in self._events("release") if e.active(self.t_s))

    def step(self, dt_s: float) -> None:
        crew = self.activities(self.t_s)
        vent = self._ventilation_on()
        leak = self._leak_rate()
        self._step_gases(dt_s, crew, vent, leak)
        self._step_particles(dt_s, crew, vent, leak)
        self._step_thermal(dt_s)
        self._step_dose(dt_s)
        self.t_s += dt_s
        self._check_finite()

    def _step_gases(self, dt: float, crew: list[Activity], vent: bool, leak: float) -> None:
        s, env = self.state, self._env
        co2_gen = sum(a.co2_kg_per_s for a in crew) / MOLAR_MASS_CO2
        o2_use = sum(a.o2_kg_per_s for a in crew) / MOLAR_MASS_O2
        ppco2 = self._kpa(s.n_co2) * MMHG_PER_KPA
        scrub = 0.0
        if vent:
            scrub = env.scrubber_kg_per_s_per_mmhg * env.scrubber_units * self._scrubber_efficiency() * ppco2
        s.n_co2 += (co2_gen - scrub / MOLAR_MASS_CO2 - leak * s.n_co2) * dt
        makeup = leak == 0.0
        o2_add = max(0.0, self._o2_target - s.n_o2) / env.o2_makeup_tau_s if makeup else 0.0
        n2_add = max(0.0, self._n2_target - s.n_n2) / env.o2_makeup_tau_s if makeup else 0.0
        s.n_o2 += (o2_add - o2_use - leak * s.n_o2) * dt
        s.n_n2 += (n2_add - leak * s.n_n2) * dt
        trace = env.trace_removal_per_s if vent else 0.0
        s.n_co += (self._release("co_ppm") - (trace + leak) * s.n_co) * dt
        nh3_gen = env.nh3_mol_per_s * len(crew) + self._release("nh3_ppm")
        s.n_nh3 += (nh3_gen - (trace + leak) * s.n_nh3) * dt
        voc_gen = env.voc_mg_per_s + self._release("tvoc_ugm3")
        s.tvoc_mg += (voc_gen - (trace + leak) * s.tvoc_mg) * dt
        for name in ("n_co2", "n_o2", "n_n2", "n_co", "n_nh3", "tvoc_mg"):
            setattr(self.state, name, max(0.0, getattr(self.state, name)))

    def _step_particles(self, dt: float, crew: list[Activity], vent: bool, leak: float) -> None:
        s, env = self.state, self._env
        weight = sum(env.exercise_dust_factor if a.exercise else 1.0 for a in crew)
        total = env.dust_total_mg_per_s * weight
        respirable = env.dust_respirable_mg_per_s * weight
        gen = {
            "pm1_mg": respirable * env.pm1_fraction_of_respirable + self._release("pm1_ugm3"),
            "pm2_5_mg": respirable + self._release("pm2_5_ugm3"),
            "pm10_mg": max(total * env.pm10_fraction_of_total, respirable) + self._release("pm10_ugm3"),
        }
        removal = (env.filtration_per_s if vent else 0.0) + leak
        for name in sorted(gen):
            current = getattr(s, name)
            setattr(s, name, max(0.0, current + (gen[name] - removal * current) * dt))

    def _humidity_target(self) -> float:
        target = self._env.relative_humidity
        for event in self._events("humidity_target"):
            if self.t_s < event.start_s:
                continue
            progress = min(1.0, (self.t_s - event.start_s) / max(event.duration_s, 1.0))
            target = self._env.relative_humidity + (event.params["relative_humidity"] - target) * progress
        return target

    def _step_thermal(self, dt: float) -> None:
        s, env = self.state, self._env
        heat = self._release("temperature_c")
        s.temperature_c += ((env.temperature_c - s.temperature_c) / env.thermal_tau_s + heat) * dt
        s.rh_percent = min(100.0, max(0.0, self._humidity_target()))

    def spe_excess_ugy_h(self, t_s: float) -> float:
        """Dose rate added by solar particle events at t: a trapezoid whose area is the event's added dose."""
        excess = 0.0
        for event in self._events("spe"):
            if not event.active(t_s):
                continue
            since, length = t_s - event.start_s, event.duration_s
            rise, fall = event.params["rise_s"], event.params["fall_s"]
            plateau = event.params["added_dose_ugy"] / ((length - (rise + fall) / 2.0) / 3600.0)
            if since < rise:
                excess += plateau * since / rise
            elif since > length - fall:
                excess += plateau * max(0.0, length - since) / fall
            else:
                excess += plateau
        return excess

    def _step_dose(self, dt: float) -> None:
        env = self._env
        if env.dose_rate_variability is None or env.dose_rate_correlation_s is None:
            return
        # Ornstein-Uhlenbeck on the log of the background factor: mean 0, stationary sd = variability.
        tau, sd = env.dose_rate_correlation_s, env.dose_rate_variability
        log_f = math.log(self.state.dose_factor)
        log_f += -log_f * dt / tau + sd * math.sqrt(2.0 * dt / tau) * self._rng.gauss(0.0, 1.0)
        self.state.dose_factor = math.exp(log_f)

    def _check_finite(self) -> None:
        for name in CabinState.__slots__:
            if not math.isfinite(getattr(self.state, name)):
                raise NonFiniteValueError(f"cabin state {name} became non-finite")

    def _noise_bands(self) -> dict[str, float]:
        env = self._env
        boost = env.exercise_noise_boost_db if any(a.exercise for a in self.activities(self.t_s)) else 0.0
        return {band: level - env.noise_margin_db + boost for band, level in env.noise_bands_db.items()}

    def truth(self) -> Truth:
        """The true cabin quantities at the current time, keyed by world quantity name."""
        s, env = self.state, self._env
        n_dry = s.n_o2 + s.n_n2 + s.n_co2 + s.n_co + s.n_nh3
        dry_kpa = self._kpa(n_dry)
        pressure_kpa = dry_kpa + self._vapor_kpa(s.temperature_c, s.rh_percent)
        o2_fraction = s.n_o2 / n_dry if n_dry > 0 else 0.0
        volume = env.volume_m3
        bands = self._noise_bands()
        truth: Truth = {
            "pressure_kpa": pressure_kpa,
            "co2_ppm": s.n_co2 / n_dry * 1e6 if n_dry > 0 else 0.0,
            "co2_mmhg": self._kpa(s.n_co2) * MMHG_PER_KPA,
            "ppo2_kpa": self._kpa(s.n_o2),
            "o2_percent": o2_fraction * 100.0,
            "pio2_mmhg": (pressure_kpa * MMHG_PER_KPA - LUNG_WATER_VAPOR_MMHG) * o2_fraction,
            "co_ppm": s.n_co / n_dry * 1e6 if n_dry > 0 else 0.0,
            "nh3_ppm": s.n_nh3 / n_dry * 1e6 if n_dry > 0 else 0.0,
            "tvoc_ugm3": s.tvoc_mg / volume * 1000.0,
            "pm1_ugm3": s.pm1_mg / volume * 1000.0,
            "pm2_5_ugm3": s.pm2_5_mg / volume * 1000.0,
            "pm10_ugm3": s.pm10_mg / volume * 1000.0,
            "temperature_c": s.temperature_c,
            "rh_percent": s.rh_percent,
            "laeq_dba": a_weighted_level(bands),
            "dose_rate_ugy_h": None,
        }
        for band, level in bands.items():
            truth[f"band_{band}_db"] = level
        if env.dose_rate_ugy_h is not None:
            truth["dose_rate_ugy_h"] = env.dose_rate_ugy_h * s.dose_factor + self.spe_excess_ugy_h(self.t_s)
        return truth


def a_weighted_level(bands_db: Mapping[str, float]) -> float:
    """Combine octave band levels into one A-weighted level (energy sum)."""
    energy = sum(10.0 ** ((level + A_WEIGHTING_DB[band]) / 10.0) for band, level in sorted(bands_db.items()))
    return 10.0 * math.log10(energy)
