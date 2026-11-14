"""Oracle for ``kinetics.evaluate_draws`` and ``kinetics.nominal_thetas`` (spec 4.4, 5.4 to
5.8, 6, 8) at 50 significant digits, one draw at a time.

The oracle recomputes the microclimate trace and the unit-step responses itself (it never
consumes the engine's), then applies, per segment s and pathway p:

    RH_out'   = RH_out(variant) + e_z(s) dRH_z          (variant: high when B >= 0, else low)
    T'        = T + dT_z                                (0 in temperature gaps)
    b'        = RH_in,start(variant) + sum_z d_z h_z(s) - RH_out'
    ln k      = ln k* + ln m_zone + [frozen] ln m_frozen + z_T sd_T d_T + z_RH sd_RH d_RH
                - (Ea/R)(1/T' - 1/T*) + B (RH_out' - RH*)
    Theta_p,s = exp(ln k) J(B b', tau, tau_m)

for thermal pathways, Theta_L = (k_vis Tr_vis E + k_uv Tr_uv U) tau / 3600 in light-exposed
zones for light pathways, and Theta_D = k_D dose_rate tau for radiation pathways (the frozen
direct-effect constant in frozen solution segments). Interpretations where the spec is
silent are listed in docs/requests/B3.md (widening only on thermal pathways, no zone
multiplier on radiation, fixed multiplier median, B "fixed at 0").
"""

import dataclasses
from collections.abc import Mapping, Sequence

from pharmacy.types import (
    EngineSettingsDoc,
    PackageDoc,
    Pathway,
    Prior,
    Segment,
    StabilityModel,
    StorageZoneDoc,
)
from tests.oracle.kinetics import conversion_from_theta, humidity_integral
from tests.oracle.microclimate import OracleTrace, forcing_indicator, internal_rh_trace, unit_step_response
from tests.oracle.precision import MP, R_MP, SECONDS_PER_HOUR, Mpf

LIQUID_FAMILIES = ("solution", "suspension")
TEMPERATURE = "temperature_k"
LN_K = "ln_k_ref_per_s"
EA = "ea_j_per_mol"
B = "b_per_percent_rh"
LN_K_VIS = "ln_k_vis_per_lux_h"
LN_K_UV = "ln_k_uv_per_w_h_per_m2"
LN_K_DOSE = "ln_k_dose_per_gy"
LN_K_DOSE_FROZEN = "ln_k_dose_frozen_per_gy"
LN_M_FROZEN = "ln_frozen_rate_multiplier"
Z_T = "z_t"
Z_RH = "z_rh"


@dataclasses.dataclass(frozen=True, slots=True)
class OracleDraw:
    """One draw: per-pathway parameter values by ParameterName (plus ``z_t`` and ``z_rh``)
    and per-zone (ln multiplier, temperature offset K, RH offset %RH)."""

    c0_percent: float
    pathway_values: Mapping[str, Mapping[str, float]]
    zone_ln_multiplier: Mapping[str, float]
    temperature_offset_k: Mapping[str, float]
    rh_offset_percent: Mapping[str, float]


@dataclasses.dataclass(frozen=True, slots=True)
class OracleDrawResult:
    theta_by_pathway: dict[str, Mpf]
    theta_conversion: Mpf
    theta_racemization: Mpf
    alpha: Mpf
    active_fraction: Mpf
    potency_total_percent: Mpf
    potency_active_percent: Mpf
    degradant_percent_by_pathway: dict[str, Mpf]
    degradant_percent_by_degradant: dict[str, Mpf]
    radiation_loss_fraction: Mpf


@dataclasses.dataclass(frozen=True, slots=True)
class OracleNominal:
    pathway_id: str
    theta: Mpf
    theta_out_of_range: Mpf
    theta_frozen: Mpf
    theta_phase_change: Mpf


@dataclasses.dataclass(frozen=True, slots=True)
class _Context:
    trace: OracleTrace
    responses: dict[str, tuple[Mpf, ...]]


def first_start_tracks_outside(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    initial_rh_percent: Mapping[str, float] | None = None,
) -> bool:
    """True when the first segment's package is first_order_exchange with no configured or
    overriding initial RH, so its start follows RH_out (spec 4.3, 4.4)."""
    if not segments or segments[0].package_id is None:
        return False
    package_id = segments[0].package_id
    moisture = packages[package_id]["moisture_model"]
    if moisture["kind"] != "first_order_exchange":
        return False
    overridden = initial_rh_percent is not None and package_id in initial_rh_percent
    return not overridden and moisture["initial_internal_rh_percent"] is None


