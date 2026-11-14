"""Oracle for the package microclimate (spec 4) at 50 significant digits."""

import dataclasses
from collections.abc import Mapping, Sequence

from pharmacy.types import MoistureModelDoc, MoistureModelKind, PackageDoc, Segment
from tests.oracle.precision import MP, R_MP, SECONDS_PER_DAY, Mpf


def moisture_capacity(moisture: MoistureModelDoc) -> Mpf:
    """C_eff = sum_j m_j s_j + headspace capacity, mg per %RH (spec 4.2)."""
    if moisture["kind"] != "first_order_exchange":
        raise ValueError("C_eff is defined for first_order_exchange only")
    headspace = moisture["headspace_capacity_mg_per_percent_rh"]
    total = MP.mpf(0) if headspace is None else MP.mpf(headspace["value"])
    for component in moisture["contents"]:
        total += MP.mpf(component["dry_mass_g"]["value"]) * MP.mpf(
            component["sorption_slope_mg_per_g_per_percent_rh"]["value"]
        )
    if total <= 0:
        raise ValueError("C_eff must be positive")
    return total


def permeance(moisture: MoistureModelDoc, temperature_k: float | Mpf, gas_constant: Mpf = R_MP) -> Mpf:
    """P(T) = (MVTR_test / dRH_test) / 86400 x exp(-(E_perm/R)(1/T - 1/T_test)) (spec 4.2)."""
    fields = (
        moisture["mvtr_mg_per_day"],
        moisture["mvtr_test_delta_rh_percent"],
        moisture["mvtr_test_temperature_k"],
        moisture["permeation_activation_energy_j_per_mol"],
    )
    if any(f is None for f in fields):
        raise ValueError("first_order_exchange needs every MVTR field")
    mvtr, d_rh, t_test, e_perm = (MP.mpf(f["value"]) for f in fields if f is not None)
    t = MP.mpf(temperature_k)
    return mvtr / d_rh / SECONDS_PER_DAY * MP.exp(-(e_perm / MP.mpf(gas_constant)) * (1 / t - 1 / t_test))


def exchange_time_constant(package: PackageDoc, temperature_k: float | Mpf) -> Mpf:
    """tau_m = C_eff / P(T); inf for sealed_constant; 0 for none (spec 4.2, 4.3)."""
    kind = package["moisture_model"]["kind"]
    if kind == "none":
        return MP.mpf(0)
    if kind == "sealed_constant":
        return MP.inf
    moisture = package["moisture_model"]
    return moisture_capacity(moisture) / permeance(moisture, temperature_k)


def relax_rh(
    rh_in_start: float | Mpf, rh_out: float | Mpf, duration_s: float | Mpf, tau_m_s: float | Mpf
) -> Mpf:
    """RH_out + (RH_in,start - RH_out) e^(-D/tau_m); tau_m = inf keeps the start, 0 gives RH_out."""
    start, out, d, tau = MP.mpf(rh_in_start), MP.mpf(rh_out), MP.mpf(duration_s), MP.mpf(tau_m_s)
    if MP.isinf(tau):
        return start
    if tau == 0:
        return out
    return out + (start - out) * MP.exp(-d / tau)


@dataclasses.dataclass(frozen=True, slots=True)
class OracleTrace:
    """MicroclimateTrace at oracle precision (same members)."""

    package_ids: tuple[str, ...]
    rh_in_start_high: tuple[Mpf, ...]
    rh_in_start_low: tuple[Mpf, ...]
    rh_in_end_high: tuple[Mpf, ...]
    rh_in_end_low: tuple[Mpf, ...]
    time_constant_s: tuple[Mpf, ...]
    moisture_kind: tuple[MoistureModelKind, ...]


