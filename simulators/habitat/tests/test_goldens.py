"""Golden checks: scenario outcomes against values typed from the NASA sources, never from our config.

Each expected number below was read in the source document (ID and page given) and checked by hand.
"""

import csv
import json
import random
import statistics
from collections.abc import Iterator
from dataclasses import replace

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.devices import STATUS_SATURATED, Device
from crew_health_sim_habitat.runner import Tick, build_devices, simulate
from crew_health_sim_habitat.units import KPA_PER_PSI, MMHG_PER_KPA
from crew_health_sim_habitat.world import CabinWorld, a_weighted_level

from conftest import REPO_ROOT

# EV01 NASA-STD-3001 Vol 2 Rev F, [V2 6004], page 51: one-hour average ppCO2 no more than 3 mmHg.
CO2_ONE_HOUR_LIMIT_MMHG = 3.0
# C25 OCHMO-TB-004 Rev D, page 5: "current ISS range: 1 to 9 mmHg".
ISS_CO2_RANGE_MMHG = (1.0, 9.0)
# C25 page 6 table, "Total Per Day" CO2 output with exercise: 1.04 kg (2.29 lbm).
CO2_PER_CREW_DAY_KG = 1.04
# C25 page 10: Thermal Amine Scrubber removes 3.7 kg/day at 2 mmHg.
TAS_KG_PER_DAY, TAS_PPCO2_MMHG = 3.7, 2.0
# EV01 Table 6.2-1, page 51: PIO2 normoxia target 145 to 155 mmHg; mild hypoxia lower limit 127 mmHg.
PIO2_TARGET_MMHG = (145.0, 155.0)
PIO2_MILD_HYPOXIA_MMHG = 127.0
# EV01 Table 6.2-1, page 51, example: exploration atmosphere 8.2 psia, 34% O2. By hand:
# 8.2 psi x 6.894757 = 56.537 kPa = 424.06 mmHg; (424.06 - 47) x 0.34 = 128.20 mmHg.
EXPLORATION_PIO2_MMHG = 128.20
# EV01 [V2 6007], page 53: rate of change no more than 13.5 psi/min.
PRESSURE_RATE_LIMIT_PSI_MIN = 13.5
# EV24 JSC 20584 Rev C (ENVIRONMENTS.md section 5): CO 24-hour SMAC 100 ppm, 1-hour 425 ppm.
CO_SMAC_24H_PPM, CO_SMAC_1H_PPM = 100.0, 425.0
# EV24 (ENVIRONMENTS.md section 6): NH3 1-hour SMAC 30 ppm.
NH3_SMAC_1H_PPM = 30.0
# EV01 Table 6.2-3, page 60: CO monitor range 5 to 1000 ppm, resolution 1 ppm.
CO_MONITOR_MAX_PPM, CO_MONITOR_RESOLUTION_PPM = 1000.0, 1.0
# EV01 Table 6.6-3, page 96: NC-50 work area octave band maxima, dB re 20 uPa.
NC50_DB = {"63": 71, "125": 64, "250": 58, "500": 54, "1k": 51, "2k": 49, "4k": 48, "8k": 47}
# EV01 [V2 6052], page 62: crew dust generation 1.33 mg/person-minute total, 0.006 respirable.
DUST_TOTAL_MG_MIN, DUST_RESPIRABLE_MG_MIN = 1.33, 0.006
# EV01 Table 6.1-1, page 48: example sampling rates in seconds.
SAMPLING_S = {"ndir_co2": 5, "o2_cell": 5, "barometer": 1, "ec_co": 1, "optical_pm": 60, "temp_rh": 60}
# N40 (Berger et al. 2018, Section 3.3, PDF page 9): the first SPE protons reached the ISS on 11 Sep 2017 at
# 04:30 UTC; the scenario's time zero is 11 Sep 00:00 UTC.
SPE_ONSET_S = (4 * 60 + 30) * 60
DAY_S = 86400
# N40 Table 4 (PDF page 20), read from the case file, never from our config.
SPE_CASE = (
    REPO_ROOT / "docs" / "product" / "nasa-data" / "cases" / "spe-2017-09-iss-columbus" / "outcome.json"
)
# NASA-SOURCES.md section 2: the 84 DosTel1 records average 6.2 uGy/h.
DOSTEL1_MEAN_UGY_H, DOSTEL1_RECORDS = 6.2, 84


def _ticks(loaded: Loaded, scenario_id: str, hours: float | None = None) -> Iterator[Tick]:
    scenario = loaded.scenario(scenario_id)
    if hours is not None:
        scenario = replace(scenario, duration_s=hours * 3600.0)
    return simulate(scenario, build_devices(scenario, loaded.wiring, loaded.kinds))


def _minute_truth(loaded: Loaded, scenario_id: str, key: str, hours: float | None = None) -> list[float]:
    values = []
    for tick in _ticks(loaded, scenario_id, hours):
        value = tick.truth[key]
        if tick.t_s % 60 == 0 and value is not None:
            values.append(value)
    return values


