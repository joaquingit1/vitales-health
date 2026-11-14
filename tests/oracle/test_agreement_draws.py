# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""``kinetics.evaluate_draws`` and ``kinetics.nominal_thetas`` against the oracle (spec 5.6
to 5.8, 6, 8, 15.2): five hand-built draws over a 30-segment exposure with a bottle, a bag
and a sealed blister (solid model, several rate-law kinds), and a frozen-capable solution
model with racemization and radiolysis in potency.

The microclimate trace and unit-step responses handed to the engine are the oracle's own
(rounded to float), so a disagreement here is about ``evaluate_draws`` alone. Light-exposed
segments stay inside the photolysis class range and radiolysis is never outside its range,
so the open question of widening on non-thermal pathways (request B3-R16) cannot affect the
comparison.
"""

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pytest

from pharmacy.config import ConfigBundle
from pharmacy.types import (
    DegradantSpec,
    DoseFormFamily,
    EngineSettingsDoc,
    LotExposure,
    MicroclimateTrace,
    PackageDoc,
    ParameterBlock,
    ParameterName,
    Pathway,
    RateLawKind,
    Segment,
    StabilityModel,
    StorageZoneDoc,
    ValidatedRange,
)
from tests.oracle import draws as od
from tests.oracle import microclimate as om
from tests.oracle.compare import assert_agrees
from tests.oracle.precision import MP

THERMAL_RANGE = ValidatedRange(288.15, 313.15, 10.0, 75.0, "class_prior", ())
WIDE_RANGE = ValidatedRange(275.15, 313.15, 0.0, 100.0, "class_prior", ())
RADIOLYSIS_RANGE = ValidatedRange(150.0, 400.0, 0.0, 100.0, "class_prior", ())
DRAW_FIELDS: Mapping[str, str] = {
    "ln_k_ref_per_s": "ln_k_ref_per_s",
    "ea_j_per_mol": "ea_j_per_mol",
    "b_per_percent_rh": "b_per_percent_rh",
    "ln_k_vis_per_lux_h": "ln_k_vis_per_lux_h",
    "ln_k_uv_per_w_h_per_m2": "ln_k_uv_per_w_h_per_m2",
    "ln_k_dose_per_gy": "ln_k_dose_per_gy",
    "ln_k_dose_frozen_per_gy": "ln_k_dose_frozen_per_gy",
    "ln_frozen_rate_multiplier": "ln_frozen_multiplier",
    "z_t": "extrapolation_z_temperature",
    "z_rh": "extrapolation_z_humidity",
}


def _block(names: Sequence[ParameterName], mean: Sequence[float], sd: Sequence[float]) -> ParameterBlock:
    cov = tuple(tuple(sd[i] ** 2 if i == j else 0.0 for j in range(len(names))) for i in range(len(names)))
    return ParameterBlock(tuple(names), tuple(mean), cov, 0.0, 0.0, ())


def _degradant(pathway_class: str, yield_: float) -> DegradantSpec:
    return DegradantSpec(
        f"deg-{pathway_class}", pathway_class, None, pathway_class, yield_, None, 1.0, "unknown", False, ()
    )


def _pathway(
    pid: str,
    mechanism: Any,
    driver: Any,
    role: Any,
    block: ParameterBlock,
    vr: ValidatedRange,
    degradant: DegradantSpec | None,
) -> Pathway:
    return Pathway(
        pid,
        pid.split(":", maxsplit=1)[0],
        pid.split(":")[1],
        mechanism,
        driver,
        role,
        (),
        block,
        vr,
        degradant,
        None,
    )


def solid_model(kind: RateLawKind, params: Mapping[str, float]) -> StabilityModel:
    pathways = (
        _pathway(
            "api:ester_hydrolysis",
            "hydrolysis",
            "thermal",
            "destructive",
            _block(
                ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
                [-19.5, 95000.0, 0.04],
                [0.5, 2e4, 0.02],
            ),
            THERMAL_RANGE,
            _degradant("ester_hydrolysis", 1.0),
        ),
        _pathway(
            "api:phenol_autoxidation",
            "autoxidation",
            "thermal",
            "destructive",
            _block(
                ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
                [-20.5, 110000.0, -0.01],
                [0.5, 2e4, 0.02],
            ),
            THERMAL_RANGE,
            _degradant("phenol_autoxidation", 0.5),
        ),
        _pathway(
            "api:photolysis_conjugated",
            "photolysis",
            "light",
            "destructive",
            _block(["ln_k_vis_per_lux_h", "ln_k_uv_per_w_h_per_m2"], [-16.0, -9.0], [1.0, 1.0]),
            WIDE_RANGE,
            None,
        ),
        _pathway(
            "api:radiolysis",
            "radiolysis",
            "radiation",
            "destructive",
            _block(["ln_k_dose_per_gy"], [-12.0], [1.15]),
            RADIOLYSIS_RANGE,
            None,
        ),
    )
    return _model("solid", kind, params, pathways, freezing=None, phase_change=315.0, f0=1.0)


def solution_model() -> StabilityModel:
    pathways = (
        _pathway(
            "epi:catechol_autoxidation",
            "autoxidation",
            "thermal",
            "destructive",
            _block(
                ["ln_k_ref_per_s", "ea_j_per_mol", "ln_frozen_rate_multiplier"],
                [-17.0, 65000.0, 0.0],
                [0.6, 1.4e4, 1.17],
            ),
            THERMAL_RANGE,
            _degradant("catechol_autoxidation", 1.0),
        ),
        _pathway(
            "epi:racemization_benzylic_alcohol",
            "racemization",
            "thermal",
            "racemization",
            _block(["ln_k_ref_per_s", "ea_j_per_mol"], [-16.5, 90000.0], [0.7, 1e4]),
            WIDE_RANGE,
            None,
        ),
        _pathway(
            "epi:radiolysis",
            "radiolysis",
            "radiation",
            "destructive",
            _block(["ln_k_dose_per_gy", "ln_k_dose_frozen_per_gy"], [-8.0, -11.0], [0.5, 1.15]),
            RADIOLYSIS_RANGE,
            None,
        ),
    )
    return _model("solution", "F1", {}, pathways, freezing=272.65, phase_change=None, f0=0.995)


def _model(
    family: DoseFormFamily,
    kind: RateLawKind,
    params: Mapping[str, float],
    pathways: tuple[Pathway, ...],
    *,
    freezing: float | None,
    phase_change: float | None,
    f0: float,
) -> StabilityModel:
    ordered = tuple(sorted(pathways, key=lambda p: p.pathway_id))
    return StabilityModel(
        "test-model",
        "test-product",
        ordered[0].ingredient_id,
        family,
        kind,
        params.get("order_n"),
        params.get("alpha0"),
        params.get("avrami_n"),
        298.15,
        40.0,
        ordered,
        None,
        None,
        f0,
        freezing,
        phase_change,
        None,
        (),
    )


def solid_segments() -> list[Segment]:
    """30 segments: transit (bottle), moon (bottle, then bag) with a 45 C excursion, an RH
    gap and a temperature gap, mars (bag, then blister) with light on and off."""
    out: list[Segment] = []
    t = 1.9e9
    for i in range(30):
        duration = 3600.0 * (1 + (i * 5) % 17) * (24 if i % 4 == 0 else 1)
        zone = "transit_kit" if i < 6 else ("moon_base_pharmacy" if i < 18 else "mars_base_pharmacy")
        package = "original_bottle" if i < 10 else ("repackaged_bag" if i < 20 else "original_blister")
        temp = 293.0 + (i * 7) % 14
        rh = 25.0 + (i * 11) % 45
        low = rh
        gaps: tuple[str, ...] = ()
        if i in (12, 13):
            temp = 318.15
        if i == 4:
            temp = 284.0
        if i in (8, 15):
            gaps, rh, low = ("relative_humidity",), 75.0, 10.0
        if i == 16:
            gaps, temp = ("temperature_k",), 313.15
        lux, uv = (500.0, 0.1) if zone == "mars_base_pharmacy" and i % 2 == 0 else (0.0, 0.0)
        if zone == "mars_base_pharmacy":
            temp = min(temp, 305.0)
        dose = 1.4e-9 * (1 + i % 3) + (2e-6 if i == 14 else 0.0)
        out.append(Segment(t, t + duration, zone, package, temp, rh, low, dose, lux, uv, gaps, ()))  # type: ignore[arg-type]
        t += duration
    return out


def solution_segments() -> list[Segment]:
    out: list[Segment] = []
    t = 1.9e9
    for i in range(30):
        duration = 7200.0 * (1 + (i * 3) % 11) * (12 if i % 5 == 0 else 1)
        zone = "transit_kit" if i < 8 else "moon_base_pharmacy"
        temp = 290.0 + (i * 3) % 12
        if i in (10, 11, 12):
            temp = 268.15
        if i in (20, 21):
            temp = 318.15
        dose = 2e-9 + (5e-6 if i == 11 else 0.0)
        out.append(Segment(t, t + duration, zone, "auto_injector", temp, 35.0, 35.0, dose, 0.0, 0.0, (), ()))
        t += duration
    return out


def draw_rows(model: StabilityModel, zone_ids: Sequence[str], n: int = 5) -> list[od.OracleDraw]:
    """Five deterministic, hand-shaped draws: means plus fixed multiples of the sd (a
    negative B in draw 3, large widening z in draws 1 and 4)."""
    shifts = [0.0, 1.3, -0.8, -2.1, 2.4][:n]
    out = []
    for j, shift in enumerate(shifts):
        values: dict[str, dict[str, float]] = {}
        for p in model.pathways:
            block = p.parameters
            row: dict[str, float] = {}
            for k, name in enumerate(block.names):
                sd = math.sqrt(block.covariance[k][k])
                row[name] = block.mean[k] + (shift if name != "b_per_percent_rh" else -1.5 * shift) * sd
            row["z_t"] = (1.0, 2.5, -0.7, 0.3, -1.9)[j]
            row["z_rh"] = (-0.5, 1.8, 0.4, -2.2, 0.9)[j]
            values[p.pathway_id] = row
        out.append(
            od.OracleDraw(
                c0_percent=100.0 + shift,
                pathway_values=values,
                zone_ln_multiplier={z: 0.2 * shift * (1 + k) for k, z in enumerate(zone_ids)},
                temperature_offset_k={z: 0.3 * shift - 0.1 * k for k, z in enumerate(zone_ids)},
                rh_offset_percent={z: 2.0 * shift + k for k, z in enumerate(zone_ids)},
            )
        )
    return out


def draw_block(model: StabilityModel, rows: Sequence[od.OracleDraw]) -> Any:
    from pharmacy.kinetics import DrawBlock

    n, p = len(rows), len(model.pathways)
    matrices = {field: np.zeros((n, p)) for field in DRAW_FIELDS.values()}
    for j, row in enumerate(rows):
        for k, pathway in enumerate(model.pathways):
            values = row.pathway_values[pathway.pathway_id]
            for name, field in DRAW_FIELDS.items():
                if name in values:
                    matrices[field][j, k] = values[name]
            if "ln_k_dose_per_gy" in values and "ln_k_dose_frozen_per_gy" not in values:
                matrices["ln_k_dose_frozen_per_gy"][j, k] = values["ln_k_dose_per_gy"]
    zone_ids = sorted(rows[0].zone_ln_multiplier)
    return DrawBlock(
        n=n,
        c0_percent=np.asarray([r.c0_percent for r in rows]),
        z_c0=np.zeros(n),
        zone_ln_multiplier={z: np.asarray([r.zone_ln_multiplier[z] for r in rows]) for z in zone_ids},
        temperature_offset_k={z: np.asarray([r.temperature_offset_k[z] for r in rows]) for z in zone_ids},
        rh_offset_percent={z: np.asarray([r.rh_offset_percent[z] for r in rows]) for z in zone_ids},
        **matrices,
    )


def float_trace(segments: Sequence[Segment], packages: Mapping[str, PackageDoc]) -> MicroclimateTrace:
    t = om.internal_rh_trace(segments, packages)

    def f(values: Sequence[Any]) -> tuple[float, ...]:
        return tuple(math.inf if MP.isinf(v) else float(v) for v in values)

    return MicroclimateTrace(
        t.package_ids,
        f(t.rh_in_start_high),
        f(t.rh_in_start_low),
        f(t.rh_in_end_high),
        f(t.rh_in_end_low),
        f(t.time_constant_s),
        t.moisture_kind,
    )


def float_responses(
    segments: Sequence[Segment], packages: Mapping[str, PackageDoc]
) -> dict[str, tuple[float, ...]]:
    t = om.internal_rh_trace(segments, packages)
    tracks = od.first_start_tracks_outside(segments, packages)
    return {
        z: tuple(
            float(h)
            for h in om.unit_step_response(segments, t.time_constant_s, z, first_start_tracks_outside=tracks)
        )
        for z in sorted({s.zone_id for s in segments})
    }


def _check(
    model: StabilityModel,
    segments: list[Segment],
    bundle: ConfigBundle,
    settings: EngineSettingsDoc,
    label: str,
) -> None:
    from pharmacy import kinetics

    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    zones: dict[str, StorageZoneDoc] = {z["zone_id"]: z for z in bundle.zones["zones"]}
    zone_ids = sorted({s.zone_id for s in segments})
    rows = draw_rows(model, zone_ids)
    exposure = LotExposure("lot-test", segments[0].t0_s, segments[-1].t1_s, tuple(segments), (), False)
    trace = float_trace(segments, packages)
    responses = float_responses(segments, packages)
    result = kinetics.evaluate_draws(
        model, exposure, trace, responses, draw_block(model, rows), zones, packages, settings
    )
    for j, row in enumerate(rows):
        oracle = od.evaluate_draw(model, segments, packages, zones, settings, row)
        where = {"case": label, "draw": j}
        for k, p in enumerate(model.pathways):
            assert_agrees(
                "kinetics.evaluate_draws.theta",
                where | {"pathway": p.pathway_id},
                float(result.theta[j, k]),
                oracle.theta_by_pathway[p.pathway_id],
                "5.6",
            )
            degradant = oracle.degradant_percent_by_pathway.get(p.pathway_id, MP.mpf(0))
            assert_agrees(
                "kinetics.evaluate_draws.degradant_percent",
                where | {"pathway": p.pathway_id},
                float(result.degradant_percent[j, k]),
                degradant,
                "5.6",
            )
        for name, value in (
            ("alpha", oracle.alpha),
            ("active_fraction", oracle.active_fraction),
            ("potency_total_percent", oracle.potency_total_percent),
            ("potency_active_percent", oracle.potency_active_percent),
            ("radiation_loss_fraction", oracle.radiation_loss_fraction),
        ):
            assert_agrees(
                f"kinetics.evaluate_draws.{name}",
                where,
                float(getattr(result, name)[j]),
                value,
                "5.6, 5.7, 6",
            )
    nominal = {
        n.pathway_id: n for n in kinetics.nominal_thetas(model, exposure, trace, zones, packages, settings)
    }
    for o in od.nominal_thetas(model, segments, packages, zones, settings):
        e = nominal[o.pathway_id]
        where = {"case": label, "pathway": o.pathway_id}
        for member in ("theta", "theta_out_of_range", "theta_frozen", "theta_phase_change"):
            assert_agrees(
                f"kinetics.nominal_thetas.{member}", where, getattr(e, member), getattr(o, member), "5.8, 8"
            )


SOLID_KINDS: list[tuple[RateLawKind, dict[str, float]]] = [
    ("F1", {}),
    ("A2", {}),
    ("Fn", {"order_n": 1.6}),
    ("D3", {}),
    ("R2", {}),
    ("B1", {"alpha0": 0.02}),
]


@pytest.mark.parametrize(("kind", "params"), SOLID_KINDS, ids=[k for k, _ in SOLID_KINDS])
def test_evaluate_draws_solid_bottle_bag_blister(
    bundle: ConfigBundle, kind: RateLawKind, params: dict[str, float]
) -> None:
    _check(solid_model(kind, params), solid_segments(), bundle, bundle.settings, f"solid-{kind}")


@pytest.mark.parametrize("include", [False, True])
def test_evaluate_draws_solution_frozen_racemizing(bundle: ConfigBundle, include: bool) -> None:
    settings = copy.deepcopy(bundle.settings)
    settings["radiation"]["include_in_potency"] = include
    _check(solution_model(), solution_segments(), bundle, settings, f"solution-include={include}")


def test_evaluate_draws_solid_with_radiation_in_potency(bundle: ConfigBundle) -> None:
    settings = copy.deepcopy(bundle.settings)
    settings["radiation"]["include_in_potency"] = True
    _check(solid_model("F1", {}), solid_segments(), bundle, settings, "solid-radiation-in-potency")


def test_oracle_draw_none_package_matches_integrated_rate(bundle: ConfigBundle) -> None:
    """Self-consistency of the draw oracle: with no offsets, no widening and a package of
    kind none, Theta is the spec 5.2 integral times each zone's configured nominal multiplier."""
    from tests.oracle import kinetics as ok

    model = solution_model()
    segments = [s for s in solution_segments() if s.temperature_k > 272.65 and s.temperature_k <= 313.15]
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    zone_ids = sorted({s.zone_id for s in segments})
    row = od.nominal_draw(model, zones, zone_ids)
    result = od.evaluate_draw(model, segments, packages, zones, bundle.settings, row)
    p = model.pathways[0]
    expected = MP.fsum(
        MP.exp(MP.mpf(row.zone_ln_multiplier[s.zone_id]))
        * ok.integrated_rate(
            p.parameters.mean[0],
            p.parameters.mean[1],
            0.0,
            [s.temperature_k],
            [35.0],
            [s.t1_s - s.t0_s],
            298.15,
            40.0,
        )
        for s in segments
    )
    assert abs(result.theta_by_pathway[p.pathway_id] - expected) <= MP.mpf(10) ** -40 * expected