def _context(
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    initial_rh_percent: Mapping[str, float] | None,
) -> _Context:
    trace = internal_rh_trace(segments, packages, initial_rh_percent)
    tracks = first_start_tracks_outside(segments, packages, initial_rh_percent)
    zone_ids = sorted({s.zone_id for s in segments})
    responses = {
        z: unit_step_response(segments, trace.time_constant_s, z, first_start_tracks_outside=tracks)
        for z in zone_ids
    }
    return _Context(trace, responses)


def humidity_term_absent(pathway: Pathway) -> bool:
    """B absent from the block or fixed at 0 (mean 0, variance 0), spec 8.1."""
    names = pathway.parameters.names
    if B not in names:
        return True
    i = names.index(B)
    return pathway.parameters.mean[i] == 0 and pathway.parameters.covariance[i][i] == 0


def measured_margin(pathway: Pathway, settings: EngineSettingsDoc) -> float:
    """Spec 8.1: only measured models receive the configured lower-temperature margin."""
    if pathway.validated_range.source_layer != "measured_model":
        return 0.0
    return settings["validated_range"]["temperature_below_min_margin_k"]["value"]


def distance_temperature(pathway: Pathway, temperature_k: float, margin_k: float = 0.0) -> Mpf:
    vr = pathway.validated_range
    t = MP.mpf(temperature_k)
    return max(
        MP.mpf(0), t - MP.mpf(vr.temperature_max_k), MP.mpf(vr.temperature_min_k) - MP.mpf(margin_k) - t
    )


def distance_humidity(pathway: Pathway, rh_values: Sequence[Mpf]) -> Mpf:
    if humidity_term_absent(pathway):
        return MP.mpf(0)
    vr = pathway.validated_range
    lo, hi = MP.mpf(vr.rh_min_percent), MP.mpf(vr.rh_max_percent)
    return max(max(MP.mpf(0), rh - hi, lo - rh) for rh in rh_values)


def is_frozen(model: StabilityModel, segment: Segment) -> bool:
    """Spec 8.3: solution or suspension segment with nominal T below the freezing point."""
    return (
        model.dose_form_family in LIQUID_FAMILIES
        and model.freezing_point_k is not None
        and segment.temperature_k < model.freezing_point_k
    )


def is_phase_change(model: StabilityModel, segment: Segment) -> bool:
    return (
        model.phase_change_temperature_k is not None
        and segment.temperature_k > model.phase_change_temperature_k
    )


def _segment_thetas(
    model: StabilityModel,
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    zones: Mapping[str, StorageZoneDoc],
    settings: EngineSettingsDoc,
    draw: OracleDraw,
    ctx: _Context,
) -> list[dict[str, Mpf]]:
    """Theta of every pathway in every segment for one draw."""
    sd_t = MP.mpf(settings["extrapolation"]["ln_k_sd_per_kelvin"]["value"])
    sd_rh = MP.mpf(settings["extrapolation"]["ln_k_sd_per_percent_rh"]["value"])
    t_ref = MP.mpf(model.reference_temperature_k)
    rh_ref = MP.mpf(model.reference_rh_percent)
    out: list[dict[str, Mpf]] = []
    for s, seg in enumerate(segments):
        zone = seg.zone_id
        tau = MP.mpf(seg.t1_s) - MP.mpf(seg.t0_s)
        e = MP.mpf(forcing_indicator(seg, zone))
        offset_sum = MP.fsum(MP.mpf(draw.rh_offset_percent[z]) * h[s] for z, h in ctx.responses.items())
        d_t = MP.mpf(0) if TEMPERATURE in seg.gap_quantities else MP.mpf(draw.temperature_offset_k[zone])
        t_prime = MP.mpf(seg.temperature_k) + d_t
        assert seg.package_id is not None
        package = packages[seg.package_id]
        tau_m = ctx.trace.time_constant_s[s]
        rh_nominal = (ctx.trace.rh_in_start_high[s], ctx.trace.rh_in_end_high[s])
        row: dict[str, Mpf] = {}
        for pathway in model.pathways:
            values = draw.pathway_values[pathway.pathway_id]
            if pathway.driver == "light":
                if not zones[zone]["light_exposure"]:
                    row[pathway.pathway_id] = MP.mpf(0)
                    continue
                k_vis = MP.exp(MP.mpf(values[LN_K_VIS])) if LN_K_VIS in values else MP.mpf(0)
                k_uv = MP.exp(MP.mpf(values[LN_K_UV])) if LN_K_UV in values else MP.mpf(0)
                tr_vis = MP.mpf(package["light_transmission_visible"]["value"])
                tr_uv = MP.mpf(package["light_transmission_uv"]["value"])
                dose = k_vis * tr_vis * MP.mpf(seg.illuminance_lux) + k_uv * tr_uv * MP.mpf(seg.uv_w_per_m2)
                row[pathway.pathway_id] = dose * tau / SECONDS_PER_HOUR
                continue
            if pathway.driver == "radiation":
                name = LN_K_DOSE_FROZEN if is_frozen(model, seg) and LN_K_DOSE_FROZEN in values else LN_K_DOSE
                row[pathway.pathway_id] = MP.exp(MP.mpf(values[name])) * MP.mpf(seg.dose_rate_gy_per_s) * tau
                continue
            b_value = MP.mpf(values.get(B, 0.0))
            high = b_value >= 0
            rh_out = MP.mpf(seg.relative_humidity_percent if high else seg.relative_humidity_low_percent)
            rh_out_prime = rh_out + e * MP.mpf(draw.rh_offset_percent[zone])
            rh_in_start = ctx.trace.rh_in_start_high[s] if high else ctx.trace.rh_in_start_low[s]
            b_prime = rh_in_start + offset_sum - rh_out_prime
            ln_k = MP.mpf(values[LN_K]) + MP.mpf(draw.zone_ln_multiplier[zone])
            if is_frozen(model, seg) and LN_M_FROZEN in values:
                ln_k += MP.mpf(values[LN_M_FROZEN])
            ln_k += (
                MP.mpf(values.get(Z_T, 0.0))
                * sd_t
                * distance_temperature(pathway, seg.temperature_k, measured_margin(pathway, settings))
            )
            ln_k += MP.mpf(values.get(Z_RH, 0.0)) * sd_rh * distance_humidity(pathway, rh_nominal)
            ln_k -= MP.mpf(values.get(EA, 0.0)) / R_MP * (1 / t_prime - 1 / t_ref)
            ln_k += b_value * (rh_out_prime - rh_ref)
            row[pathway.pathway_id] = MP.exp(ln_k) * humidity_integral(b_value * b_prime, tau, tau_m)
        out.append(row)
    return out


