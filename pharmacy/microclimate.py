"""Package microclimate: internal relative humidity of a package over a lot exposure.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 4.

Model kinds (packages.json ``moisture_model.kind``):

* ``none``: the product sees the zone RH (RH_in = RH_out).
* ``sealed_constant``: high-barrier packs; RH_in = ``sealed_rh_percent`` whatever RH_out.
* ``first_order_exchange``: dRH_in/dt = (RH_out - RH_in) / tau_m(T) with
  tau_m = C_eff / P(T), exact per piecewise-constant segment (spec 4.2).
"""

import dataclasses
import math
from collections.abc import Mapping, Sequence

from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_DAY
from pharmacy.errors import ModelDomainError
from pharmacy.types import (
    MicroclimateTrace,
    MoistureModelDoc,
    MoistureModelKind,
    PackageDoc,
    Segment,
    SourcedValue,
)


def _require(value: float | None, name: str) -> float:
    if value is None:
        raise ModelDomainError(f"first_order_exchange needs {name}")
    if not math.isfinite(value):
        raise ModelDomainError(f"{name} must be finite, got {value!r}")
    return float(value)


def moisture_capacity_mg_per_percent_rh(moisture: MoistureModelDoc) -> float:
    """C_eff = sum_j m_j s_j + headspace capacity, in mg of water per %RH (spec 4.2):
    m_j dry mass of component j (g), s_j its sorption slope (mg/g per %RH). Raises
    ``ModelDomainError`` if the model is not first_order_exchange or C_eff <= 0."""
    if moisture["kind"] != "first_order_exchange":
        raise ModelDomainError(
            f"moisture capacity is defined for first_order_exchange, not {moisture['kind']!r}"
        )
    headspace = moisture["headspace_capacity_mg_per_percent_rh"]
    total = _require(
        headspace["value"] if headspace is not None else None, "headspace_capacity_mg_per_percent_rh"
    )
    for component in moisture["contents"]:
        mass = _require(component["dry_mass_g"]["value"], "dry_mass_g")
        slope = _require(
            component["sorption_slope_mg_per_g_per_percent_rh"]["value"],
            "sorption_slope_mg_per_g_per_percent_rh",
        )
        total += mass * slope
    if not total > 0.0:
        raise ModelDomainError(f"moisture capacity C_eff must be positive, got {total!r}")
    return total


def permeance_mg_per_s_per_percent_rh(
    moisture: MoistureModelDoc,
    temperature_k: float,
    gas_constant_j_per_mol_k: float = GAS_CONSTANT_J_PER_MOL_K,
) -> float:
    """P(T) = (MVTR_test / dRH_test) / 86400 x exp(-(E_perm/R)(1/T - 1/T_test)) in mg/(s %RH)
    (spec 4.2). MVTR_test in mg/day at T_test and driving difference dRH_test."""
    if moisture["kind"] != "first_order_exchange":
        raise ModelDomainError(f"permeance is defined for first_order_exchange, not {moisture['kind']!r}")

    def field(name: str, doc: SourcedValue | None) -> float:
        return _require(None if doc is None else doc["value"], name)

    mvtr = field("mvtr_mg_per_day", moisture["mvtr_mg_per_day"])
    t_test = field("mvtr_test_temperature_k", moisture["mvtr_test_temperature_k"])
    delta_rh = field("mvtr_test_delta_rh_percent", moisture["mvtr_test_delta_rh_percent"])
    e_perm = field(
        "permeation_activation_energy_j_per_mol", moisture["permeation_activation_energy_j_per_mol"]
    )
    if mvtr <= 0.0 or t_test <= 0.0 or delta_rh <= 0.0:
        raise ModelDomainError("MVTR, its test temperature and its test RH difference must be positive")
    if not math.isfinite(temperature_k) or temperature_k <= 0.0:
        raise ModelDomainError(f"temperature must be positive and finite, got {temperature_k!r}")
    # 1/T - 1/T_test written as (T_test - T)/(T T_test) so T close to T_test does not cancel.
    inverse_gap = (t_test - temperature_k) / (temperature_k * t_test)
    exponent = -(e_perm / gas_constant_j_per_mol_k) * inverse_gap
    return (mvtr / delta_rh) / SECONDS_PER_DAY * math.exp(exponent)


def exchange_time_constant_s(package: PackageDoc, temperature_k: float) -> float:
    """tau_m = C_eff / P(T) in s for first_order_exchange; ``math.inf`` for
    sealed_constant; 0.0 for none."""
    moisture = package["moisture_model"]
    kind = moisture["kind"]
    if kind == "none":
        return 0.0
    if kind == "sealed_constant":
        return math.inf
    capacity = moisture_capacity_mg_per_percent_rh(moisture)
    return capacity / permeance_mg_per_s_per_percent_rh(moisture, temperature_k)


