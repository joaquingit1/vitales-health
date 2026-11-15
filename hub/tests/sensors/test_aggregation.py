"""E23 section 12 tests T-E23-017, 022 to 025, 030 to 036, 063, 075, 077, 080 (pure rule part).

Golden expectations (RR-1 and DosTel1) are the independently checked numbers of E23 8, CONTRACTS 13 and the
conformance report, not values derived from the documents under test.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from e23_helpers import (
    SCAN_MAX,
    UCUM,
    assumed_pressure,
    evaluation_input,
    point,
    read_dostel1,
    read_rr1_ppm,
    sensor,
    ucum_for,
    utc,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.kernel.testing import MemoryUnits
from crew_health.kernel.units import PressureContext, Quantity, Unit
from crew_health.sensors.adapters.config_reader import SensorConfig
from crew_health.sensors.domain.aggregation import (
    DirectWindows,
    aggregate_direct,
    coverage,
    energy,
    window_instants,
)
from crew_health.sensors.domain.buckets import (
    BucketedWindows,
    BucketPartial,
    BucketSpec,
    bucket_spec_for,
    build_buckets,
    touched_by_insert,
)
from crew_health.sensors.domain.channel import AcceptedUnit, AggregationSpec, SensorTypeFacts
from crew_health.sensors.domain.conversion import convert_wire_reading
from crew_health.sensors.domain.errors import (
    InvalidUnit,
    InvalidValue,
    SensorComputationFailure,
    WindowScanBound,
)
from crew_health.sensors.domain.evaluation import (
    crosses,
    evaluate_channel,
    evaluate_limits,
)
from crew_health.sensors.domain.freshness import (
    ReplayReference,
    TimeFacts,
    advance_replay_reference,
    decide_freshness,
    select_reference,
)
from crew_health.sensors.domain.loader_checks import check_catalog
from crew_health.sensors.domain.plausibility import assess, registration_policy
from crew_health.sensors.domain.reading import (
    Instant,
    ReadingFact,
    advance_window_end,
    collapse_instants,
    round_wire,
)

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None, suppress_health_check=list(HealthCheck))
MMHG = Unit("mm[Hg]")


def reading(
    at: datetime | None,
    value: str,
    *,
    seq: int = 1,
    state: str = "known",
    quality: str = "accepted",
    origin: str = "LIVE",
    receive: datetime | None = None,
) -> ReadingFact:
    return ReadingFact(
        f"rdg_{seq}",
        "ch_test",
        seq,
        at,
        receive,
        state,
        Decimal(value),
        quality,
        origin,
        "seg_1",  # type: ignore[arg-type]
    )


# --- conversion: T-E23-022 to 025 ---------------------------------------------------------------------


@pytest.mark.trace("T-E23-022", "R42", "S5.4")
@given(
    value=st.decimals(min_value=Decimal(-1000000), max_value=Decimal(1000000), allow_nan=False, places=4),
    index=st.integers(min_value=0, max_value=2),
)
@EXAMPLES
def test_conversion_is_exact_finite_and_rounds_once(
    config: SensorConfig, release: object, converter: object, value: Decimal, index: int
) -> None:
    kind = sensor(config, "o2")
    accepted = kind.accepted_units[index % len(kind.accepted_units)]
    result = convert_wire_reading(
        converter,
        release,
        kind,
        value=value,
        unit=accepted.unit,
        canonical_unit=MMHG,
        pressure=None,  # type: ignore[arg-type]
    )
    expected = value * (accepted.factor if accepted.factor is not None else Decimal(1))
    assert result.converted.canonical.value == expected  # unrounded internal value
    assert result.wire_value == round_wire(expected)
    assert result.converted.original.value == value and result.converted.original.unit == accepted.unit
    assert (result.conversion is None) == (accepted.conversion == "identity")


@pytest.mark.trace("T-E23-022", "R42", "S5.4")
@given(unit=st.text(max_size=12))
@EXAMPLES
def test_unknown_unit_is_rejected(
    config: SensorConfig, release: object, converter: object, unit: str
) -> None:
    kind = sensor(config, "o2")
    if any(a.unit == unit for a in kind.accepted_units):
        return
    with pytest.raises(InvalidUnit):
        convert_wire_reading(
            converter,
            release,
            kind,
            value=Decimal(1),
            unit=unit,
            canonical_unit=MMHG,
            pressure=None,  # type: ignore[arg-type]
        )


@pytest.mark.trace("T-E23-022", "C13", "S5.4")
def test_every_catalog_unit_maps_to_ucum_and_dimensions_agree(
    config: SensorConfig, units: MemoryUnits
) -> None:
    from e23_helpers import BOUNDS

    assert check_catalog(config.sensor_types, BOUNDS, units, ucum_for) == ()
    for kind in config.sensor_types:
        assert ucum_for(kind.id, kind.unit) is not None
        for accepted in kind.accepted_units:
            assert ucum_for(kind.id, accepted.unit) in {code for code, _, _ in UCUM.values()}


@pytest.mark.trace("T-E23-023", "C13")
def test_4646_ppm_at_assumed_760_is_3_531_mmhg_with_basis(
    config: SensorConfig, release: object, converter: object
) -> None:
    result = convert_wire_reading(
        converter,
        release,
        sensor(config, "co2"),
        value=Decimal("4646"),
        unit="ppm",  # type: ignore[arg-type]
        canonical_unit=MMHG,
        pressure=assumed_pressure(),
    )
    assert result.wire_value == Decimal("3.5310")
    assert result.conversion is not None
    assert result.conversion.method == "ppm_to_mmhg"
    assert result.conversion.pressure_basis == "assumed"
    assert result.conversion.cabin_pressure == Quantity(Decimal(760), MMHG)
    assert result.converted.original.value == Decimal("4646")


@pytest.mark.trace("T-E23-024", "DE23-6", "T-E23-090")
@pytest.mark.parametrize("pressure", [None, "zero", "negative", "unsourced"])
def test_ppm_without_a_usable_pressure_is_rejected_never_defaulted(
    config: SensorConfig, release: object, converter: object, pressure: str | None
) -> None:
    context: PressureContext | None = None
    if pressure == "zero":
        context = assumed_pressure("0")
    elif pressure == "negative":
        context = assumed_pressure("-1")
    elif pressure == "unsourced":
        context = PressureContext(Quantity(Decimal(760), MMHG), "assumed", "", None)
    with pytest.raises(InvalidValue):
        convert_wire_reading(
            converter,
            release,
            sensor(config, "co2"),
            value=Decimal(4646),
            unit="ppm",  # type: ignore[arg-type]
            canonical_unit=MMHG,
            pressure=context,
        )


@pytest.mark.trace("T-E23-025", "DE23-11", "AGENTS5")
def test_non_finite_values_are_rejected_at_every_boundary(
    config: SensorConfig, release: object, converter: object
) -> None:
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(InvalidValue):
            convert_wire_reading(
                converter,
                release,
                sensor(config, "o2"),
                value=Decimal(bad),
                unit="mmHg",  # type: ignore[arg-type]
                canonical_unit=MMHG,
                pressure=None,
            )
        with pytest.raises(InvalidValue):
            reading(utc(2026, 1, 1), bad)
        with pytest.raises(InvalidValue):
            Instant(utc(2026, 1, 1), Decimal(bad), Decimal(bad))


@pytest.mark.trace("T-E23-025", "DE23-11", "D3")
def test_unknown_future_rejected_and_pre_activation_readings_never_enter_a_window() -> None:
    active = utc(2026, 1, 1, 12)
    rows = [
        reading(utc(2026, 1, 1, 12, 1), "1", seq=1),
        reading(utc(2026, 1, 1, 12, 2), "2", seq=2, state="unknown"),
        reading(None, "3", seq=3, state="unknown"),
        reading(utc(2026, 1, 1, 12, 3), "4", seq=4, state="future"),
        reading(utc(2026, 1, 1, 12, 4), "5", seq=5, quality="rejected"),
        reading(utc(2026, 1, 1, 11, 59), "6", seq=6),
    ]
    kept = collapse_instants(rows, activated_at=active, max_rows=100)
    assert [(i.at, i.low) for i in kept] == [(utc(2026, 1, 1, 12, 1), Decimal(1))]
    assert collapse_instants(rows, activated_at=None, max_rows=100) == ()  # before mission init


@pytest.mark.trace("T-E23-025", "DE23-13")
def test_replay_activation_cutoff_uses_receive_time_live_uses_observed_time() -> None:
    active = utc(2026, 11, 14, 9)
    old_dataset_time = utc(2014, 10, 7, 15, 36)
    replay_after = reading(old_dataset_time, "1", origin="REPLAY", receive=utc(2026, 11, 14, 9, 1))
    replay_before = reading(old_dataset_time, "2", seq=2, origin="REPLAY", receive=utc(2026, 11, 14, 8, 59))
    live_before = reading(utc(2026, 11, 14, 8, 59), "3", seq=3, receive=utc(2026, 11, 14, 9, 5))
    kept = collapse_instants([replay_after, replay_before, live_before], activated_at=active, max_rows=10)
    assert [i.low for i in kept] == [Decimal(1)]


@pytest.mark.trace("T-E23-025", "T-E23-070", "DE23-11", "R-E23-2")
def test_missing_plausibility_criteria_never_pass_and_nothing_is_evaluated(config: SensorConfig) -> None:
    kind = sensor(config, "co2")
    result = assess(kind, reading(utc(2026, 1, 1), "3"))
    assert result.status == "unavailable" and not result.evaluable and result.failed_check is None
    rows = [point(utc(2014, 10, 7, 15, 36), "3.9")]
    inp = evaluation_input(config, "co2", rows, interval=300)
    evaluation = evaluate_channel(inp, result)
    assert evaluation.status == "insufficient_data"
    assert evaluation.level is None and evaluation.unavailable_reason == "criteria_missing"
    assert all(r.crossed is None and r.clear_holds is None for r in evaluation.results)
    assert evaluation.aggregated_value is None


@pytest.mark.trace("T-E23-070", "DE23-8", "R-E23-2")
def test_live_is_refused_and_replay_sim_capture_without_evaluation_when_criteria_missing() -> None:
    live = registration_policy("LIVE", criteria_available=False)
    assert (live.register, live.capture, live.evaluate) == (False, False, False)
    for origin in ("REPLAY", "SIM"):
        policy = registration_policy(origin, criteria_available=False)  # type: ignore[arg-type]
        assert (policy.register, policy.capture, policy.evaluate) == (True, True, False)
    assert registration_policy("LIVE", criteria_available=True).evaluate is True


# --- aggregation properties: T-E23-030 ------------------------------------------------------------------

BASE = utc(2026, 1, 1)
instant_sets = st.lists(
    st.tuples(st.integers(0, 600), st.integers(-500, 3000), st.integers(0, 40)),
    min_size=1,
    max_size=24,
    unique_by=lambda t: t[0],
)


def build_series(raw: list[tuple[int, int, int]]) -> tuple[Instant, ...]:
    series = []
    for offset, value, spread in raw:
        low = Decimal(value) / Decimal(10)
        series.append(Instant(BASE + timedelta(seconds=offset), low, low + Decimal(spread) / Decimal(10)))
    return tuple(sorted(series, key=lambda i: i.at))


class ListStore:
    def __init__(self, series: tuple[Instant, ...], spec: BucketSpec) -> None:
        self.map = build_buckets(series, spec)

    def partials(self, spec: BucketSpec, first: int, last: int, limit: int) -> list[BucketPartial]:
        return [p for i, p in sorted(self.map.items()) if first <= i <= last][:limit]


class ListReader:
    def __init__(self, series: tuple[Instant, ...]) -> None:
        self.series = series

    def instants(self, after: datetime, until: datetime, limit: int) -> list[Instant]:
        return [i for i in self.series if after < i.at <= until][:limit]


def spec_of(kind: str, window: int) -> AggregationSpec:
    return AggregationSpec("agg", kind, window, "label")  # type: ignore[arg-type]


@pytest.mark.trace("T-E23-030", "C13", "R42")
@given(raw=instant_sets, window=st.integers(1, 700), interval=st.one_of(st.none(), st.integers(1, 90)))
@EXAMPLES
def test_aggregations_stay_inside_the_bounds_of_their_values(
    raw: list[tuple[int, int, int]], window: int, interval: int | None
) -> None:
    series = build_series(raw)
    end = series[-1].at
    for kind in ("mean", "time_weighted_mean", "max", "sum"):
        stats = aggregate_direct(
            spec_of(kind, window), end, series, expected_interval_seconds=interval, scan_max=SCAN_MAX
        )
        inside = window_instants(series, end, window)
        assert stats is not None and stats.n == len(inside)
        assert stats.low <= stats.high
        assert stats.coverage is not None and Decimal(0) <= stats.coverage <= Decimal(1)
        if kind in ("mean", "time_weighted_mean", "max"):
            assert min(i.low for i in inside) <= stats.low
            assert stats.high <= max(i.high for i in inside)


@pytest.mark.trace("T-E23-030", "C13")
@given(raw=instant_sets, window=st.integers(1, 700), seed=st.integers(0, 10**6))
@EXAMPLES
def test_arrival_order_does_not_change_the_result_and_empty_windows_have_no_value(
    raw: list[tuple[int, int, int]], window: int, seed: int
) -> None:
    rows = [
        reading(BASE + timedelta(seconds=o), str(Decimal(v) / 10), seq=n)
        for n, (o, v, _) in enumerate(raw, start=1)
    ]
    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)
    first = collapse_instants(rows, activated_at=BASE, max_rows=1000)
    second = collapse_instants(shuffled, activated_at=BASE, max_rows=1000)
    assert first == second
    spec = spec_of("mean", window)
    end = first[-1].at
    assert aggregate_direct(
        spec, end, first, expected_interval_seconds=None, scan_max=SCAN_MAX
    ) == aggregate_direct(spec, end, second, expected_interval_seconds=None, scan_max=SCAN_MAX)
    assert (
        aggregate_direct(
            spec, BASE - timedelta(days=1), first, expected_interval_seconds=None, scan_max=SCAN_MAX
        )
        is None
    )


@pytest.mark.trace("T-E23-030", "C13", "S5.4")
@given(
    raw=instant_sets,
    window=st.integers(1, 700),
    bucket=st.integers(1, 120),
    interval=st.one_of(st.none(), st.integers(1, 90)),
    kind=st.sampled_from(["mean", "time_weighted_mean", "max", "sum"]),
)
@EXAMPLES
def test_bucketed_equals_direct(
    raw: list[tuple[int, int, int]], window: int, bucket: int, interval: int | None, kind: str
) -> None:
    series = build_series(raw)
    spec = spec_of(kind, window)
    end = series[-1].at
    layout = bucket_spec_for(spec, bucket) or BucketSpec(bucket, "identity")
    bucketed = BucketedWindows(
        bucket, ListStore(series, layout), ListReader(series), series[-1], interval, SCAN_MAX, 10080
    )
    direct = DirectWindows(series, interval, SCAN_MAX)
    assert bucketed.stats(spec, end) == direct.stats(spec, end)


@pytest.mark.trace("T-E23-030", "T-E23-075", "C13", "S5.4")
@given(
    raw=st.lists(
        st.tuples(st.integers(0, 600), st.integers(300, 1300), st.integers(0, 20)),
        min_size=1,
        max_size=16,
        unique_by=lambda t: t[0],
    ),
    window=st.integers(1, 700),
    bucket=st.integers(1, 120),
    interval=st.one_of(st.none(), st.integers(1, 90)),
)
@EXAMPLES
def test_bucketed_energy_mean_equals_direct(
    raw: list[tuple[int, int, int]], window: int, bucket: int, interval: int | None
) -> None:
    series = build_series(raw)
    spec = spec_of("energy_mean", window)
    layout = bucket_spec_for(spec, bucket)
    assert layout is not None and layout.transform == "energy"
    bucketed = BucketedWindows(
        bucket, ListStore(series, layout), ListReader(series), series[-1], interval, SCAN_MAX, 10080
    )
    direct = DirectWindows(series, interval, SCAN_MAX)
    assert bucketed.stats(spec, series[-1].at) == direct.stats(spec, series[-1].at)


@pytest.mark.trace("T-E23-030", "T-E23-092")
@given(raw=instant_sets, bucket=st.integers(1, 120), pick=st.integers(0, 10**6))
@EXAMPLES
def test_a_late_reading_updates_only_the_buckets_it_touches(
    raw: list[tuple[int, int, int]], bucket: int, pick: int
) -> None:
    series = build_series(raw)
    late = series[pick % len(series)]
    rest = tuple(i for i in series if i is not late)
    for transform in ("identity", "energy"):
        layout = BucketSpec(bucket, transform)  # type: ignore[arg-type]
        stored = build_buckets(rest, layout) if rest else {}
        before = [i for i in rest if i.at < late.at]
        touched = touched_by_insert(before[-1].at if before else None, late.at, bucket)
        stored.update(build_buckets(series, layout, only=touched))
        assert stored == build_buckets(series, layout)


@pytest.mark.trace("T-E23-030", "C13")
def test_hold_weights_and_empty_window_have_no_value_and_no_interpolation() -> None:
    """Hand computed: holds 10 for 60 s then 20 for 120 s, window start excluded."""
    series = (
        point(BASE, "10"),
        point(BASE + timedelta(seconds=60), "20"),
        point(BASE + timedelta(seconds=180), "99"),
    )
    stats = aggregate_direct(
        spec_of("time_weighted_mean", 180),
        series[-1].at,
        series,
        expected_interval_seconds=None,
        scan_max=SCAN_MAX,
    )
    assert stats is not None
    assert stats.n == 2  # BASE is the excluded window start (E - W, E]
    assert stats.low == Decimal(20)  # only the 20 reading holds before E; nothing assumed before it
    gap = aggregate_direct(
        spec_of("mean", 10),
        BASE + timedelta(seconds=1000),
        series,
        expected_interval_seconds=None,
        scan_max=SCAN_MAX,
    )
    assert gap is None


@pytest.mark.trace("T-E23-030", "T-E23-092", "C13")
def test_coverage_formula_with_and_without_an_expected_interval() -> None:
    first, last = BASE, BASE + timedelta(seconds=1800)
    assert coverage(
        12, first_at=first, last_at=last, window_seconds=3600, expected_interval_seconds=300
    ) == Decimal(1)
    assert coverage(
        3, first_at=first, last_at=last, window_seconds=3600, expected_interval_seconds=300
    ) == Decimal("0.25")
    assert coverage(
        1, first_at=first, last_at=first, window_seconds=3600, expected_interval_seconds=None
    ) == Decimal(0)
    assert coverage(
        5, first_at=first, last_at=last, window_seconds=3600, expected_interval_seconds=None
    ) == Decimal("0.5")


# --- limits: T-E23-031 to 036, 063 ----------------------------------------------------------------------


def rr1_instants(
    config: SensorConfig, release: object, converter: object, *, until: datetime
) -> list[Instant]:
    kind = sensor(config, "co2")
    out = []
    for row in read_rr1_ppm():
        if row.at > until:
            continue
        converted = convert_wire_reading(
            converter,
            release,
            kind,
            value=row.value,
            unit="ppm",
            canonical_unit=MMHG,  # type: ignore[arg-type]
            pressure=assumed_pressure(),
        )
        out.append(Instant(row.at, converted.converted.canonical.value, converted.converted.canonical.value))
    return out


@pytest.mark.trace("T-E23-036", "R35", "V55", "D16", "CONTRACTS20")
def test_golden_rr1_co2_window(config: SensorConfig, release: object, converter: object) -> None:
    end = utc(2014, 10, 7, 15, 36)
    instants = rr1_instants(config, release, converter, until=end)
    inp = evaluation_input(config, "co2", instants, interval=300)  # the 5-minute file: channel interval 300 s
    mean_1h = next(a for a in inp.sensor_type.aggregations if a.id == "mean_1h")
    stats = inp.windows.stats(mean_1h, end)
    assert stats is not None
    assert stats.n == 12  # (1) twelve samples in (14:36, 15:36]
    assert stats.window_start == utc(2014, 10, 7, 14, 36)
    ppm_mean = (stats.low * 1_000_000 / Decimal(760)).quantize(Decimal("0.01"))
    assert ppm_mean == Decimal("4605.67")  # (2)
    assert round_wire(stats.low) == Decimal("3.5003")  # (3) at an assumed 760 mmHg
    assert stats.coverage == Decimal(1)  # (4)
    evaluation = evaluate_limits(inp)
    assert (evaluation.status, evaluation.level, evaluation.limit_id) == (
        "beyond_limit",
        "act",
        "co2_1h_act",
    )  # (5)
    assert evaluation.coverage == Decimal(1) and evaluation.window_end == end
    from crew_health.sensors.domain.card import format_value

    assert (
        format_value(stats.low, inp.sensor_type.decimals, inp.sensor_type.trim_trailing_zeros) == "3.5"
    )  # (6)
    assert evaluation.aggregated_value is not None and round_wire(evaluation.aggregated_value) == Decimal(
        "3.5003"
    )
    assert evaluation.pending_limit_ids == ()


@pytest.mark.trace("T-E23-036", "R35", "V55", "D16", "CONTRACTS20")
def test_golden_dostel1_two_hour_window_is_threshold_pending(config: SensorConfig) -> None:
    rows = read_dostel1()
    end = max(r.at for r in rows)
    assert end == utc(2022, 4, 2, 1, 4, 40)
    inp = evaluation_input(config, "rad", [point(r.at, r.value) for r in rows], interval=None)
    mean_2h = next(a for a in inp.sensor_type.aggregations if a.id == "mean_2h")
    stats = inp.windows.stats(mean_2h, end)
    assert stats is not None and stats.n == 80  # (7) eighty samples
    assert round_wire(stats.low) == Decimal("6.0819")
    evaluation = evaluate_limits(inp)
    assert evaluation.status == "threshold_pending" and evaluation.level is None  # (8)
    assert evaluation.pending_limit_ids == ("rad_rate_watch", "rad_rate_act")
    from crew_health.sensors.domain.card import format_value

    assert format_value(stats.low, inp.sensor_type.decimals, inp.sensor_type.trim_trailing_zeros) == "6.1"
    assert evaluation.results == ()  # a pending limit evaluates nothing


@pytest.mark.trace("T-E23-031", "C13", "R35")
def test_windowed_limit_below_min_coverage_is_insufficient_data_and_crosses_nothing(
    config: SensorConfig,
) -> None:
    end = utc(2014, 10, 7, 15, 36)
    rows = [point(end - timedelta(minutes=m), "3.9") for m in (0, 5, 10)]  # 3 of 12 expected: coverage 0.25
    evaluation = evaluate_limits(evaluation_input(config, "co2", rows, interval=300))
    assert evaluation.status == "insufficient_data" and evaluation.level is None
    assert all(r.crossed is None and r.reason == "insufficient_coverage" for r in evaluation.results)


@pytest.mark.trace("T-E23-032", "C13")
def test_only_active_warning_limits_of_the_active_environment_are_evaluated(config: SensorConfig) -> None:
    end = utc(2026, 1, 1, 12)
    rows = [point(end - timedelta(seconds=60 * m), "5") for m in range(0, 1440, 1)]  # dust above both limits
    for env, expected in (
        ("lunar_surface", {"dust_lunar_act"}),
        ("mars_surface", {"dust_mars_act", "dust_mars_peak_emergency"}),
    ):
        inp = evaluation_input(config, "dust", rows, interval=60, environment=env)
        assert {r.limit_id for r in evaluate_limits(inp).results} == expected
    station = evaluation_input(config, "dust", rows, interval=60, environment="station_leo")
    assert evaluate_limits(station).status == "no_limit_on_file"


@pytest.mark.trace("T-E23-033", "T-E23-034", "R35", "C13")
def test_pending_never_raises_and_display_only_never_changes_status(config: SensorConfig) -> None:
    end = utc(2026, 1, 1, 12)
    air_only_display = tuple(
        replace(limit, status="threshold_pending", value=None)
        if limit.limit_id == "air_index_watch"
        else limit
        for limit in config.limits
    )
    inp = evaluation_input(config, "air", [point(end, "9")], interval=None, limits=air_only_display)
    assert evaluate_limits(inp).status == "threshold_pending"  # never within_limit
    no_warning = tuple(limit for limit in config.limits if limit.limit_id != "air_index_watch")
    assert (
        evaluate_limits(
            evaluation_input(config, "air", [point(end, "9")], interval=None, limits=no_warning)
        ).status
        == "no_limit_on_file"
    )
    plain = evaluate_limits(evaluation_input(config, "air", [point(end, "9")], interval=None))
    assert plain.status == "beyond_limit" and plain.level == "watch" and plain.limit_id == "air_index_watch"
    calm = evaluate_limits(evaluation_input(config, "air", [point(end, "0.5")], interval=None))
    assert calm.status == "within_limit"


@pytest.mark.trace("T-E23-035", "C13", "V56")
def test_clear_holds_is_none_for_stale_non_advancing_untrusted_and_failed(config: SensorConfig) -> None:
    end = utc(2026, 1, 1, 12)
    calm = [point(end, "150")]
    ok = evaluate_limits(evaluation_input(config, "o2", calm, interval=5))
    assert {r.limit_id: r.clear_holds for r in ok.results}["o2_low_watch"] is True
    for kwargs in ({"freshness": "stale"}, {"advanced": False}, {"trusted": False}):
        out = evaluate_limits(evaluation_input(config, "o2", calm, interval=5, **kwargs))  # type: ignore[arg-type]
        assert all(r.clear_holds is None for r in out.results), kwargs
    low = evaluate_limits(evaluation_input(config, "o2", [point(end, "100")], interval=5))
    assert {r.limit_id: r.clear_holds for r in low.results}["o2_low_watch"] is False
    scratch = tuple(
        replace(limit, clear=replace(limit.clear, value=None)) if limit.limit_id == "o2_low_watch" else limit
        for limit in config.limits
    )
    unset = evaluate_limits(evaluation_input(config, "o2", calm, interval=5, limits=scratch))
    assert {r.limit_id: r.clear_holds for r in unset.results}["o2_low_watch"] is None


@pytest.mark.trace("T-E23-063", "CONTRACTS682", "R35")
def test_o2_low_emergency_crosses_on_one_reading_without_a_coverage_gate(config: SensorConfig) -> None:
    only = [point(utc(2026, 1, 1, 12), "100")]
    evaluation = evaluate_limits(evaluation_input(config, "o2", only, interval=5))
    assert (evaluation.status, evaluation.level, evaluation.limit_id) == (
        "beyond_limit",
        "emergency",
        "o2_low_emergency",
    )
    by_id = {r.limit_id: r for r in evaluation.results}
    assert by_id["o2_low_emergency"].crossed is True and by_id["o2_low_emergency"].coverage is None
    assert (
        by_id["o2_low_act"].reason == "insufficient_coverage"
    )  # the windowed limit still waits for its window


@pytest.mark.trace("T-E23-036", "T-E23-063", "C13")
def test_highest_crossed_level_wins_and_ties_go_to_the_earlier_limit(config: SensorConfig) -> None:
    end = utc(2026, 1, 1, 12)
    both = evaluate_limits(evaluation_input(config, "noise", [point(end, "120")], interval=None))
    assert (both.level, both.limit_id) == ("emergency", "noise_peak_emergency")
    twice = tuple(
        replace(limit, limit_id="x_" + limit.limit_id)
        for limit in config.limits
        if limit.limit_id == "o2_low_watch"
    )
    again = tuple(replace(limit, limit_id="y_" + limit.limit_id) for limit in twice)
    out = evaluate_limits(
        evaluation_input(config, "o2", [point(end, "100")], interval=5, limits=(*twice, *again))
    )
    assert out.limit_id == "x_o2_low_watch"


@pytest.mark.trace("T-E23-036", "C13")
def test_status_precedence_no_reading_then_stale(config: SensorConfig) -> None:
    empty = evaluate_limits(evaluation_input(config, "o2", [], interval=5, freshness="never_reported"))
    assert empty.status == "no_reading" and empty.level is None and empty.aggregated_value is None
    stale = evaluate_limits(
        evaluation_input(config, "o2", [point(utc(2026, 1, 1), "100")], interval=5, freshness="stale")
    )
    assert stale.status == "stale" and stale.level is None  # a crossing is never raised from a stale value
    assert all(r.crossed is None for r in stale.results)


def inspired_oxygen_type(config: SensorConfig, *, vapor: Decimal | None = Decimal(47)) -> SensorTypeFacts:
    """The o2 type with a kPa ppO2 row. The catalog schema cannot name this conversion yet (R-E23-22), so the row
    is built here from the shipped type; PH2O 47 mmHg is EV01 Table 6.2-1 (p.51)."""
    kind = sensor(config, "o2")
    row = AcceptedUnit("kPa", "ppo2_to_pio2", Decimal("7.50062"), vapor, "EV01 Table 6.2-1 p.51")
    return replace(kind, accepted_units=(*kind.accepted_units, row))


def ppo2_converter() -> object:
    from crew_health.kernel.testing import MemoryWireConverter
    from crew_health.kernel.units import ConvertedQuantity

    def convert(value, indicator, rel, to, pressure):  # type: ignore[no-untyped-def]
        # kPa to mmHg only; 7.50062 mmHg per kPa is the unit factor, the PIO2 step is not here
        return ConvertedQuantity(
            value, Quantity(value.value * Decimal("7.50062"), to), "scale", "t", None, "r"
        )

    return MemoryWireConverter(convert)


@pytest.mark.trace("T-E23-075", "S5.4", "D12", "EV01")
def test_ambient_ppo2_becomes_inspired_oxygen_with_cabin_pressure_and_raises_at_the_limits_level(
    config: SensorConfig, release: object
) -> None:
    """Golden checked by hand from EV01 Table 6.2-1 (p.51), PIO2 = (PB - 47 mmHg) x FIO2, not from the code.

    8.2 psia x 6.894757 kPa/psi = 56.537 kPa x 7.50062 = 424.06 mmHg. FIO2 0.2995 gives ppO2 = 127.005 mmHg
    (16.9322 kPa) and PIO2 = (424.06 - 47) x 0.2995 = 112.93 mmHg: below the 122 floor, so emergency, not watch.
    """
    kind = inspired_oxygen_type(config)
    pressure = PressureContext(Quantity(Decimal("424.0585"), MMHG), "measured", "adapter:test", None)
    out = convert_wire_reading(
        ppo2_converter(),  # type: ignore[arg-type]
        release,  # type: ignore[arg-type]
        kind,
        value=Decimal("16.9322"),
        unit="kPa",
        canonical_unit=MMHG,
        pressure=pressure,
    )
    assert abs(out.converted.canonical.value - Decimal("112.93")) < Decimal("0.02")
    assert out.converted.conversion == "ppo2_to_pio2" and out.conversion is not None
    assert out.conversion.method == "ppo2_to_pio2" and out.conversion.pressure_basis == "measured"
    at = utc(2026, 1, 1, 12)
    point_value = out.converted.canonical.value
    evaluation = evaluate_limits(
        evaluation_input(config, "o2", [Instant(at, point_value, point_value)], interval=5)
    )
    assert (evaluation.status, evaluation.limit_id) == ("beyond_limit", "o2_low_emergency")
    assert evaluation.level != "watch"


@pytest.mark.trace("T-E23-075", "S5.4", "DE23-6", "EV01")
def test_ppo2_without_cabin_pressure_or_a_sourced_vapor_pressure_is_not_converted(
    config: SensorConfig, release: object
) -> None:
    pressure = PressureContext(Quantity(Decimal("424.0585"), MMHG), "measured", "adapter:test", None)
    common = dict(value=Decimal("16.9322"), unit="kPa", canonical_unit=MMHG)
    with pytest.raises(InvalidValue):
        convert_wire_reading(
            ppo2_converter(),
            release,
            inspired_oxygen_type(config),
            pressure=None,
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidValue):  # a water vapor pressure that is not configured is never assumed
        convert_wire_reading(
            ppo2_converter(),  # type: ignore[arg-type]
            release,  # type: ignore[arg-type]
            inspired_oxygen_type(config, vapor=None),
            pressure=pressure,
            **common,  # type: ignore[arg-type]
        )


@pytest.mark.trace("T-E23-075", "S5.4", "D12")
def test_near_threshold_crossing_survives_four_place_wire_rounding(config: SensorConfig) -> None:
    """The unrounded PIO2 decides: a value that rounds to the limit on the wire still crosses it."""
    at = utc(2026, 1, 1, 12)
    below = Decimal("121.99996")  # shows as 122.0000 on the wire, below 122 exactly
    assert round_wire(below) == Decimal("122.0000")
    evaluation = evaluate_limits(evaluation_input(config, "o2", [Instant(at, below, below)], interval=5))
    assert (evaluation.status, evaluation.limit_id) == ("beyond_limit", "o2_low_emergency")
    assert crosses("below", round_wire(below), Decimal(122)) is False  # rounding first would have missed it


@pytest.mark.trace("T-E23-075", "S5.4", "D12")
def test_energy_mean_uses_precision_34_and_fails_typed_when_non_finite() -> None:
    quiet = utc(2026, 1, 1)
    even = (
        point(quiet, "60"),
        point(quiet + timedelta(seconds=30), "70"),
        point(quiet + timedelta(seconds=60), "70"),
    )
    stats = aggregate_direct(
        spec_of("energy_mean", 61), even[-1].at, even, expected_interval_seconds=None, scan_max=SCAN_MAX
    )
    assert stats is not None
    assert round_wire(stats.low) == Decimal("67.4036")  # 10 log10((1e6 + 1e7) / 2), checked independently
    constant = tuple(point(quiet + timedelta(seconds=s), "70") for s in (0, 30, 60))
    flat = aggregate_direct(
        spec_of("energy_mean", 61),
        constant[-1].at,
        constant,
        expected_interval_seconds=None,
        scan_max=SCAN_MAX,
    )
    assert flat is not None and round_wire(flat.low) == Decimal("70.0000")
    with pytest.raises(SensorComputationFailure):
        energy(Decimal("99999999999"))


@pytest.mark.trace("T-E23-080", "3.7", "AGENTS6")
def test_two_values_at_one_instant_cross_on_either_variant_clear_needs_both_display_is_worse(
    config: SensorConfig,
) -> None:
    at = utc(2026, 1, 1, 12)
    conflict = Instant(at, Decimal("120"), Decimal("150"))  # one source says 120 (crosses 122), one says 150
    out = evaluate_limits(evaluation_input(config, "o2", [conflict], interval=5))
    assert (out.status, out.level) == ("beyond_limit", "emergency")  # the low variant crosses
    assert out.conflict is True
    by_id = {r.limit_id: r for r in out.results}
    assert by_id["o2_low_watch"].crossed_low is True and by_id["o2_low_watch"].crossed_high is False
    assert by_id["o2_low_watch"].clear_holds is False  # clearing needs both variants to hold
    assert out.aggregated_value == Decimal("120")  # display: the variant with the higher crossed level
    safe = Instant(at, Decimal("200"), Decimal("210"))
    clean = evaluate_limits(evaluation_input(config, "o2", [safe], interval=5))
    assert clean.status == "within_limit"
    assert {r.limit_id: r.clear_holds for r in clean.results}["o2_low_watch"] is True
    assert clean.aggregated_value == Decimal("200")  # no crossing: nearer the card mark limit (127 mmHg)
    unmarked = evaluate_limits(
        evaluation_input(config, "water", [Instant(at, Decimal("0.2"), Decimal("0.4"))], interval=None)
    )
    assert unmarked.status == "no_limit_on_file" and unmarked.aggregated_value == Decimal(
        "0.4"
    )  # else the higher value


# --- scan bound: T-E23-083, 092 --------------------------------------------------------------------------


def peak_type(config: SensorConfig) -> tuple[object, tuple[object, ...]]:
    kind = sensor(config, "noise")
    spec = AggregationSpec("peak_1h", "max", 3600, "hourly peak")
    limit = replace(
        next(item for item in config.limits if item.limit_id == "noise_peak_emergency"),
        limit_id="noise_hour_peak",
        aggregation_id="peak_1h",
        min_coverage=Decimal(0),
        value=Decimal(100),
    )
    return replace(kind, aggregations=(*kind.aggregations, spec)), (limit,)


@pytest.mark.trace("T-E23-083", "T-E23-092", "3.7")
def test_a_max_window_over_the_scan_bound_is_not_evaluated_and_never_partial(config: SensorConfig) -> None:
    kind, limits = peak_type(config)
    end = utc(2026, 1, 1, 12)
    rows = [point(end - timedelta(seconds=s), "130") for s in range(0, 40)]
    over = evaluation_input(config, "noise", rows, interval=None, kind=kind, limits=limits, scan_max=39)  # type: ignore[arg-type]
    out = evaluate_limits(over)
    assert out.faults == ("WINDOW_SCAN_BOUND",)
    assert (
        out.results[0].crossed is None
        and out.results[0].clear_holds is None
        and out.results[0].reason == "scan_bound"
    )
    assert out.status == "insufficient_data"
    exact = evaluation_input(config, "noise", rows, interval=None, kind=kind, limits=limits, scan_max=40)  # type: ignore[arg-type]
    assert evaluate_limits(exact).status == "beyond_limit"
    with pytest.raises(WindowScanBound):
        aggregate_direct(
            AggregationSpec("p", "max", 3600, "x"),
            end,
            [point(r.at, "1") for r in map(lambda p: p, rows)],
            expected_interval_seconds=None,
            scan_max=39,
        )


@pytest.mark.trace("T-E23-092", "AGENTS6")
def test_row_and_bucket_bounds_at_zero_maximum_and_over_maximum() -> None:
    rows = [reading(BASE + timedelta(seconds=s), "1", seq=s + 1) for s in range(5)]
    assert collapse_instants([], activated_at=BASE, max_rows=0) == ()
    assert len(collapse_instants(rows, activated_at=BASE, max_rows=5)) == 5
    with pytest.raises(WindowScanBound):
        collapse_instants(rows, activated_at=BASE, max_rows=4)
    series = tuple(point(BASE + timedelta(seconds=s + 3), "1") for s in range(0, 600, 10))
    spec = spec_of("mean", 600)
    layout = BucketSpec(10, "identity")
    reader = ListReader(series)
    for buckets, ok in ((58, False), (59, True)):
        windows = BucketedWindows(10, ListStore(series, layout), reader, series[-1], None, SCAN_MAX, buckets)
        if ok:
            assert windows.stats(spec, series[-1].at) is not None
        else:
            with pytest.raises(WindowScanBound):
                windows.stats(spec, series[-1].at)
    tight = BucketedWindows(10, ListStore(series, layout), reader, series[-1], None, 0, 59)
    with pytest.raises(WindowScanBound):
        tight.stats(spec, series[-1].at)


# --- freshness: T-E23-017, 077, 087 (pure part) -----------------------------------------------------------

NOW = utc(2026, 11, 14, 12)


def known(at: datetime, uncertainty_ms: int = 0) -> TimeFacts:
    return TimeFacts("known", at, uncertainty_ms)


@pytest.mark.trace("T-E23-017", "D3", "R42")
def test_freshness_comes_from_observation_time_and_a_late_reading_is_not_fresh() -> None:
    window = 300
    fresh = decide_freshness(
        now=NOW, stale_after_seconds=window, has_history=True, reference=known(NOW - timedelta(seconds=299))
    )
    assert fresh.state == "current" and fresh.clock_state == "trusted"
    late = decide_freshness(
        now=NOW, stale_after_seconds=window, has_history=True, reference=known(NOW - timedelta(seconds=301))
    )
    assert late.state == "stale" and late.stale_since == NOW - timedelta(seconds=1)
    edge = decide_freshness(
        now=NOW, stale_after_seconds=window, has_history=True, reference=known(NOW - timedelta(seconds=300))
    )
    assert edge.state == "current"  # stale means strictly greater than the duration
    assert edge.next_check_at == NOW
    assert (
        decide_freshness(now=NOW, stale_after_seconds=window, has_history=False, reference=None).state
        == "never_reported"
    )


@pytest.mark.trace("T-E23-017", "D3", "T-E23-087")
def test_unknown_future_regressed_and_uncertain_time_is_never_current() -> None:
    base = {"now": NOW, "stale_after_seconds": 300, "has_history": True}
    unknown = decide_freshness(reference=TimeFacts("unknown", None, None), **base)  # type: ignore[arg-type]
    assert (unknown.state, unknown.clock_state, unknown.stale_since) == ("stale", "unknown", None)
    future = decide_freshness(reference=TimeFacts("future", NOW + timedelta(hours=1), 10), **base)  # type: ignore[arg-type]
    assert (future.state, future.clock_state) == ("stale", "uncertain")
    regressed = decide_freshness(reference=known(NOW), regressed=True, **base)  # type: ignore[arg-type]
    assert regressed.state == "stale" and regressed.clock_state == "unknown"
    straddling = decide_freshness(reference=known(NOW - timedelta(seconds=1), 5000), **base)  # type: ignore[arg-type]
    assert straddling.state == "stale" and straddling.next_check_at == NOW + timedelta(seconds=4)
    assert decide_freshness(reference=None, **base).state == "stale"  # type: ignore[arg-type]
    wide = decide_freshness(reference=known(NOW - timedelta(seconds=290), 20000), **base)  # type: ignore[arg-type]
    assert wide.state == "stale"  # the whole uncertainty interval must fit the window


@pytest.mark.trace("T-E23-077", "D14", "T-E23-087")
def test_replay_freshness_only_advancing_observations_refresh_the_reference() -> None:
    first = known(NOW - timedelta(minutes=10))
    reference = advance_replay_reference(None, utc(2014, 10, 7, 15, 36), first)
    looped = advance_replay_reference(reference, utc(2014, 9, 20, 4), known(NOW))  # restarted from the top
    assert looped == reference
    duplicate = advance_replay_reference(reference, utc(2014, 10, 7, 15, 36), known(NOW))
    assert duplicate == reference
    advancing = advance_replay_reference(reference, utc(2014, 10, 7, 15, 41), known(NOW))
    assert advancing == ReplayReference(utc(2014, 10, 7, 15, 41), known(NOW))
    stopped = decide_freshness(
        now=NOW + timedelta(seconds=301),
        stale_after_seconds=300,
        has_history=True,
        reference=select_reference("REPLAY", None, advancing),
    )
    assert stopped.state == "stale"
    playing = decide_freshness(
        now=NOW + timedelta(seconds=299),
        stale_after_seconds=300,
        has_history=True,
        reference=select_reference("REPLAY", None, advancing),
    )
    assert playing.state == "current"
    assert (
        select_reference("REPLAY", known(NOW), None) is None
    )  # dataset observation time is never the reference
    assert select_reference("LIVE", known(NOW), advancing) == known(NOW)


@pytest.mark.trace("T-E23-017", "T-E23-053", "3.4")
def test_activation_quiet_watch_anchor_goes_stale_without_inventing_a_reading() -> None:
    anchor = known(NOW)
    watch = decide_freshness(
        now=NOW + timedelta(seconds=200),
        stale_after_seconds=300,
        has_history=True,
        reference=None,
        quiet_anchor=anchor,
    )
    assert watch.state == "current"
    quiet = decide_freshness(
        now=NOW + timedelta(seconds=301),
        stale_after_seconds=300,
        has_history=True,
        reference=None,
        quiet_anchor=anchor,
    )
    assert quiet.state == "stale" and quiet.stale_since == NOW + timedelta(seconds=300)


@pytest.mark.trace("T-E23-017", "T-E23-090", "3.1")
@given(a=st.one_of(st.none(), st.integers(0, 1000)), b=st.one_of(st.none(), st.integers(0, 1000)))
@EXAMPLES
def test_window_end_never_moves_backward(a: int | None, b: int | None) -> None:
    first = None if a is None else BASE + timedelta(seconds=a)
    second = None if b is None else BASE + timedelta(seconds=b)
    result = advance_window_end(first, second)
    assert result is None or (first is None or result >= first) and (second is None or result >= second)
    assert advance_window_end(first, None) == first


@pytest.mark.trace("T-E23-031", "T-E23-033", "C13")
@pytest.mark.parametrize(
    ("comparator", "below", "equal", "above"),
    [
        ("above", False, False, True),
        ("at_or_above", False, True, True),
        ("below", True, False, False),
        ("at_or_below", True, True, False),
    ],
)
def test_each_comparator_at_the_threshold(comparator: str, below: bool, equal: bool, above: bool) -> None:
    threshold = Decimal(3)
    assert crosses(comparator, Decimal("2.9999"), threshold) is below  # type: ignore[arg-type]
    assert crosses(comparator, threshold, threshold) is equal  # type: ignore[arg-type]
    assert crosses(comparator, Decimal("3.0001"), threshold) is above  # type: ignore[arg-type]


@pytest.mark.trace("T-E23-031", "C13")
def test_coverage_exactly_at_min_coverage_is_evaluated(config: SensorConfig) -> None:
    end = utc(2014, 10, 7, 15, 36)
    nine = [point(end - timedelta(minutes=5 * n), "3.9") for n in range(9)]  # 9 x 300 s / 3600 s = 0.75
    evaluation = evaluate_limits(evaluation_input(config, "co2", nine, interval=300))
    assert evaluation.status == "beyond_limit" and evaluation.coverage == Decimal("0.75")
    eight = evaluate_limits(evaluation_input(config, "co2", nine[:8], interval=300))
    assert eight.status == "insufficient_data"


@pytest.mark.trace("T-E23-080", "3.7")
def test_the_per_limit_value_is_the_variant_nearer_to_crossing(config: SensorConfig) -> None:
    at = utc(2026, 1, 1, 12)
    low_side = evaluate_limits(
        evaluation_input(config, "o2", [Instant(at, Decimal("130"), Decimal("140"))], interval=5)
    )
    assert {r.limit_id: r.value for r in low_side.results}["o2_low_watch"] == Decimal(
        "130"
    )  # "below": the lower variant
    high_side = evaluate_limits(
        evaluation_input(config, "noise", [Instant(at, Decimal("100"), Decimal("110"))], interval=None)
    )
    assert {r.limit_id: r.value for r in high_side.results}["noise_peak_emergency"] == Decimal(
        "110"
    )  # "above": the higher


@pytest.mark.trace("T-E23-022", "S5.4", "DE23-6")
def test_a_pressure_is_handed_to_the_converter_only_for_a_ppm_conversion(
    config: SensorConfig, release: object
) -> None:
    from crew_health.kernel.testing import MemoryWireConverter
    from crew_health.kernel.units import ConvertedQuantity

    seen: list[object] = []

    def record(value, indicator, rel, to, pressure):  # type: ignore[no-untyped-def]
        seen.append(pressure)
        return ConvertedQuantity(value, Quantity(value.value, to), "x", "m", pressure, "r")

    spy = MemoryWireConverter(record)
    convert_wire_reading(
        spy,
        release,
        sensor(config, "rad"),
        value=Decimal(1),
        unit="mGy/h",
        canonical_unit=Unit("uGy/h"),
        pressure=assumed_pressure(),
    )  # type: ignore[arg-type]
    convert_wire_reading(
        spy,
        release,
        sensor(config, "co2"),
        value=Decimal(1),
        unit="ppm",
        canonical_unit=MMHG,
        pressure=assumed_pressure(),
    )  # type: ignore[arg-type]
    assert seen[0] is None and seen[1] is not None
