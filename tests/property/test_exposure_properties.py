"""Property tests of exposure and microclimate (spec 3.10, 4.6, 15.3): MKT of a constant
series (TV-MKT-4), MKT >= arithmetic mean (TV-MKT-5), equal intervals give the USP form
(TV-MKT-6), segment durations tile the window, coverage fractions in [0, 1], RH_in within
the envelope of its start and forcing, h in [0, 1], and offset linearity."""

import dataclasses
import itertools
import math

from hypothesis import given
from hypothesis import strategies as st

from pharmacy import exposure, microclimate
from pharmacy.config import load_bundle
from pharmacy.types import Quantity, Segment, SensorPoint, SensorSeries, seconds_to_iso
from tests.unit.core import builders as b

BUNDLE = load_bundle()
ZONES = {zone["zone_id"]: zone for zone in BUNDLE.zones["zones"]}
PACKAGES = {package["package_id"]: package for package in BUNDLE.packages["packages"]}
EA = 83144.0

temperatures = st.floats(min_value=200.0, max_value=350.0)
durations = st.floats(min_value=1.0, max_value=1e7)


@given(temperatures, st.lists(durations, min_size=1, max_size=20))
def test_tv_mkt_4_constant_series(temperature: float, spans: list[float]) -> None:
    mkt = exposure.mean_kinetic_temperature([temperature] * len(spans), spans, EA)
    assert math.isclose(mkt, temperature, rel_tol=1e-12)


@given(st.lists(st.tuples(temperatures, durations), min_size=1, max_size=20))
def test_tv_mkt_5_not_below_the_mean(series: list[tuple[float, float]]) -> None:
    temps = [t for t, _ in series]
    spans = [d for _, d in series]
    mean = math.fsum(t * d for t, d in series) / math.fsum(spans)
    assert exposure.mean_kinetic_temperature(temps, spans, EA) >= mean * (1.0 - 1e-12)


@given(st.lists(temperatures, min_size=1, max_size=20), durations)
def test_tv_mkt_6_equal_intervals_reduce_to_the_usp_form(temps: list[float], span: float) -> None:
    ratio = EA / 8.31446261815324
    usp = ratio / -math.log(math.fsum(math.exp(-ratio / t) for t in temps) / len(temps))
    assert math.isclose(exposure.mean_kinetic_temperature(temps, [span] * len(temps), EA), usp, rel_tol=1e-12)


T0 = 1830902400.0  # 2028-01-10T00:00:00Z


@given(
    st.lists(
        st.tuples(st.floats(min_value=1.0, max_value=7200.0), st.floats(min_value=280.0, max_value=310.0)),
        min_size=0,
        max_size=25,
    ),
    st.floats(min_value=600.0, max_value=86400.0),
    st.floats(min_value=-3600.0, max_value=3600.0),
)
def test_segments_tile_the_window(steps: list[tuple[float, float]], span: float, lead: float) -> None:
    points = []
    clock = T0 + lead
    for gap, value in steps:
        clock = round(clock + gap, 6)  # whole microseconds, so the ISO stamp and t_s agree
        points.append(SensorPoint(seconds_to_iso(clock), clock, value))
    series = SensorSeries("s-t", "stk-t1", "station_kit", "temperature_k", "K", tuple(points))
    end = T0 + span
    window = end - T0  # the representable window length (T0 + span rounds at this epoch)
    history = exposure.build_zone_history(ZONES["station_kit"], [series], T0, end)
    segments = history.segments
    assert segments[0].t0_s == T0
    assert segments[-1].t1_s == end
    assert all(a.t1_s == c.t0_s for a, c in itertools.pairwise(segments))
    assert all(s.t1_s > s.t0_s for s in segments)
    assert math.isclose(math.fsum(s.t1_s - s.t0_s for s in segments), window, rel_tol=1e-12)
    for entry in history.coverage:
        assert 0.0 <= entry.coverage_fraction <= 1.0
        total = entry.measured_s + entry.held_s + entry.gap_s + entry.declared_s + entry.not_applicable_s
        assert math.isclose(total, window, rel_tol=1e-12)


rh_steps = st.lists(
    st.tuples(
        st.floats(min_value=0.0, max_value=100.0),
        st.floats(min_value=60.0, max_value=5e6),
        st.floats(min_value=270.0, max_value=320.0),
        st.sampled_from(["station_kit", "transit_kit"]),
        st.booleans(),
    ),
    min_size=1,
    max_size=15,
)
package_ids = st.sampled_from(["repackaged_bag", "original_bottle", "original_vial", "original_blister"])


def _segments(steps: list[tuple[float, float, float, str, bool]], package: str) -> list[Segment]:
    out: list[Segment] = []
    clock = 0.0
    for rh, span, temperature, zone, gap in steps:
        gaps: tuple[Quantity, ...] = ("relative_humidity",) if gap else ()
        out.append(
            b.segment(
                clock, clock + span, zone=zone, package=package, temperature=temperature, rh=rh, gaps=gaps
            )
        )
        clock += span
    return out


@given(rh_steps, package_ids, st.floats(min_value=0.0, max_value=100.0))
def test_rh_in_stays_in_its_envelope(
    steps: list[tuple[float, float, float, str, bool]], package: str, start: float
) -> None:
    segments = _segments(steps, package)
    trace = microclimate.trace_from(segments, PACKAGES, start, start)
    forcing = [s[0] for s in steps]
    sealed = PACKAGES["original_blister"]["moisture_model"]["sealed_rh_percent"]
    extra = [] if sealed is None else [float(sealed["value"])]
    lo = min([start, *forcing, *extra]) - 1e-9
    hi = max([start, *forcing, *extra]) + 1e-9
    for values in (trace.rh_in_start_high, trace.rh_in_end_high, trace.rh_in_start_low, trace.rh_in_end_low):
        assert all(lo <= v <= hi for v in values)


@given(rh_steps, package_ids, st.booleans(), st.floats(min_value=-15.0, max_value=15.0))
def test_step_response_bounds_and_linearity(
    steps: list[tuple[float, float, float, str, bool]], package: str, tracks: bool, offset: float
) -> None:
    segments = _segments(steps, package)
    start = None if tracks else 40.0
    base = microclimate.trace_from(segments, PACKAGES, start, start)
    h = microclimate.unit_step_response(
        segments, base.time_constant_s, "station_kit", first_start_tracks_outside=tracks
    )
    assert all(0.0 <= v <= 1.0 for v in h)
    shifted = [
        dataclasses.replace(
            s,
            relative_humidity_percent=s.relative_humidity_percent + offset,
            relative_humidity_low_percent=s.relative_humidity_low_percent + offset,
        )
        if s.zone_id == "station_kit" and "relative_humidity" not in s.gap_quantities
        else s
        for s in segments
    ]
    moved = microclimate.trace_from(shifted, PACKAGES, start, start)
    for index in range(len(segments)):
        if base.moisture_kind[index] == "sealed_constant":
            continue
        assert math.isclose(
            moved.rh_in_start_high[index], base.rh_in_start_high[index] + offset * h[index], abs_tol=1e-12
        )
