"""R-E23-2 / DE23-11: the four plausibility checks on every input (T-E23-025, 070).

Golden expectations come from the NASA files under docs/product/nasa-data/ read here with their own small run
and step finders, never from the criteria under test. The criteria under test are the shipped catalog's.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

import pytest
from e23_helpers import BOUNDS, NASA, ROOT, sensor, ucum_for, utc
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.testing import MemoryUnits
from crew_health.sensors.adapters.config_reader import SensorConfig, load_sensor_config
from crew_health.sensors.domain.channel import PlausibilityCriteria, SensorTypeFacts
from crew_health.sensors.domain.loader_checks import check_catalog
from crew_health.sensors.domain.plausibility import (
    CRITERIA_MISSING,
    HISTORY_BOUND_TOO_SMALL,
    INSUFFICIENT_HISTORY,
    OUT_OF_RANGE,
    RATE_EXCEEDED,
    SATURATED,
    STUCK_VALUE,
    TIME_UNAVAILABLE,
    assess,
    registration_policy,
)
from crew_health.sensors.domain.reading import ReadingFact

pytestmark = [pytest.mark.e23, pytest.mark.trace("T-E23-025", "T-E23-070", "DE23-11", "R-E23-2")]
EXAMPLES = settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))
BOUND = 64
T0 = utc(2026, 1, 1)


@cache
def shipped() -> SensorConfig:
    names = {
        ConfigName.SENSOR_CATALOG: ROOT / "config/sensor-catalog.json",
        ConfigName.SENSOR_LIMITS: ROOT / "config/sensor-limits.json",
        ConfigName.ENVIRONMENT_PROFILES: ROOT / "config/environment-profiles.json",
        ConfigName.UNIT_MAPPING: ROOT / "config/unit-mapping.json",
        ConfigName.ENVIRONMENT_HAZARDS: ROOT / "docs/product/environment-hazards.json",
    }
    return load_sensor_config(lambda name: names[name].read_bytes())


def criteria_of(config: SensorConfig) -> PlausibilityCriteria:
    found = sensor(config, "co2").plausibility
    assert found is not None
    return found


def fact(at: datetime | None, value: str | Decimal, seq: int = 1, state: str = "known") -> ReadingFact:
    return ReadingFact(
        f"rdg_{seq}",
        "ch_test",
        seq,
        at,
        at,
        state,
        Decimal(value),
        "accepted",
        "SIM",
        "seg_1",  # type: ignore[arg-type]
    )


def series(count: int, step_s: int, value: str | Decimal, start: datetime = T0) -> list[ReadingFact]:
    return [fact(start + timedelta(seconds=i * step_s), value, i + 1) for i in range(count)]


def verdict(kind: SensorTypeFacts, reading: ReadingFact, history: list[ReadingFact]) -> tuple[str, str]:
    result = assess(kind, reading, history, history_bound=BOUND)
    return result.status, result.reason


def varied(count: int, step_s: int, start: datetime = T0) -> list[ReadingFact]:
    """A history that is plainly not stuck: two alternating values close together."""
    return [
        fact(start + timedelta(seconds=i * step_s), "1" if i % 2 else "1.0001", i + 1) for i in range(count)
    ]


# --- missing criteria -----------------------------------------------------------------------------------------


def test_a_sensor_type_without_criteria_is_unavailable_never_passed(config: SensorConfig) -> None:
    gaps = [k for k in config.sensor_types if k.plausibility is None]
    assert {k.id for k in gaps} >= {"rad", "rad_day", "o2", "noise", "air", "dust", "water"}
    for kind in gaps:
        result = assess(kind, fact(T0, "1"), series(70, 60, "2"), history_bound=BOUND)
        assert (result.status, result.reason, result.evaluable) == ("unavailable", CRITERIA_MISSING, False)


def test_live_registration_follows_the_catalog(config: SensorConfig) -> None:
    for kind in config.sensor_types:
        live = registration_policy("LIVE", criteria_available=kind.plausibility is not None)
        assert live.register is (kind.plausibility is not None)


# --- one property per check ---------------------------------------------------------------------------------


@EXAMPLES
@given(st.integers(-200000, 2_000_000))
def test_property_range_rejects_exactly_what_lies_outside(hundredths: int) -> None:
    config = shipped()
    criteria, kind = criteria_of(config), sensor(config, "co2")
    value = Decimal(hundredths) / 100
    status, reason = verdict(kind, fact(T0 + timedelta(days=1), value, 99), varied(5, 60))
    if value < criteria.minimum or value > criteria.maximum:
        assert (status, reason) == ("failed", OUT_OF_RANGE)
    else:
        assert reason != OUT_OF_RANGE


def test_every_saturation_value_is_rejected_and_range_edges_are_not_confused(config: SensorConfig) -> None:
    criteria, kind = criteria_of(config), sensor(config, "co2")
    assert criteria.saturation
    for value in criteria.saturation:
        assert verdict(kind, fact(T0 + timedelta(days=1), value, 99), varied(5, 60)) == ("failed", SATURATED)
    assert verdict(kind, fact(T0, criteria.maximum + Decimal("0.01")), [])[1] == OUT_OF_RANGE


@EXAMPLES
@given(st.integers(1, 100_000), st.integers(-3_000_000, 3_000_000))
def test_property_rate_fails_exactly_above_the_maximum(seconds: int, micro: int) -> None:
    config = shipped()
    criteria, kind = criteria_of(config), sensor(config, "co2")
    delta = Decimal(micro) / 1_000_000
    base = criteria.minimum + 100
    moved = base + delta
    if delta == 0 or not criteria.minimum < moved < criteria.maximum or moved in criteria.saturation:
        return
    previous = fact(T0, base, 1)
    status, reason = verdict(kind, fact(T0 + timedelta(seconds=seconds), base + delta, 2), [previous])
    if abs(delta) > criteria.max_rate_per_s * seconds:
        assert (status, reason) == ("failed", RATE_EXCEEDED)
    else:
        assert reason != RATE_EXCEEDED


@EXAMPLES
@given(st.integers(2, 60), st.integers(1, 200_000))
def test_property_stuck_needs_both_the_count_and_the_span(count: int, step_s: int) -> None:
    config = shipped()
    criteria, kind = criteria_of(config), sensor(config, "co2")
    same = criteria.minimum + 1
    window = series(count, step_s, same)
    span = (count - 1) * step_s
    status, reason = verdict(kind, window[-1], window[:-1])
    if count < criteria.stuck_count:
        assert (status, reason) == ("unavailable", INSUFFICIENT_HISTORY)  # never proved, never passed
    elif span >= criteria.stuck_min_span_s:
        assert (status, reason) == ("failed", STUCK_VALUE)
    else:
        assert status == "passed"
    assert status != "passed" or count >= criteria.stuck_count


@EXAMPLES
@given(st.integers(0, 70))
def test_property_a_different_value_in_the_window_proves_not_stuck(length: int) -> None:
    config = shipped()
    kind = sensor(config, "co2")
    history = varied(length, 300)
    status, _ = verdict(kind, fact(T0 + timedelta(seconds=length * 300), "1.2", 999), history)
    assert status == ("passed" if length >= 1 else "unavailable")  # one earlier reading proves the rate


def test_insufficient_history_is_never_a_pass(config: SensorConfig) -> None:
    kind = sensor(config, "co2")
    assert verdict(kind, fact(T0, "1"), []) == ("unavailable", INSUFFICIENT_HISTORY)
    assert verdict(kind, fact(None, "1"), varied(3, 60)) == ("unavailable", TIME_UNAVAILABLE)
    assert verdict(kind, fact(T0, "1", state="unknown"), varied(3, 60))[0] == "unavailable"
    same_instant = [fact(T0, "1", 1)]  # a reading at the same instant is not an earlier reading
    assert verdict(kind, fact(T0, "1", 2), same_instant) == ("unavailable", INSUFFICIENT_HISTORY)
    small = assess(kind, fact(T0 + timedelta(days=9), "1.0001", 7), varied(5, 60), history_bound=3)
    assert (small.status, small.reason) == ("unavailable", HISTORY_BOUND_TOO_SMALL)


def test_a_failure_wins_over_an_unproved_check(config: SensorConfig) -> None:
    kind = sensor(config, "co2")
    jump = fact(T0 + timedelta(seconds=1), Decimal(100))
    assert verdict(kind, jump, [fact(T0, "1")]) == ("failed", RATE_EXCEEDED)


# --- golden cases from the NASA telemetry ----------------------------------------------------------------------

RR1 = NASA / "habitat-environment" / "eda_rr1_telemetry.csv"
RR5 = NASA / "habitat-environment" / "eda_rr5_telemetry.csv"
PA_PER_MMHG = Decimal("133.3224")
CEILING_PA = Decimal(103000)  # NASA-STD-3001 Vol 2 Rev F V2 6006: at most 103 kPa


def ppm_to_mmhg(ppm: Decimal) -> Decimal:
    return ppm * (CEILING_PA / PA_PER_MMHG) / Decimal(1_000_000)


def rows(path: Any) -> list[tuple[datetime, Decimal]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            (datetime.fromisoformat(r["time"]).replace(tzinfo=utc(2000, 1, 1).tzinfo), Decimal(r["co2_iss"]))
            for r in csv.DictReader(handle)
        ]


def longest_run(data: list[tuple[datetime, Decimal]]) -> tuple[int, int]:
    best, best_start, start = 1, 0, 0
    for i in range(1, len(data) + 1):
        if i == len(data) or data[i][1] != data[start][1]:
            if i - start > best:
                best, best_start = i - start, start
            start = i
    return best_start, best


def test_golden_rr1_held_stretch_is_flagged_from_its_38th_identical_sample(config: SensorConfig) -> None:
    kind, data = sensor(config, "co2"), rows(RR1)
    start, length = longest_run(data)
    # the file itself: 1,837 samples held at 2,646 ppm from 2014-09-21 04:24:00 (read independently here)
    assert (length, data[start][0], data[start][1]) == (
        1837,
        datetime(2014, 9, 21, 4, 24, tzinfo=data[0][0].tzinfo),
        2646,
    )
    facts = [fact(at, ppm_to_mmhg(ppm), i + 1) for i, (at, ppm) in enumerate(data[: start + 60])]
    outcomes = [verdict(kind, facts[i], facts[max(0, i - BOUND) : i]) for i in range(start - 2, start + 60)]
    flagged = [start - 2 + i for i, (s, r) in enumerate(outcomes) if (s, r) == ("failed", STUCK_VALUE)]
    assert flagged[0] == start + 37  # 38th sample, 07:29:00, 37 x 300 s after the first
    assert flagged == list(range(start + 37, start + 60))
    assert data[start + 37][0] == data[start][0] + timedelta(seconds=11100)
    assert all(s != "failed" for s, _ in outcomes[: 37 + 2 - 1])  # the first 37 identical samples are natural


def test_golden_rr5_longest_natural_run_of_37_samples_is_not_stuck(config: SensorConfig) -> None:
    kind, data = sensor(config, "co2"), rows(RR5)
    natural = next(
        i for i, (at, v) in enumerate(data) if at == datetime(2017, 7, 4, 12, 20, tzinfo=data[0][0].tzinfo)
    )
    assert {v for _, v in data[natural : natural + 37]} == {Decimal(2443)} and data[natural + 37][1] != 2443
    facts = [fact(at, ppm_to_mmhg(ppm), i + 1) for i, (at, ppm) in enumerate(data[: natural + 40])]
    for i in range(natural, natural + 40):
        assert verdict(kind, facts[i], facts[max(0, i - BOUND) : i])[0] == "passed"


def held_indexes(data: list[tuple[datetime, Decimal]]) -> set[int]:
    """The file's one held stretch: its longest run, read here (1,837 samples in RR-1, 2,666 in RR-5)."""
    start, length = longest_run(data)
    return set(range(start, start + length))