def _in_conversion(pathway: Pathway, settings: EngineSettingsDoc) -> bool:
    if pathway.role != "destructive":
        return False
    return pathway.driver != "radiation" or settings["radiation"]["include_in_potency"]


def evaluate_draw(
    model: StabilityModel,
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    zones: Mapping[str, StorageZoneDoc],
    settings: EngineSettingsDoc,
    draw: OracleDraw,
    *,
    api_molar_mass_g_per_mol: float | None = None,
    initial_rh_percent: Mapping[str, float] | None = None,
) -> OracleDrawResult:
    """Spec 5.6 to 5.8 and 6 for one draw over the lot's segments (time order)."""
    ordered = sorted(segments, key=lambda s: s.t0_s)
    ctx = _context(ordered, packages, initial_rh_percent)
    rows = _segment_thetas(model, ordered, packages, zones, settings, draw, ctx)
    kind_args = {"order_n": model.order_n, "alpha0": model.alpha0, "avrami_n": model.avrami_n}
    conversion_ids = [p.pathway_id for p in model.pathways if _in_conversion(p, settings)]
    racemization_ids = [p.pathway_id for p in model.pathways if p.role == "racemization"]
    radiation_ids = [p.pathway_id for p in model.pathways if p.driver == "radiation"]
    theta_by = {p.pathway_id: MP.fsum(row[p.pathway_id] for row in rows) for p in model.pathways}

    alpha_by = {pid: MP.mpf(0) for pid in conversion_ids}
    cumulative = MP.mpf(0)
    g_prev = MP.mpf(0)
    for row in rows:
        theta_seg = MP.fsum(row[pid] for pid in conversion_ids)
        cumulative += theta_seg
        g_now = conversion_from_theta(model.rate_law_kind, cumulative, **kind_args)
        if theta_seg > 0:
            for pid in conversion_ids:
                alpha_by[pid] += row[pid] / theta_seg * (g_now - g_prev)
        g_prev = g_now
    theta_conv = MP.fsum(theta_by[pid] for pid in conversion_ids)
    alpha = conversion_from_theta(model.rate_law_kind, theta_conv, **kind_args)
    theta_r = MP.fsum(theta_by[pid] for pid in racemization_ids)
    if racemization_ids:
        f0 = MP.mpf(model.initial_active_fraction)
        rho = (1 + (2 * f0 - 1) * MP.exp(-2 * theta_r)) / 2
    else:
        rho = MP.mpf(1)
    c0 = MP.mpf(draw.c0_percent)
    total = c0 * (1 - alpha)
    degradant_by_pathway: dict[str, Mpf] = {}
    degradant_by_id: dict[str, Mpf] = {}
    for p in model.pathways:
        if p.degradant is None or p.pathway_id not in alpha_by:
            continue
        ratio = MP.mpf(1)
        if p.degradant.molar_mass_g_per_mol is not None and api_molar_mass_g_per_mol is not None:
            ratio = MP.mpf(p.degradant.molar_mass_g_per_mol) / MP.mpf(api_molar_mass_g_per_mol)
        value = c0 * MP.mpf(p.degradant.yield_mol_per_mol) * ratio * alpha_by[p.pathway_id]
        degradant_by_pathway[p.pathway_id] = value
        degradant_by_id[p.degradant.degradant_id] = (
            degradant_by_id.get(p.degradant.degradant_id, MP.mpf(0)) + value
        )
    radiation_loss = -MP.expm1(-MP.fsum(theta_by[pid] for pid in radiation_ids))
    return OracleDrawResult(
        theta_by_pathway=theta_by,
        theta_conversion=theta_conv,
        theta_racemization=theta_r,
        alpha=alpha,
        active_fraction=rho,
        potency_total_percent=total,
        potency_active_percent=total * rho,
        degradant_percent_by_pathway=degradant_by_pathway,
        degradant_percent_by_degradant=degradant_by_id,
        radiation_loss_fraction=radiation_loss,
    )