def _relax(start: float, outside: float, duration_s: float, tau_m_s: float) -> float:
    if math.isinf(tau_m_s):
        return start
    if tau_m_s == 0.0:
        return outside
    return outside + (start - outside) * math.exp(-duration_s / tau_m_s)


def relax_rh(rh_in_start_percent: float, rh_out_percent: float, duration_s: float, tau_m_s: float) -> float:
    """Exact end-of-segment internal RH: RH_out + (RH_in_start - RH_out) exp(-duration/tau_m).
    tau_m = inf returns RH_in_start; tau_m = 0 returns RH_out."""
    for name, value in (("rh_in_start_percent", rh_in_start_percent), ("rh_out_percent", rh_out_percent)):
        if not math.isfinite(value):
            raise ModelDomainError(f"{name} must be finite, got {value!r}")
    if not math.isfinite(duration_s) or duration_s < 0.0:
        raise ModelDomainError(f"duration must be finite and non-negative, got {duration_s!r}")
    if math.isnan(tau_m_s) or tau_m_s < 0.0:
        raise ModelDomainError(f"tau_m must be >= 0 (inf for sealed), got {tau_m_s!r}")
    return _relax(rh_in_start_percent, rh_out_percent, duration_s, tau_m_s)


def _step_forward(
    segments: Sequence[Segment], time_constants_s: Sequence[float], zone_id: str, h_start: float
) -> tuple[float, ...]:
    """h_z at each segment start, starting the first segment from ``h_start`` when it
    exchanges (the rules of spec 4.4; memoryless kinds ignore ``h_start``)."""
    out: list[float] = []
    previous_end = h_start
    for index, (segment, tau_m) in enumerate(zip(segments, time_constants_s, strict=True)):
        if math.isnan(tau_m) or tau_m < 0.0:
            raise ModelDomainError(f"tau_m must be >= 0 (inf for sealed), got {tau_m!r}")
        forcing = (
            1.0 if segment.zone_id == zone_id and "relative_humidity" not in segment.gap_quantities else 0.0
        )
        if tau_m == 0.0:
            start = end = forcing
        elif math.isinf(tau_m):
            start = end = 0.0
        else:
            start = previous_end if index > 0 else h_start
            decay = math.exp(-(segment.t1_s - segment.t0_s) / tau_m)
            end = start * decay + forcing * -math.expm1(-(segment.t1_s - segment.t0_s) / tau_m)
        out.append(start)
        previous_end = end
    return tuple(out)


def unit_step_response(
    segments: Sequence[Segment],
    time_constants_s: Sequence[float],
    zone_id: str,
    *,
    first_start_tracks_outside: bool,
) -> tuple[float, ...]:
    """h_z at each segment start: the response of RH_in to a unit offset added to RH_out in
    the segments of zone ``zone_id`` whose RH is not gap-filled (spec 4.4). The forcing is
    e = 1 in those segments and 0 elsewhere. tau_m = 0 (kind none) gives h = e; tau_m = inf
    (sealed_constant) gives h = 0; otherwise h_start is the previous segment's h_end (the
    first segment starts at e when ``first_start_tracks_outside``, that is when the first
    package interval has no configured initial RH, else at 0) and
    h_end = h_start exp(-tau/tau_m) + e (1 - exp(-tau/tau_m)). Per-draw offsets d_z then give
    RH_in,start(draw) = RH_in,start(nominal) + sum over zones of d_z h_z. Same length as
    ``segments``."""
    if len(segments) != len(time_constants_s):
        raise ModelDomainError("segments and time constants must have the same length")
    if not segments:
        return ()
    first = segments[0]
    first_forcing = (
        1.0 if first.zone_id == zone_id and "relative_humidity" not in first.gap_quantities else 0.0
    )
    h_start = first_forcing if first_start_tracks_outside else 0.0
    return _step_forward(segments, time_constants_s, zone_id, h_start)


def step_response_end(
    segments: Sequence[Segment], time_constants_s: Sequence[float], zone_id: str, h_starts: Sequence[float]
) -> float:
    """h_z at the end of the last segment, from the starts that ``unit_step_response``
    returned (used to continue the response into a forecast projection)."""
    if not segments:
        raise ModelDomainError("no segments")
    segment = segments[-1]
    tau_m = time_constants_s[-1]
    forcing = 1.0 if segment.zone_id == zone_id and "relative_humidity" not in segment.gap_quantities else 0.0
    if tau_m == 0.0:
        return forcing
    if math.isinf(tau_m):
        return 0.0
    duration = segment.t1_s - segment.t0_s
    return h_starts[-1] * math.exp(-duration / tau_m) + forcing * -math.expm1(-duration / tau_m)


def continue_step_response(
    segments: Sequence[Segment], time_constants_s: Sequence[float], zone_id: str, h_start: float
) -> tuple[float, ...]:
    """``unit_step_response`` over segments that continue a history whose response at the
    first segment's start is ``h_start`` (forecast projection, spec 11.4)."""
    if len(segments) != len(time_constants_s):
        raise ModelDomainError("segments and time constants must have the same length")
    return _step_forward(segments, time_constants_s, zone_id, h_start)