def _hourly_means(per_minute: list[float]) -> list[float]:
    return [statistics.fmean(per_minute[i : i + 60]) for i in range(0, len(per_minute) - 59, 60)]


def test_crew_co2_output_per_day_matches_c25(loaded: Loaded) -> None:
    scenario = loaded.scenario("station_nominal")
    world = CabinWorld(replace(scenario, crew_offsets_s=(0.0,), crew_count=1), random.Random(0))
    total = sum(world.activities(float(t))[0].co2_kg_per_s for t in range(86400))
    assert round(total, 2) == CO2_PER_CREW_DAY_KG


def test_scrubber_anchor_matches_c25(loaded: Loaded) -> None:
    env = loaded.scenario("station_nominal").environment
    assert env.scrubber_kg_per_s_per_mmhg * TAS_PPCO2_MMHG * 86400.0 == pytest.approx(TAS_KG_PER_DAY)


def test_nominal_station_co2_stays_within_the_one_hour_limit(loaded: Loaded) -> None:
    per_minute = _minute_truth(loaded, "station_nominal", "co2_mmhg")
    hourly = _hourly_means(per_minute)
    assert max(hourly) <= CO2_ONE_HOUR_LIMIT_MMHG
    low, high = ISS_CO2_RANGE_MMHG
    assert low <= statistics.fmean(per_minute) <= high


def test_degraded_scrubber_crosses_the_one_hour_limit(loaded: Loaded) -> None:
    hourly = _hourly_means(_minute_truth(loaded, "station_scrubber_degradation", "co2_mmhg"))
    assert hourly[0] <= CO2_ONE_HOUR_LIMIT_MMHG < max(hourly)


def test_station_pio2_in_the_normoxia_target(loaded: Loaded) -> None:
    pio2 = _minute_truth(loaded, "station_nominal", "pio2_mmhg", hours=6)
    low, high = PIO2_TARGET_MMHG
    assert all(low <= p <= high for p in pio2)


def test_exploration_atmosphere_pio2_matches_the_hand_value(loaded: Loaded) -> None:
    env = loaded.scenario("lunar_nominal").environment
    assert env.total_pressure_kpa == pytest.approx(8.2 * KPA_PER_PSI, abs=0.01)
    pio2 = _minute_truth(loaded, "lunar_nominal", "pio2_mmhg", hours=2)
    assert statistics.fmean(pio2) == pytest.approx(EXPLORATION_PIO2_MMHG, abs=0.3)
    assert min(pio2) > PIO2_MILD_HYPOXIA_MMHG


def test_slow_leak_lowers_pio2_below_mild_hypoxia_without_a_rapid_rate(loaded: Loaded) -> None:
    ticks = list(_ticks(loaded, "station_pressure_leak"))
    pio2 = [t.truth["pio2_mmhg"] for t in ticks if t.truth["pio2_mmhg"] is not None]
    assert min(pio2) < PIO2_MILD_HYPOXIA_MMHG
    limit_kpa_min = PRESSURE_RATE_LIMIT_PSI_MIN * KPA_PER_PSI
    rates = [r.quantity.value for t in ticks for r in t.readings if r.output == "pressure_rate"]
    assert rates
    assert max(abs(r) for r in rates) < limit_kpa_min
    assert min(rates) < 0


def test_fire_raises_co_particles_and_temperature_together(loaded: Loaded) -> None:
    ticks = list(_ticks(loaded, "station_fire"))
    co = [r.quantity.value for t in ticks for r in t.readings if r.device_id == "sim_ec_co_cabin"]
    assert max(co) > CO_SMAC_24H_PPM
    assert all(float(v).is_integer() for v in co)  # 1 ppm resolution
    peak_t = max(
        (t for t in ticks if t.truth["co_ppm"] is not None), key=lambda t: t.truth["co_ppm"] or 0.0
    ).t_s
    before = next(t for t in ticks if t.t_s >= peak_t - 3 * 3600)
    peak = next(t for t in ticks if t.t_s >= peak_t)
    for key in ("pm2_5_ugm3", "temperature_c", "tvoc_ugm3"):
        assert (peak.truth[key] or 0.0) > (before.truth[key] or 0.0), key


def test_co_cell_saturates_at_the_monitor_range(loaded: Loaded) -> None:
    kind = loaded.kinds["ec_co"]
    device = Device("sim_test_co", kind, (), random.Random(1))
    readings = device.sample(kind.warmup_s + 1, {"co_ppm": 5000.0})
    assert readings[0].quantity.value == CO_MONITOR_MAX_PPM
    assert readings[0].status == STATUS_SATURATED
    assert kind.output("co").resolution == CO_MONITOR_RESOLUTION_PPM


def test_ammonia_leak_passes_the_one_hour_smac(loaded: Loaded) -> None:
    assert max(_minute_truth(loaded, "station_ammonia_leak", "nh3_ppm")) > NH3_SMAC_1H_PPM