def median_ln_multiplier(prior: Prior) -> float:
    """ln of the median of a zone rate multiplier: lognormal gives mu; fixed gives ln(mu)
    (types.Prior: fixed means value = mu; spec 9.7 writes "ln m = mu", request B3-R3)."""
    if prior["family"] == "lognormal":
        return prior["mu"]
    if prior["family"] == "fixed":
        return float(MP.log(MP.mpf(prior["mu"])))
    raise ValueError(f"unsupported multiplier family {prior['family']}")


def nominal_draw(
    model: StabilityModel, zones: Mapping[str, StorageZoneDoc], zone_ids: Sequence[str]
) -> OracleDraw:
    """Posterior means, Ea at its location, multipliers at their medians, offsets and z 0."""
    values: dict[str, dict[str, float]] = {}
    for p in model.pathways:
        block = p.parameters
        values[p.pathway_id] = {str(n): m for n, m in zip(block.names, block.mean, strict=True)}
    return OracleDraw(
        c0_percent=100.0,
        pathway_values=values,
        zone_ln_multiplier={z: median_ln_multiplier(zones[z]["rate_multiplier"]) for z in zone_ids},
        temperature_offset_k=dict.fromkeys(zone_ids, 0.0),
        rh_offset_percent=dict.fromkeys(zone_ids, 0.0),
    )


def nominal_thetas(
    model: StabilityModel,
    segments: Sequence[Segment],
    packages: Mapping[str, PackageDoc],
    zones: Mapping[str, StorageZoneDoc],
    settings: EngineSettingsDoc,
    *,
    initial_rh_percent: Mapping[str, float] | None = None,
) -> tuple[OracleNominal, ...]:
    """Spec 5.8 and 8: nominal Theta per pathway and the parts accrued out of range (d_T > 0
    or d_RH > 0 for that pathway, nominal values), frozen and during phase change."""
    ordered = sorted(segments, key=lambda s: s.t0_s)
    ctx = _context(ordered, packages, initial_rh_percent)
    draw = nominal_draw(model, zones, sorted({s.zone_id for s in ordered}))
    rows = _segment_thetas(model, ordered, packages, zones, settings, draw, ctx)
    result: list[OracleNominal] = []
    for p in sorted(model.pathways, key=lambda q: q.pathway_id):
        out_of_range, frozen, phase = MP.mpf(0), MP.mpf(0), MP.mpf(0)
        for s, (seg, row) in enumerate(zip(ordered, rows, strict=True)):
            theta = row[p.pathway_id]
            rh_nominal = (ctx.trace.rh_in_start_high[s], ctx.trace.rh_in_end_high[s])
            if (
                distance_temperature(p, seg.temperature_k, measured_margin(p, settings)) > 0
                or distance_humidity(p, rh_nominal) > 0
            ):
                out_of_range += theta
            if is_frozen(model, seg):
                frozen += theta
            if is_phase_change(model, seg):
                phase += theta
        result.append(
            OracleNominal(
                p.pathway_id, MP.fsum(row[p.pathway_id] for row in rows), out_of_range, frozen, phase
            )
        )
    return tuple(result)