@dataclasses.dataclass(frozen=True, slots=True)
class _Moisture:
    kind: MoistureModelKind
    sealed_rh_percent: float


def _moisture_of(package_id: str | None, packages: Mapping[str, PackageDoc]) -> tuple[PackageDoc, _Moisture]:
    if package_id is None:
        raise ModelDomainError("a lot segment has no package")
    if package_id not in packages:
        raise ModelDomainError(f"unknown package {package_id!r}")
    package = packages[package_id]
    moisture = package["moisture_model"]
    sealed = 0.0
    if moisture["kind"] == "sealed_constant":
        if moisture["sealed_rh_percent"] is None:
            raise ModelDomainError(f"package {package_id!r} is sealed_constant without sealed_rh_percent")
        sealed = float(moisture["sealed_rh_percent"]["value"])
    return package, _Moisture(moisture["kind"], sealed)


def trace_from(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    start_high: float | None,
    start_low: float | None,
) -> MicroclimateTrace:
    """The trace of spec 4.3 over ``segments`` with the first segment's exchange starting
    at (``start_high``, ``start_low``); None for both starts at the first segment's RH_out
    (high and low variants). ``internal_rh_trace`` and the forecast projection use it."""
    package_ids: list[str] = []
    start_hi: list[float] = []
    start_lo: list[float] = []
    end_hi: list[float] = []
    end_lo: list[float] = []
    taus: list[float] = []
    kinds: list[MoistureModelKind] = []
    carry_hi = start_high
    carry_lo = start_low
    for segment in segments:
        package, moisture = _moisture_of(segment.package_id, packages)
        out_hi = segment.relative_humidity_percent
        out_lo = segment.relative_humidity_low_percent
        tau_m = exchange_time_constant_s(package, segment.temperature_k)
        duration = segment.t1_s - segment.t0_s
        if moisture.kind == "none":
            s_hi, s_lo, e_hi, e_lo = out_hi, out_lo, out_hi, out_lo
        elif moisture.kind == "sealed_constant":
            s_hi = s_lo = e_hi = e_lo = moisture.sealed_rh_percent
        else:
            s_hi = out_hi if carry_hi is None else carry_hi
            s_lo = out_lo if carry_lo is None else carry_lo
            e_hi = _relax(s_hi, out_hi, duration, tau_m)
            e_lo = _relax(s_lo, out_lo, duration, tau_m)
        package_ids.append(package["package_id"])
        start_hi.append(s_hi)
        start_lo.append(s_lo)
        end_hi.append(e_hi)
        end_lo.append(e_lo)
        taus.append(tau_m)
        kinds.append(moisture.kind)
        carry_hi = e_hi
        carry_lo = e_lo
    return MicroclimateTrace(
        package_ids=tuple(package_ids),
        rh_in_start_high=tuple(start_hi),
        rh_in_start_low=tuple(start_lo),
        rh_in_end_high=tuple(end_hi),
        rh_in_end_low=tuple(end_lo),
        time_constant_s=tuple(taus),
        moisture_kind=tuple(kinds),
    )


def initial_rh_of_first_package(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    initial_rh_percent: Mapping[str, float] | None = None,
) -> float | None:
    """The configured start of the lot's first package interval (the override, else the
    package's ``initial_internal_rh_percent``), or None when it tracks RH_out."""
    if not segments:
        return None
    package, _ = _moisture_of(segments[0].package_id, packages)
    package_id = package["package_id"]
    if initial_rh_percent is not None and package_id in initial_rh_percent:
        value = float(initial_rh_percent[package_id])
        if not math.isfinite(value):
            raise ModelDomainError(f"initial internal RH must be finite, got {value!r}")
        return value
    configured = package["moisture_model"]["initial_internal_rh_percent"]
    return None if configured is None else float(configured["value"])


def internal_rh_trace(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    initial_rh_percent: Mapping[str, float] | None = None,
) -> MicroclimateTrace:
    """Internal RH at the start and end of every lot segment for the high and low forcing
    variants (spec 4.3). The package of each segment is ``segment.package_id``. The first
    package interval starts at the package's ``initial_internal_rh_percent`` (null: the first
    segment's RH_out). Every later package change is a repackaging of already exposed
    product, so RH_in is continuous across it (the product carries its moisture).
    ``initial_rh_percent`` overrides the start value of the first interval per package id.
    tau_m is evaluated at each segment's nominal temperature.

    Raises ``ModelDomainError`` if a segment has no package or the package model is
    incomplete for its kind.
    """
    initial = initial_rh_of_first_package(segments, packages, initial_rh_percent)
    return trace_from(segments, packages, initial, initial)