def test_background_noise_is_within_nc50(loaded: Loaded) -> None:
    env = loaded.scenario("station_nominal").environment
    for band, limit in NC50_DB.items():
        assert env.noise_bands_db[band] - env.noise_margin_db <= limit
        assert env.noise_bands_db[band] == limit  # the reference curve is NC-50 itself
    exercise = {
        b: v - env.noise_margin_db + env.exercise_noise_boost_db for b, v in env.noise_bands_db.items()
    }
    quiet = {b: v - env.noise_margin_db for b, v in env.noise_bands_db.items()}
    assert a_weighted_level(exercise) > a_weighted_level(quiet)


def test_dust_generation_and_sampling_rates_match_ev01(loaded: Loaded) -> None:
    env = loaded.scenario("station_nominal").environment
    assert env.dust_total_mg_per_s * 60 == pytest.approx(DUST_TOTAL_MG_MIN)
    assert env.dust_respirable_mg_per_s * 60 == pytest.approx(DUST_RESPIRABLE_MG_MIN)
    for kind, interval in SAMPLING_S.items():
        assert loaded.kinds[kind].interval_s == interval, kind


def test_station_background_dose_rate_matches_the_dostel_records(loaded: Loaded) -> None:
    path = REPO_ROOT / loaded.wiring.datasets["dostel"].path
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [
            float(r["absorbed_dose_rate"]) for r in csv.DictReader(handle) if r["instrument_id"] == "DosTel1"
        ]
    assert len(rows) == DOSTEL1_RECORDS
    assert round(statistics.fmean(rows), 1) == DOSTEL1_MEAN_UGY_H
    assert loaded.scenario("station_nominal").environment.dose_rate_ugy_h == DOSTEL1_MEAN_UGY_H


def _table_4_excess_ugy() -> dict[str, float]:
    """DOSTEL-2 daily dose above the 10 Sep quiet day, per N40 Table 4 (the paper's own SPE dose split)."""
    table = json.loads(SPE_CASE.read_text(encoding="utf-8"))["documented_table"]
    daily = {row["date"]: float(row["DOSTEL-2"]) for row in table["rows"]}
    quiet = daily["2017-09-10"]
    excess = {day: daily[day] - quiet for day in ("2017-09-11", "2017-09-12")}
    assert sum(excess.values()) == pytest.approx(daily["SPE dose"], abs=0.05)
    return excess


def test_solar_particle_event_adds_table_4_dose_on_each_day_after_the_onset(loaded: Loaded) -> None:
    scenario = loaded.scenario("station_solar_particle_event")
    world = CabinWorld(scenario, random.Random(0))
    excess = [world.spe_excess_ugy_h(float(t)) for t in range(0, int(scenario.duration_s), 10)]
    onset_index = SPE_ONSET_S // 10
    assert not any(excess[:onset_index])  # nothing before the protons reach the ISS
    assert excess[onset_index + 1] > 0
    per_day = DAY_S // 10
    documented = _table_4_excess_ugy()
    for index, day in enumerate(("2017-09-11", "2017-09-12")):
        twin = sum(excess[index * per_day : (index + 1) * per_day]) * 10 / 3600
        assert twin == pytest.approx(documented[day], rel=0.01), day


def test_solar_particle_event_days_read_above_the_same_days_without_it(loaded: Loaded) -> None:
    """The twin's whole truth (background plus event) against the same seed with no event, day by day."""
    scenario = loaded.scenario("station_solar_particle_event")
    quiet = replace(scenario, events=())
    totals: list[list[float]] = []
    for case in (scenario, quiet):
        world = CabinWorld(case, random.Random(case.seed))
        days = [0.0, 0.0]
        for step in range(int(case.duration_s) // 10):
            world.step(10.0)
            days[min(1, step * 10 // DAY_S)] += (world.truth()["dose_rate_ugy_h"] or 0.0) * 10 / 3600
        totals.append(days)
    documented = _table_4_excess_ugy()
    for index, day in enumerate(("2017-09-11", "2017-09-12")):
        assert totals[0][index] - totals[1][index] == pytest.approx(documented[day], rel=0.02), day


def test_ndir_ppm_needs_cabin_pressure_for_mmhg(loaded: Loaded) -> None:
    """ENVIRONMENTS.md section 2: the same ppm is a lower partial pressure in a low-pressure habitat."""
    lunar = _minute_truth(loaded, "lunar_nominal", "co2_ppm", hours=1)
    lunar_mmhg = _minute_truth(loaded, "lunar_nominal", "co2_mmhg", hours=1)
    pressure = _minute_truth(loaded, "lunar_nominal", "pressure_kpa", hours=1)
    for ppm, mmhg, kpa in zip(lunar, lunar_mmhg, pressure, strict=True):
        # NASA-SOURCES.md section 2: ppCO2 = ppm x total pressure in mmHg / 1,000,000 (dry gas here).
        assert ppm * kpa * MMHG_PER_KPA / 1e6 == pytest.approx(mmhg, rel=0.03)