def test_golden_largest_real_step_passes_and_one_ppm_more_fails(config: SensorConfig) -> None:
    """The largest step between two samples outside the held stretches. RR-5's 673 ppm step (3,000 to 2,327 ppm,
    2017-06-10 11:05) leaves its 2,666 sample hold, so it measures the sensor resuming, not the cabin."""
    kind = sensor(config, "co2")
    steps = []
    for data in (rows(RR1), rows(RR5)):
        held = held_indexes(data)
        steps += [
            (abs(data[i + 1][1] - data[i][1]), data[i], data[i + 1])
            for i in range(len(data) - 1)
            if i not in held and i + 1 not in held and data[i + 1][0] - data[i][0] == timedelta(seconds=300)
        ]
    biggest, before, after = max(steps, key=lambda s: s[0])
    assert biggest == 542 and (before[1], after[1]) == (3965, 4507)  # RR-1, 2014-10-25 18:41 to 18:46
    prior = fact(before[0], ppm_to_mmhg(before[1]), 1)
    real = fact(after[0], ppm_to_mmhg(after[1]), 2)
    assert verdict(kind, real, [prior])[1] != RATE_EXCEEDED
    beyond = fact(after[0], ppm_to_mmhg(after[1] + 1), 2)
    assert verdict(kind, beyond, [prior]) == ("failed", RATE_EXCEEDED)


