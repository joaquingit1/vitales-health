"""True potency of a lot through a simulated mission (spec 15.4 ``true_potency_within_interval``
and 15.8 VS-3 to VS-5).

The lot's history is cut into windows of constant (zone, package) from its custody and
package intervals. Each window integrates the true rate on the zone's truth series (sensed
zones: the simulator's truth, never readings; declared zones: the declared mean, constant)
with ``tests.oracle.truth`` and the oracle's package model. Internal RH carries over between
windows while the package is unchanged; a new package starts as spec 4.3 says (configured
initial RH, else the outside RH). Theta is summed over the conversion pathways
(destructive thermal pathways; radiation stays out of potency unless the settings include it,
spec 6.1, and is then not modelled here, so the helper refuses such settings). Light-driven
pathways are refused when a lit zone meets a light-transmitting package, since the
simulator has no light truth.

The truth parameters are one row of a parameter draw (``OracleDraw``): ln k*, Ea, B, the
extrapolation z values and the zone ln multipliers. Temperature and RH offsets are sensor
calibration uncertainty in the engine's model and are not part of the truth.
"""

import dataclasses
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pharmacy.types import (
    EngineSettingsDoc,
    LotDoc,
    PackageDoc,
    Pathway,
    StabilityModel,
    StorageZoneDoc,
    iso_to_seconds,
)
from tests.oracle.draws import EA, LN_K, Z_RH, Z_T, B, OracleDraw, humidity_term_absent
from tests.oracle.kinetics import conversion_from_theta
from tests.oracle.truth import PackageModel, TruthModel, TruthPathway, integrate_truth


class DrawBlock(Protocol):
    """Structural draw input: no import of any engine implementation."""

    @property
    def c0_percent(self) -> Any: ...

    @property
    def ln_k_ref_per_s(self) -> Any: ...

    @property
    def ea_j_per_mol(self) -> Any: ...

    @property
    def b_per_percent_rh(self) -> Any: ...

    @property
    def extrapolation_z_temperature(self) -> Any: ...

    @property
    def extrapolation_z_humidity(self) -> Any: ...

    @property
    def zone_ln_multiplier(self) -> Any: ...

    @property
    def temperature_offset_k(self) -> Any: ...

    @property
    def rh_offset_percent(self) -> Any: ...


ZoneTruth = tuple[Sequence[float], Sequence[float], Sequence[float]]
"""(t_s, temperature_k, relative_humidity_percent) on the simulator grid."""


class TruthNotModelledError(ValueError):
    """The lot or settings need physics the truth helper does not model."""


@dataclasses.dataclass(frozen=True, slots=True)
class LotWindow:
    zone_id: str
    package_id: str
    t0_s: float
    t1_s: float


@dataclasses.dataclass(frozen=True, slots=True)
class LotTruth:
    """True potency (total, percent of label) and conversion Theta at each requested time,
    with the largest step-halving relative difference over the windows."""

    times_s: tuple[float, ...]
    theta: tuple[float, ...]
    potency_total_percent: tuple[float, ...]
    halving_relative_error: float


def lot_windows(lot: LotDoc, start_s: float, end_s: float) -> list[LotWindow]:
    """Constant (zone, package) windows of ``lot`` between ``start_s`` and ``end_s``."""
    custody = [
        (
            c["zone_id"],
            iso_to_seconds(c["from_utc"]),
            math.inf if c["to_utc"] is None else iso_to_seconds(c["to_utc"]),
        )
        for c in lot["custody"]
    ]
    history = sorted((iso_to_seconds(p["from_utc"]), p["package_id"]) for p in lot["package_history"])
    package_spans = [
        (pid, t0, history[i + 1][0] if i + 1 < len(history) else math.inf)
        for i, (t0, pid) in enumerate(history)
    ]
    out: list[LotWindow] = []
    for zone_id, z0, z1 in custody:
        for package_id, p0, p1 in package_spans:
            a, b = max(z0, p0, start_s), min(z1, p1, end_s)
            if b > a:
                out.append(LotWindow(zone_id, package_id, a, b))
    out.sort(key=lambda w: w.t0_s)
    return out