def internal_rh_trace(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    initial_rh_percent: Mapping[str, float] | None = None,
) -> OracleTrace:
    """Spec 4.3: walk the segments in time order carrying RH_in for the high and low
    forcing variants. none and sealed_constant are memoryless; first_order_exchange starts
    from the previous segment's end value (continuous across repackaging); the first
    segment starts at the package's initial_internal_rh_percent (null: its RH_out), unless
    ``initial_rh_percent`` overrides it for that package."""
    ordered = sorted(segments, key=lambda s: s.t0_s)
    ids: list[str] = []
    s_hi: list[Mpf] = []
    s_lo: list[Mpf] = []
    e_hi: list[Mpf] = []
    e_lo: list[Mpf] = []
    taus: list[Mpf] = []
    kinds: list[MoistureModelKind] = []
    prev_hi: Mpf | None = None
    prev_lo: Mpf | None = None
    for index, seg in enumerate(ordered):
        if seg.package_id is None or seg.package_id not in packages:
            raise ValueError(f"segment at {seg.t0_s} has no known package")
        package = packages[seg.package_id]
        kind = package["moisture_model"]["kind"]
        out_hi, out_lo = MP.mpf(seg.relative_humidity_percent), MP.mpf(seg.relative_humidity_low_percent)
        tau = exchange_time_constant(package, seg.temperature_k)
        if kind == "none":
            start_hi, start_lo = out_hi, out_lo
        elif kind == "sealed_constant":
            sealed = package["moisture_model"]["sealed_rh_percent"]
            if sealed is None:
                raise ValueError("sealed_constant needs sealed_rh_percent")
            start_hi = start_lo = MP.mpf(sealed["value"])
        elif index == 0:
            override = None if initial_rh_percent is None else initial_rh_percent.get(seg.package_id)
            configured = package["moisture_model"]["initial_internal_rh_percent"]
            if override is not None:
                start_hi = start_lo = MP.mpf(override)
            elif configured is not None:
                start_hi = start_lo = MP.mpf(configured["value"])
            else:
                start_hi, start_lo = out_hi, out_lo
        else:
            assert prev_hi is not None
            assert prev_lo is not None
            start_hi, start_lo = prev_hi, prev_lo
        duration = MP.mpf(seg.t1_s) - MP.mpf(seg.t0_s)
        if kind == "first_order_exchange":
            end_hi = relax_rh(start_hi, out_hi, duration, tau)
            end_lo = relax_rh(start_lo, out_lo, duration, tau)
        else:
            end_hi, end_lo = start_hi, start_lo
        ids.append(seg.package_id)
        s_hi.append(start_hi)
        s_lo.append(start_lo)
        e_hi.append(end_hi)
        e_lo.append(end_lo)
        taus.append(tau)
        kinds.append(kind)
        prev_hi, prev_lo = end_hi, end_lo
    return OracleTrace(
        tuple(ids), tuple(s_hi), tuple(s_lo), tuple(e_hi), tuple(e_lo), tuple(taus), tuple(kinds)
    )


def forcing_indicator(segment: Segment, zone_id: str) -> int:
    """e_z(s) of spec 4.4: 1 in segments of zone z whose RH is not gap-filled."""
    return int(segment.zone_id == zone_id and "relative_humidity" not in segment.gap_quantities)


def unit_step_response(
    segments: Sequence[Segment],
    time_constants_s: Sequence[float | Mpf],
    zone_id: str,
    *,
    first_start_tracks_outside: bool,
) -> tuple[Mpf, ...]:
    """h_z at each segment start (spec 4.4)."""
    if len(segments) != len(time_constants_s):
        raise ValueError("one time constant per segment")
    starts: list[Mpf] = []
    prev_end: Mpf | None = None
    for index, (seg, tau_raw) in enumerate(zip(segments, time_constants_s, strict=True)):
        tau = MP.mpf(tau_raw)
        e = MP.mpf(forcing_indicator(seg, zone_id))
        if tau == 0:
            start = end = e
        elif MP.isinf(tau):
            start = end = MP.mpf(0)
        else:
            if index == 0:
                start = e if first_start_tracks_outside else MP.mpf(0)
            else:
                assert prev_end is not None
                start = prev_end
            decay = MP.exp(-(MP.mpf(seg.t1_s) - MP.mpf(seg.t0_s)) / tau)
            end = start * decay + e * (1 - decay)
        starts.append(start)
        prev_end = end
    return tuple(starts)