def test_golden_the_step_out_of_the_rr5_hold_is_flagged_as_a_rate_jump(config: SensorConfig) -> None:
    kind, data = sensor(config, "co2"), rows(RR5)
    last = max(held_indexes(data))
    assert (data[last][1], data[last + 1][1]) == (3000, 2327)
    prior = fact(data[last][0], ppm_to_mmhg(data[last][1]), 1)
    resumed = fact(data[last + 1][0], ppm_to_mmhg(data[last + 1][1]), 2)
    assert verdict(kind, resumed, [prior]) == ("failed", RATE_EXCEEDED)


def test_golden_rr1_cabin_values_are_in_range_and_the_ceiling_is_the_nasa_total_pressure(
    config: SensorConfig,
) -> None:
    criteria = criteria_of(config)
    data = rows(RR1) + rows(RR5)
    assert all(criteria.minimum <= ppm_to_mmhg(v) <= criteria.maximum for _, v in data)
    assert criteria.minimum == 0 and criteria.maximum >= ppm_to_mmhg(
        Decimal(1_000_000)
    )  # 100 % CO2 at 103 kPa
    assert criteria.maximum - ppm_to_mmhg(Decimal(1_000_000)) < Decimal("0.01")
    assert min(v for _, v in data) > 0  # why a reading of exactly 0 is a clipped floor


