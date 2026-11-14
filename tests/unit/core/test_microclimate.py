"""Unit tests of pharmacy.microclimate (spec 4): capacity, permeance, exact relaxation, the
internal RH trace for every moisture kind, repackaging continuity, and the unit step
response with its linearity property (offset d gives RH_in + d h within 1e-12)."""

import copy
import dataclasses
import math
from typing import Any

import pytest

from pharmacy import microclimate
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_DAY
from pharmacy.errors import ModelDomainError
from pharmacy.types import PackageDoc, Segment
from tests.unit.core import builders as b

DAY = SECONDS_PER_DAY


@pytest.fixture(scope="module")
def packages(bundle: ConfigBundle) -> dict[str, PackageDoc]:
    return {package["package_id"]: package for package in bundle.packages["packages"]}


def _value(doc: Any) -> float:
    return float(doc["value"])


def test_capacity_and_permeance_of_the_bottle(packages: dict[str, PackageDoc]) -> None:
    moisture = packages["original_bottle"]["moisture_model"]
    expected = _value(moisture["headspace_capacity_mg_per_percent_rh"]) + math.fsum(
        _value(c["dry_mass_g"]) * _value(c["sorption_slope_mg_per_g_per_percent_rh"])
        for c in moisture["contents"]
    )
    assert microclimate.moisture_capacity_mg_per_percent_rh(moisture) == pytest.approx(expected, rel=1e-15)
    t_test = _value(moisture["mvtr_test_temperature_k"])
    at_test = _value(moisture["mvtr_mg_per_day"]) / _value(moisture["mvtr_test_delta_rh_percent"]) / DAY
    assert microclimate.permeance_mg_per_s_per_percent_rh(moisture, t_test) == pytest.approx(
        at_test, rel=1e-15
    )
    e_perm = _value(moisture["permeation_activation_energy_j_per_mol"])
    ratio = microclimate.permeance_mg_per_s_per_percent_rh(moisture, 298.15) / at_test
    assert ratio == pytest.approx(
        math.exp(-e_perm / GAS_CONSTANT_J_PER_MOL_K * (1 / 298.15 - 1 / t_test)), rel=1e-13
    )
    tau = microclimate.exchange_time_constant_s(packages["original_bottle"], 298.15)
    assert tau == pytest.approx(
        expected / microclimate.permeance_mg_per_s_per_percent_rh(moisture, 298.15), rel=1e-15
    )
    # Warmer packs exchange faster.
    assert microclimate.exchange_time_constant_s(packages["original_bottle"], 310.0) < tau


def test_time_constants_of_memoryless_kinds(packages: dict[str, PackageDoc]) -> None:
    assert microclimate.exchange_time_constant_s(packages["original_vial"], 298.15) == 0.0
    assert math.isinf(microclimate.exchange_time_constant_s(packages["original_blister"], 298.15))


def _broken(package: PackageDoc, path: tuple[Any, ...], value: Any) -> Any:
    out: Any = copy.deepcopy(package["moisture_model"])
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("headspace_capacity_mg_per_percent_rh",), None),
        (("contents", 0, "dry_mass_g", "value"), math.nan),
        (("headspace_capacity_mg_per_percent_rh", "value"), -100.0),
    ],
)
def test_capacity_errors(path: tuple[Any, ...], value: Any, packages: dict[str, PackageDoc]) -> None:
    with pytest.raises(ModelDomainError):
        microclimate.moisture_capacity_mg_per_percent_rh(_broken(packages["original_bottle"], path, value))


@pytest.mark.parametrize(
    ("path", "value", "temperature"),
    [
        (("mvtr_mg_per_day",), None, 298.15),
        (("mvtr_mg_per_day", "value"), 0.0, 298.15),
        (("mvtr_test_delta_rh_percent", "value"), -1.0, 298.15),
        (("mvtr_mg_per_day", "value"), 1.3, 0.0),
        (("mvtr_mg_per_day", "value"), 1.3, math.inf),
    ],
)
def test_permeance_errors(
    path: tuple[Any, ...], value: Any, temperature: float, packages: dict[str, PackageDoc]
) -> None:
    with pytest.raises(ModelDomainError):
        microclimate.permeance_mg_per_s_per_percent_rh(
            _broken(packages["original_bottle"], path, value), temperature
        )