def oracle_draw_row(model: StabilityModel, block: DrawBlock, i: int) -> OracleDraw:
    """Row ``i`` of an engine DrawBlock as an OracleDraw (columns in model order)."""
    values: dict[str, dict[str, float]] = {}
    for j, p in enumerate(model.pathways):
        row = {
            LN_K: float(block.ln_k_ref_per_s[i, j]),
            EA: float(block.ea_j_per_mol[i, j]),
            B: float(block.b_per_percent_rh[i, j]),
            Z_T: float(block.extrapolation_z_temperature[i, j]),
            Z_RH: float(block.extrapolation_z_humidity[i, j]),
        }
        for name, attribute in (
            ("ln_k_vis_per_lux_h", "ln_k_vis_per_lux_h"),
            ("ln_k_uv_per_w_h_per_m2", "ln_k_uv_per_w_h_per_m2"),
            ("ln_k_dose_per_gy", "ln_k_dose_per_gy"),
            ("ln_k_dose_frozen_per_gy", "ln_k_dose_frozen_per_gy"),
            ("ln_frozen_rate_multiplier", "ln_frozen_multiplier"),
        ):
            if name in p.parameters.names:
                row[name] = float(getattr(block, attribute)[i, j])
        values[p.pathway_id] = row
    return OracleDraw(
        c0_percent=float(block.c0_percent[i]),
        pathway_values=values,
        zone_ln_multiplier={z: float(v[i]) for z, v in block.zone_ln_multiplier.items()},
        temperature_offset_k={z: float(v[i]) for z, v in block.temperature_offset_k.items()},
        rh_offset_percent={z: float(v[i]) for z, v in block.rh_offset_percent.items()},
    )


def _conversion_thermal(model: StabilityModel, settings: EngineSettingsDoc) -> list[Pathway]:
    if settings["radiation"]["include_in_potency"] and any(p.driver == "radiation" for p in model.pathways):
        raise TruthNotModelledError("radiation in potency is not part of the simulated truth")
    if any(p.role == "racemization" for p in model.pathways):
        raise TruthNotModelledError("racemization (active fraction) is not part of the simulated truth")
    return [p for p in model.pathways if p.role == "destructive" and p.driver == "thermal"]


def truth_model(model: StabilityModel, draw: OracleDraw, settings: EngineSettingsDoc) -> TruthModel:
    """The thermal conversion pathways of ``model`` at the parameter values of ``draw``."""
    pathways = []
    for p in _conversion_thermal(model, settings):
        v = draw.pathway_values[p.pathway_id]
        vr = p.validated_range
        absent = humidity_term_absent(p)
        pathways.append(
            TruthPathway(
                ln_k_ref_per_s=v[LN_K],
                ea_j_per_mol=v.get(EA, 0.0),
                b_per_percent_rh=0.0 if absent else v.get(B, 0.0),
                z_temperature=v.get(Z_T, 0.0),
                z_humidity=v.get(Z_RH, 0.0),
                temperature_min_k=vr.temperature_min_k,
                temperature_max_k=vr.temperature_max_k,
                rh_min_percent=vr.rh_min_percent,
                rh_max_percent=vr.rh_max_percent,
                humidity_term=not absent,
            )
        )
    return TruthModel(
        pathways=tuple(pathways),
        reference_temperature_k=model.reference_temperature_k,
        reference_rh_percent=model.reference_rh_percent,
        sd_ln_k_per_kelvin=settings["extrapolation"]["ln_k_sd_per_kelvin"]["value"],
        sd_ln_k_per_percent_rh=settings["extrapolation"]["ln_k_sd_per_percent_rh"]["value"],
    )