def test_every_criterion_carries_a_source_and_non_nasa_ones_say_so(config: SensorConfig) -> None:
    for kind in config.sensor_types:
        for source in () if kind.plausibility is None else kind.plausibility.sources:
            assert source.source_ids and source.citation
            assert source.basis != "non_nasa" or "non-NASA" in source.citation


# --- a configuration edit changes the outcome, no code change -------------------------------------------------


def catalog_with(raw: dict[Any, bytes], edit: Any) -> SensorConfig:
    document = json.loads(raw[ConfigName.SENSOR_CATALOG])
    edit(document)
    changed = {**raw, ConfigName.SENSOR_CATALOG: json.dumps(document, ensure_ascii=False).encode("utf-8")}
    return load_sensor_config(lambda name: changed[name])


def test_a_criteria_edit_changes_the_outcome_with_no_code_change(raw_documents: dict[Any, bytes]) -> None:
    reading, history = fact(T0 + timedelta(seconds=10), "3.0"), [fact(T0, "1.0")]

    def co2(document: dict[str, Any]) -> dict[str, Any]:
        return next(s for s in document["sensor_types"] if s["id"] == "co2")

    shipped = catalog_with(raw_documents, lambda d: None)
    assert verdict(sensor(shipped, "co2"), reading, history) == ("failed", RATE_EXCEEDED)  # 0.2 mmHg/s
    wider = catalog_with(
        raw_documents, lambda d: co2(d)["plausibility"]["rate"].update({"max_per_second": 1})
    )
    assert verdict(sensor(wider, "co2"), reading, history)[0] == "passed"
    narrower = catalog_with(raw_documents, lambda d: co2(d)["plausibility"]["range"].update({"max": 2}))
    assert verdict(sensor(narrower, "co2"), reading, history) == ("failed", OUT_OF_RANGE)
    removed = catalog_with(raw_documents, lambda d: co2(d).pop("plausibility"))
    assert verdict(sensor(removed, "co2"), reading, history) == ("unavailable", CRITERIA_MISSING)
    assert (
        registration_policy(
            "LIVE", criteria_available=sensor(removed, "co2").plausibility is not None
        ).register
        is False
    )


# --- the loader check rejects an incoherent criteria object ---------------------------------------------------


def findings(config: SensorConfig, units: MemoryUnits, change: Any) -> list[str]:
    criteria = criteria_of(config)
    kinds = tuple(
        replace(k, plausibility=change(criteria)) if k.id == "co2" else k for k in config.sensor_types
    )
    return [f.message for f in check_catalog(kinds, BOUNDS, units, ucum_for) if "/plausibility" in f.pointer]


def test_the_shipped_criteria_pass_the_loader_check(config: SensorConfig, units: MemoryUnits) -> None:
    assert findings(config, units, lambda c: c) == []


def test_the_loader_check_rejects_incoherent_criteria(config: SensorConfig, units: MemoryUnits) -> None:
    cases = {
        "below max": lambda c: replace(c, minimum=c.maximum),
        "within the range": lambda c: replace(c, saturation=(c.maximum + 1,)),
        "positive": lambda c: replace(c, max_rate_per_s=Decimal(-1)),
        "two or more": lambda c: replace(c, stuck_count=1),
        "span": lambda c: replace(c, stuck_min_span_s=Decimal(0)),
        "exactly once": lambda c: replace(c, sources=c.sources[:3]),
        "source IDs": lambda c: replace(c, sources=(replace(c.sources[0], source_ids=()), *c.sources[1:])),
        "says so": lambda c: replace(c, sources=(replace(c.sources[0], basis="non_nasa"), *c.sources[1:])),
    }
    for fragment, change in cases.items():
        assert any(fragment in message for message in findings(config, units, change)), fragment


def test_a_flat_run_that_fills_the_whole_history_cannot_be_proved_not_stuck(config: SensorConfig) -> None:
    """Fast sampling: 64 identical readings five seconds apart span 320 s, far below the sourced span, but the run
    may be older than the history can show, so the answer is unavailable, never passed."""
    kind = sensor(config, "co2")
    history = series(BOUND, 5, "1")
    now = fact(T0 + timedelta(seconds=BOUND * 5), "1", 999)
    assert verdict(kind, now, history) == ("unavailable", INSUFFICIENT_HISTORY)
    short = series(40, 5, "1")  # not full: the run really starts at the first reading and is short in time
    assert verdict(kind, fact(T0 + timedelta(seconds=200), "1", 999), short)[0] == "passed"