def test_wrong_kind_errors(packages: dict[str, PackageDoc]) -> None:
    blister = packages["original_blister"]["moisture_model"]
    with pytest.raises(ModelDomainError):
        microclimate.moisture_capacity_mg_per_percent_rh(blister)
    with pytest.raises(ModelDomainError):
        microclimate.permeance_mg_per_s_per_percent_rh(blister, 298.15)


def test_relax_rh() -> None:
    assert microclimate.relax_rh(30.0, 70.0, 100.0, 50.0) == pytest.approx(
        70.0 - 40.0 * math.exp(-2.0), rel=1e-15
    )
    assert microclimate.relax_rh(30.0, 70.0, 100.0, math.inf) == 30.0
    assert microclimate.relax_rh(30.0, 70.0, 100.0, 0.0) == 70.0
    assert microclimate.relax_rh(30.0, 70.0, 0.0, 50.0) == 30.0
    for args in (
        (math.nan, 70.0, 1.0, 1.0),
        (30.0, math.inf, 1.0, 1.0),
        (30.0, 70.0, -1.0, 1.0),
        (30.0, 70.0, 1.0, -1.0),
        (30.0, 70.0, 1.0, math.nan),
    ):
        with pytest.raises(ModelDomainError):
            microclimate.relax_rh(*args)


def _bag_days(
    values: list[float], *, zone: str = "station_kit", start: float = 0.0, package: str = "repackaged_bag"
) -> list[Segment]:
    return [
        b.segment(
            start + i * DAY, start + (i + 1) * DAY, zone=zone, package=package, rh=v, temperature=296.0 + i
        )
        for i, v in enumerate(values)
    ]


def test_bag_trace_relaxes_exactly_and_tracks_the_first_rh_out(packages: dict[str, PackageDoc]) -> None:
    segments = _bag_days([60.0, 30.0, 45.0])
    trace = microclimate.internal_rh_trace(segments, packages)
    assert trace.package_ids == ("repackaged_bag",) * 3
    assert trace.moisture_kind == ("first_order_exchange",) * 3
    start = 60.0  # no configured initial RH: the first segment starts at RH_out
    for index, segment in enumerate(segments):
        tau = microclimate.exchange_time_constant_s(packages["repackaged_bag"], segment.temperature_k)
        end = microclimate.relax_rh(start, segment.relative_humidity_percent, DAY, tau)
        assert trace.rh_in_start_high[index] == start
        assert trace.time_constant_s[index] == tau
        assert trace.rh_in_end_high[index] == pytest.approx(end, rel=1e-15)
        start = end
    assert trace.rh_in_start_low == trace.rh_in_start_high
    assert microclimate.initial_rh_of_first_package(segments, packages) is None


def test_gap_low_variant_and_initial_rh(packages: dict[str, PackageDoc]) -> None:
    segments = [
        b.segment(0.0, DAY, package="original_bottle", rh=50.0),
        b.segment(DAY, 2 * DAY, package="original_bottle", rh=70.0, rh_low=25.0, gaps=("relative_humidity",)),
    ]
    trace = microclimate.internal_rh_trace(segments, packages)
    initial = _value(packages["original_bottle"]["moisture_model"]["initial_internal_rh_percent"])
    assert trace.rh_in_start_high[0] == initial == trace.rh_in_start_low[0]
    assert trace.rh_in_end_high[1] > trace.rh_in_start_high[1] > trace.rh_in_end_low[1]
    overridden = microclimate.internal_rh_trace(segments, packages, {"original_bottle": 42.0})
    assert overridden.rh_in_start_high[0] == 42.0
    assert microclimate.initial_rh_of_first_package(segments, packages, {"repackaged_bag": 10.0}) == initial
    with pytest.raises(ModelDomainError):
        microclimate.internal_rh_trace(segments, packages, {"original_bottle": math.nan})


def test_repackaging_carries_the_moisture_across(packages: dict[str, PackageDoc]) -> None:
    segments = [*_bag_days([20.0], package="original_bottle"), *_bag_days([80.0, 80.0], start=DAY)]
    trace = microclimate.internal_rh_trace(segments, packages)
    assert trace.rh_in_start_high[1] == trace.rh_in_end_high[0]
    assert trace.rh_in_start_high[1] < 40.0  # the bottle kept it dry; the bag starts from there