def _interpolate(ts: Sequence[float], ys: Sequence[float], t: float) -> float:
    lo, hi = 0, len(ts) - 1
    if t <= ts[0]:
        return ys[0]
    if t >= ts[hi]:
        return ys[hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    w = (t - ts[lo]) / (ts[hi] - ts[lo])
    return ys[lo] + (ys[hi] - ys[lo]) * w


def _window_series(
    truth: ZoneTruth, t0: float, t1: float, extra: Sequence[float]
) -> tuple[list[float], list[float], list[float]]:
    ts, temps, rhs = truth
    inner = [t for t in ts if t0 < t < t1]
    nodes = sorted({t0, t1, *inner, *(t for t in extra if t0 < t < t1)})
    return nodes, [_interpolate(ts, temps, t) for t in nodes], [_interpolate(ts, rhs, t) for t in nodes]


def declared_truth(zone: StorageZoneDoc, t0: float, t1: float) -> ZoneTruth:
    """A declared zone's truth: its declared means, constant."""
    declared = zone["declared_conditions"]
    if declared is None:
        raise TruthNotModelledError(f"zone {zone['zone_id']} has no declared conditions")
    t_prior, rh_prior = declared["temperature_k"], declared["relative_humidity_percent"]
    if t_prior is None or rh_prior is None:
        raise TruthNotModelledError(f"zone {zone['zone_id']} declares no temperature or RH")
    t, rh = t_prior["mu"], rh_prior["mu"]
    return ([t0, t1], [t, t], [rh, rh])


def _check_light(
    model: StabilityModel,
    windows: Sequence[LotWindow],
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
) -> None:
    if not any(p.driver == "light" and p.role == "destructive" for p in model.pathways):
        return
    for w in windows:
        package = packages[w.package_id]
        lit = zones[w.zone_id]["light_exposure"]
        transmits = (
            package["light_transmission_visible"]["value"] > 0
            or package["light_transmission_uv"]["value"] > 0
        )
        if lit and transmits:
            raise TruthNotModelledError(f"light pathway in lit zone {w.zone_id} through {w.package_id}")


def lot_true_potency(
    model: StabilityModel,
    draw: OracleDraw,
    lot: LotDoc,
    zone_truth: Mapping[str, ZoneTruth],
    zones: Mapping[str, StorageZoneDoc],
    packages: Mapping[str, PackageDoc],
    settings: EngineSettingsDoc,
    times_s: Sequence[float],
    *,
    relative_target: float = 1e-6,
) -> LotTruth:
    """True total potency of ``lot`` at each of ``times_s`` (ascending) for one truth draw.
    ``zone_truth`` holds the sensed zones' truth series; declared zones use their means."""
    if list(times_s) != sorted(times_s):
        raise ValueError("times must be ascending")
    start = min(iso_to_seconds(c["from_utc"]) for c in lot["custody"])
    windows = lot_windows(lot, start, max(times_s))
    _check_light(model, windows, zones, packages)
    tm = truth_model(model, draw, settings)
    kind_args = {"order_n": model.order_n, "alpha0": model.alpha0, "avrami_n": model.avrami_n}
    cumulative = 0.0
    reported: dict[float, float] = {}
    worst = 0.0
    rh_in: float | None = None
    previous_package: str | None = None
    for w in windows:
        if zones[w.zone_id]["kind"] == "declared":
            series = declared_truth(zones[w.zone_id], w.t0_s, w.t1_s)
        else:
            series = zone_truth[w.zone_id]
        nodes, temps, rhs = _window_series(series, w.t0_s, w.t1_s, times_s)
        report = [i for i, t in enumerate(nodes) if t in set(times_s)] + [len(nodes) - 1]
        report = sorted(set(report))
        package = PackageModel.from_doc(packages[w.package_id])
        carry = rh_in if w.package_id == previous_package else None
        result = integrate_truth(
            tm,
            nodes,
            temps,
            rhs,
            package,
            ln_multiplier=draw.zone_ln_multiplier.get(w.zone_id, 0.0),
            report_indices=report,
            rh_in_start=carry,
            relative_target=relative_target,
        )
        worst = max(worst, result.halving_relative_error)
        for index, theta in zip(report, result.theta_at, strict=True):
            if nodes[index] in times_s:
                reported[nodes[index]] = cumulative + theta
        cumulative += result.theta_at[-1]
        rh_in = result.rh_in_end
        previous_package = w.package_id
    thetas = tuple(reported.get(t, cumulative) for t in times_s)
    potencies = tuple(
        draw.c0_percent * (1.0 - float(conversion_from_theta(model.rate_law_kind, theta, **kind_args)))
        for theta in thetas
    )
    return LotTruth(tuple(times_s), thetas, potencies, worst)