def test_sealed_and_none_kinds(packages: dict[str, PackageDoc]) -> None:
    segments = [
        b.segment(0.0, DAY, package="original_blister", rh=80.0),
        b.segment(DAY, 2 * DAY, package="original_vial", rh=60.0, rh_low=20.0, gaps=("relative_humidity",)),
        b.segment(2 * DAY, 3 * DAY, package="repackaged_bag", rh=40.0),
    ]
    trace = microclimate.internal_rh_trace(segments, packages)
    sealed = _value(packages["original_blister"]["moisture_model"]["sealed_rh_percent"])
    assert trace.rh_in_start_high[0] == trace.rh_in_end_high[0] == sealed
    assert (trace.rh_in_start_high[1], trace.rh_in_start_low[1]) == (60.0, 20.0)
    assert (trace.rh_in_end_high[1], trace.rh_in_end_low[1]) == (60.0, 20.0)
    assert (trace.rh_in_start_high[2], trace.rh_in_start_low[2]) == (60.0, 20.0)
    assert trace.moisture_kind == ("sealed_constant", "none", "first_order_exchange")


def test_trace_from_explicit_starts(packages: dict[str, PackageDoc]) -> None:
    segments = _bag_days([50.0, 50.0])
    trace = microclimate.trace_from(segments, packages, 30.0, 20.0)
    assert (trace.rh_in_start_high[0], trace.rh_in_start_low[0]) == (30.0, 20.0)
    assert microclimate.trace_from((), packages, None, None).package_ids == ()


def test_trace_errors(packages: dict[str, PackageDoc]) -> None:
    with pytest.raises(ModelDomainError):
        microclimate.internal_rh_trace([b.segment(0.0, DAY, package=None)], packages)
    with pytest.raises(ModelDomainError):
        microclimate.internal_rh_trace([b.segment(0.0, DAY, package="crate")], packages)
    broken: Any = copy.deepcopy(packages)
    broken["original_blister"]["moisture_model"]["sealed_rh_percent"] = None
    with pytest.raises(ModelDomainError):
        microclimate.internal_rh_trace([b.segment(0.0, DAY)], broken)
    assert microclimate.initial_rh_of_first_package((), packages) is None


# =========================================================================================
# Unit step response (spec 4.4)
# =========================================================================================


def _steps_and_taus(segments: list[Segment], packages: dict[str, PackageDoc]) -> tuple[float, ...]:
    return microclimate.internal_rh_trace(segments, packages).time_constant_s


def test_step_response_rules(packages: dict[str, PackageDoc]) -> None:
    segments = [
        *_bag_days([50.0, 50.0]),
        b.segment(2 * DAY, 3 * DAY, zone="transit_kit", package="repackaged_bag"),
        b.segment(3 * DAY, 4 * DAY, package="repackaged_bag", gaps=("relative_humidity",)),
        b.segment(4 * DAY, 5 * DAY, package="repackaged_bag"),
        b.segment(5 * DAY, 6 * DAY, package="original_vial"),
        b.segment(6 * DAY, 7 * DAY, package="original_blister"),
        b.segment(7 * DAY, 8 * DAY, zone="transit_kit", package="original_vial"),
    ]
    taus = _steps_and_taus(segments, packages)
    h = microclimate.unit_step_response(segments, taus, "station_kit", first_start_tracks_outside=True)
    assert h[0] == 1.0
    decay = [math.exp(-DAY / tau) if 0.0 < tau < math.inf else 0.0 for tau in taus]
    assert h[1] == pytest.approx(1.0, abs=1e-15)  # starts at 1 with forcing 1 stays at 1
    assert h[2] == pytest.approx(1.0, abs=1e-15)
    assert h[3] == pytest.approx(decay[2], rel=1e-14)  # other zone: no forcing, decays
    assert h[4] == pytest.approx(decay[2] * decay[3], rel=1e-14)  # RH gap: no offset either
    assert h[5:] == (1.0, 0.0, 0.0)  # none follows the forcing, sealed ignores it
    cold = microclimate.unit_step_response(segments, taus, "station_kit", first_start_tracks_outside=False)
    assert cold[0] == 0.0
    assert cold[1] == pytest.approx(-math.expm1(-DAY / taus[0]), rel=1e-14)
    other = microclimate.unit_step_response(segments, taus, "transit_kit", first_start_tracks_outside=True)
    assert other[0] == 0.0
    assert other[-1] == 1.0
    end = microclimate.step_response_end(segments[:3], taus[:3], "station_kit", h[:3])
    assert end == pytest.approx(h[3], rel=1e-15)
    assert microclimate.step_response_end(segments[:6], taus[:6], "station_kit", h[:6]) == 1.0
    assert microclimate.step_response_end(segments[:7], taus[:7], "station_kit", h[:7]) == 0.0


def test_continuing_the_step_response_equals_the_whole(packages: dict[str, PackageDoc]) -> None:
    segments = _bag_days([50.0, 60.0, 30.0, 40.0, 70.0])
    segments[2] = dataclasses.replace(segments[2], zone_id="transit_kit")
    taus = _steps_and_taus(segments, packages)
    whole = microclimate.unit_step_response(segments, taus, "station_kit", first_start_tracks_outside=False)
    head = microclimate.unit_step_response(
        segments[:2], taus[:2], "station_kit", first_start_tracks_outside=False
    )
    h_end = microclimate.step_response_end(segments[:2], taus[:2], "station_kit", head)
    tail = microclimate.continue_step_response(segments[2:], taus[2:], "station_kit", h_end)
    assert head + tail == pytest.approx(whole, rel=1e-15, abs=0.0)


def test_step_response_errors(packages: dict[str, PackageDoc]) -> None:
    segments = _bag_days([50.0, 60.0])
    with pytest.raises(ModelDomainError):
        microclimate.unit_step_response(segments, (1.0,), "station_kit", first_start_tracks_outside=True)
    with pytest.raises(ModelDomainError):
        microclimate.continue_step_response(segments, (1.0,), "station_kit", 0.0)
    with pytest.raises(ModelDomainError):
        microclimate.unit_step_response(segments, (1.0, -1.0), "station_kit", first_start_tracks_outside=True)
    with pytest.raises(ModelDomainError):
        microclimate.step_response_end((), (), "station_kit", ())
    assert microclimate.unit_step_response((), (), "station_kit", first_start_tracks_outside=True) == ()


@pytest.mark.parametrize("tracks", [True, False])
@pytest.mark.parametrize("offset", [-7.5, 0.3, 12.0])
def test_offset_linearity(tracks: bool, offset: float, packages: dict[str, PackageDoc]) -> None:
    """Adding d to RH_out in the zone's non-gap segments moves RH_in by exactly d h."""
    segments = [
        *_bag_days([55.0, 30.0], package="original_bottle"),
        *_bag_days([65.0], start=2 * DAY),
        b.segment(3 * DAY, 4 * DAY, zone="transit_kit", package="repackaged_bag", rh=35.0),
        b.segment(
            4 * DAY, 4.5 * DAY, package="repackaged_bag", rh=70.0, rh_low=25.0, gaps=("relative_humidity",)
        ),
        b.segment(4.5 * DAY, 5 * DAY, package="original_vial", rh=48.0),
        b.segment(5 * DAY, 6 * DAY, package="repackaged_bag", rh=52.0),
    ]
    shifted_segments = [
        dataclasses.replace(
            s,
            relative_humidity_percent=s.relative_humidity_percent + offset,
            relative_humidity_low_percent=s.relative_humidity_low_percent + offset,
        )
        if s.zone_id == "station_kit" and "relative_humidity" not in s.gap_quantities
        else s
        for s in segments
    ]
    if tracks:  # the first exchange starts at RH_out, offset included
        base = microclimate.trace_from(segments, packages, None, None)
        shifted = microclimate.trace_from(shifted_segments, packages, None, None)
    else:  # the first exchange starts at a configured value the offset does not touch
        base = microclimate.internal_rh_trace(segments, packages, {"original_bottle": 35.0})
        shifted = microclimate.internal_rh_trace(shifted_segments, packages, {"original_bottle": 35.0})
    h = microclimate.unit_step_response(
        segments, base.time_constant_s, "station_kit", first_start_tracks_outside=tracks
    )
    for index in range(len(segments)):
        if base.moisture_kind[index] == "sealed_constant":
            continue
        assert shifted.rh_in_start_high[index] == pytest.approx(
            base.rh_in_start_high[index] + offset * h[index], abs=1e-12
        )
