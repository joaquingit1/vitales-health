"""Hand-built stability models, exposures and sensor series for the B1 unit tests.

Nothing here touches the chemistry layer: models are written out parameter by parameter so
every test knows its exact inputs.
"""

import dataclasses
import math
from collections.abc import Mapping, Sequence

import numpy as np

from pharmacy.config import ConfigBundle, ConfigDocument, canonical_sha256
from pharmacy.kinetics import DrawBlock, FloatArray
from pharmacy.types import (
    CoverageInterval,
    DegradantEstimate,
    DegradantSpec,
    DoseFormFamily,
    Driver,
    EstimateFlag,
    ExposureSummary,
    Forecast,
    ForecastStatus,
    InspectionFinding,
    JSONObject,
    LotExposure,
    Mechanism,
    MonteCarloReport,
    ParameterBlock,
    ParameterEstimate,
    ParameterName,
    Pathway,
    PathwayRole,
    Pins,
    Placard,
    PotencyEstimate,
    Projection,
    QuantileValue,
    Quantity,
    RadiationReport,
    RateLawKind,
    Segment,
    SensorPoint,
    SensorSeries,
    SourceLayer,
    SpecificationLimits,
    StabilityModel,
    ValidatedRange,
    iso_to_seconds,
    seconds_to_iso,
)

THERMAL_RANGE = ValidatedRange(288.15, 313.15, 10.0, 75.0, "class_prior", ())
WIDE_RANGE = ValidatedRange(150.0, 400.0, 0.0, 100.0, "class_prior", ())

_UNITS: Mapping[str, str] = {
    "ln_k_ref_per_s": "ln(1/s)",
    "ea_j_per_mol": "J/mol",
    "b_per_percent_rh": "1/%RH",
    "ln_k_vis_per_lux_h": "ln(1/(lux h))",
    "ln_k_uv_per_w_h_per_m2": "ln(m2/(W h))",
    "ln_k_dose_per_gy": "ln(1/Gy)",
    "ln_k_dose_frozen_per_gy": "ln(1/Gy)",
    "ln_frozen_rate_multiplier": "1",
}


def block(
    names: Sequence[ParameterName],
    mean: Sequence[float],
    sd: Sequence[float],
    *,
    layer: SourceLayer = "class_prior",
    shared_weight: float = 0.0,
    ea_lower: float = 0.0,
    covariance: Sequence[Sequence[float]] | None = None,
) -> ParameterBlock:
    """A parameter block with diagonal covariance (or the one given) and one provenance
    record per parameter at the given layer."""
    count = len(names)
    cov = (
        tuple(tuple(float(v) for v in row) for row in covariance)
        if covariance is not None
        else tuple(tuple(sd[i] ** 2 if i == j else 0.0 for j in range(count)) for i in range(count))
    )
    estimates = tuple(
        ParameterEstimate(
            name=name,
            family="normal",
            mu=float(mean[i]),
            sigma=float(sd[i]),
            lower=None,
            upper=None,
            unit=_UNITS[name],
            source_layer=layer,
            method="test",
            source_ids=(),
            basis="team_default",
            note="",
        )
        for i, name in enumerate(names)
    )
    return ParameterBlock(
        tuple(names), tuple(float(m) for m in mean), cov, ea_lower, shared_weight, estimates
    )


def degradant(
    name: str,
    *,
    yield_mol: float = 1.0,
    molar_mass: float | None = None,
    limit: float | None = 1.0,
    toxicity: str = "unknown",
) -> DegradantSpec:
    return DegradantSpec(
        degradant_id=f"deg_{name}",
        name=name,
        smiles=None,
        formed_by_class_id=name,
        yield_mol_per_mol=yield_mol,
        molar_mass_g_per_mol=molar_mass,
        limit_percent=limit,
        toxicity_flag=toxicity,  # type: ignore[arg-type]
        product_specific=False,
        source_ids=(),
    )


def pathway(
    pathway_id: str,
    params: ParameterBlock,
    *,
    driver: Driver = "thermal",
    role: PathwayRole = "destructive",
    mechanism: Mechanism = "hydrolysis",
    validated: ValidatedRange = THERMAL_RANGE,
    degradant_spec: DegradantSpec | None = None,
) -> Pathway:
    ingredient, class_id = pathway_id.split(":")
    return Pathway(
        pathway_id, ingredient, class_id, mechanism, driver, role, (), params, validated, degradant_spec, None
    )


def model(
    pathways: Sequence[Pathway],
    *,
    product_id: str = "test_product",
    ingredient_id: str = "api",
    family: DoseFormFamily = "solid",
    kind: RateLawKind = "F1",
    order_n: float | None = None,
    alpha0: float | None = None,
    avrami_n: float | None = None,
    f0: float = 1.0,
    freezing: float | None = None,
    phase_change: float | None = None,
    deliquescence: float | None = None,
    max_study_s: float | None = None,
    measured_model_id: str | None = None,
) -> StabilityModel:
    return StabilityModel(
        model_id=f"{product_id}:{ingredient_id}:test",
        product_id=product_id,
        ingredient_id=ingredient_id,
        dose_form_family=family,
        rate_law_kind=kind,
        order_n=order_n,
        alpha0=alpha0,
        avrami_n=avrami_n,
        reference_temperature_k=298.15,
        reference_rh_percent=40.0,
        pathways=tuple(sorted(pathways, key=lambda p: p.pathway_id)),
        measured_model_id=measured_model_id,
        max_study_duration_s=max_study_s,
        initial_active_fraction=f0,
        freezing_point_k=freezing,
        phase_change_temperature_k=phase_change,
        deliquescence_rh_percent=deliquescence,
        source_ids=(),
    )


def thermal_model(
    *,
    ln_k: float = -19.0,
    ea: float = 90000.0,
    b: float | None = 0.03,
    product_id: str = "test_product",
    ingredient_id: str = "api",
    kind: RateLawKind = "F1",
    sd: float = 0.3,
    layer: SourceLayer = "class_prior",
    with_radiolysis: bool = True,
) -> StabilityModel:
    """One thermal destructive pathway (with or without a humidity term) and, optionally, a
    radiolysis pathway, on the ingredient ``ingredient_id``."""
    names: list[ParameterName] = ["ln_k_ref_per_s", "ea_j_per_mol"]
    mean = [ln_k, ea]
    sds = [sd, 1e4]
    if b is not None:
        names.append("b_per_percent_rh")
        mean.append(b)
        sds.append(0.005)
    paths = [
        pathway(
            f"{ingredient_id}:amide_hydrolysis",
            block(names, mean, sds, layer=layer),
            degradant_spec=degradant("amide_hydrolysis", molar_mass=109.13, limit=0.15),
        )
    ]
    if with_radiolysis:
        paths.append(
            pathway(
                f"{ingredient_id}:radiolysis",
                block(["ln_k_dose_per_gy"], [-14.0], [1.0]),
                driver="radiation",
                mechanism="radiolysis",
                validated=WIDE_RANGE,
            )
        )
    return model(paths, product_id=product_id, ingredient_id=ingredient_id, kind=kind)


def segment(
    t0: float,
    t1: float,
    *,
    zone: str = "station_kit",
    package: str | None = "original_blister",
    temperature: float = 298.15,
    rh: float = 50.0,
    rh_low: float | None = None,
    dose: float = 0.0,
    lux: float = 0.0,
    uv: float = 0.0,
    gaps: tuple[Quantity, ...] = (),
    declared: tuple[Quantity, ...] = (),
) -> Segment:
    return Segment(
        t0,
        t1,
        zone,
        package,
        temperature,
        rh,
        rh if rh_low is None else rh_low,
        dose,
        lux,
        uv,
        gaps,
        declared,
    )


def lot_exposure(segments: Sequence[Segment], lot_id: str = "lot-test") -> LotExposure:
    t0 = segments[0].t0_s if segments else 0.0
    t1 = segments[-1].t1_s if segments else 0.0
    return LotExposure(lot_id, t0, t1, tuple(segments), (), False)


def fixed_draws(
    test_model: StabilityModel,
    zone_ids: Sequence[str],
    *,
    n: int = 1,
    c0: float = 100.0,
    overrides: Mapping[str, FloatArray | Mapping[str, FloatArray]] | None = None,
) -> DrawBlock:
    """A DrawBlock at the block means (zone multipliers 0, offsets 0), with per-name
    overrides of shape (n, P) or (n,)."""
    count = len(test_model.pathways)
    columns: dict[str, FloatArray] = {
        name: np.zeros((n, count))
        for name in (
            "ln_k_ref_per_s",
            "ea_j_per_mol",
            "b_per_percent_rh",
            "ln_k_vis_per_lux_h",
            "ln_k_uv_per_w_h_per_m2",
            "ln_k_dose_per_gy",
            "ln_k_dose_frozen_per_gy",
            "ln_frozen_multiplier",
            "extrapolation_z_temperature",
            "extrapolation_z_humidity",
        )
    }
    target = {"ln_frozen_rate_multiplier": "ln_frozen_multiplier"}
    for p, path in enumerate(test_model.pathways):
        for index, name in enumerate(path.parameters.names):
            columns[target.get(name, name)][:, p] = path.parameters.mean[index]
        if "ln_k_dose_frozen_per_gy" not in path.parameters.names:
            columns["ln_k_dose_frozen_per_gy"][:, p] = columns["ln_k_dose_per_gy"][:, p]
    zero = np.zeros(n)
    values: dict[str, object] = {
        "n": n,
        "c0_percent": np.full(n, c0),
        "z_c0": zero.copy(),
        **columns,
        "zone_ln_multiplier": {z: zero.copy() for z in sorted(zone_ids)},
        "temperature_offset_k": {z: zero.copy() for z in sorted(zone_ids)},
        "rh_offset_percent": {z: zero.copy() for z in sorted(zone_ids)},
    }
    for key, value in (overrides or {}).items():
        values[key] = value
    return DrawBlock(**values)  # type: ignore[arg-type]


def constant_series(
    series_id: str,
    sensor_id: str,
    zone_id: str,
    quantity: Quantity,
    unit: str,
    start_utc: str,
    end_utc: str,
    step_s: float,
    value: float | Sequence[float],
) -> SensorSeries:
    """Readings every ``step_s`` seconds on [start, end] at ``value`` (a constant or a cycle
    of values)."""
    t0 = iso_to_seconds(start_utc)
    t1 = iso_to_seconds(end_utc)
    cycle = [float(value)] if isinstance(value, int | float) else [float(v) for v in value]
    points: list[SensorPoint] = []
    count = math.floor((t1 - t0) / step_s) + 1
    for index in range(count):
        t = t0 + index * step_s
        points.append(SensorPoint(seconds_to_iso(t), t, cycle[index % len(cycle)]))
    return SensorSeries(series_id, sensor_id, zone_id, quantity, unit, tuple(points))


def bundle_with(bundle: ConfigBundle, member: str, data: JSONObject) -> ConfigBundle:
    """A copy of ``bundle`` with one document's data replaced (tests only; the hash follows
    the new data so pins stay honest)."""
    old = bundle.documents[member]
    new = dataclasses.replace(old, data=data, sha256=canonical_sha256(data))
    documents: dict[str, ConfigDocument] = dict(bundle.documents)
    documents[member] = new
    return ConfigBundle(documents=documents)


def pins(seed_hex: str = "0" * 32) -> Pins:
    """Placeholder pins for hand-built decision inputs."""
    return Pins("0.1.0", (), (), "0" * 64, seed_hex, "numpy.PCG64", "2", "2026", "0.8", ())


def potency_estimate(
    ingredient_id: str,
    q_lo: float,
    q_hi: float,
    *,
    lot_id: str = "lot-1",
    product_id: str = "test_product",
    as_of_utc: str = "2028-06-01T00:00:00Z",
    limits: tuple[float, float] = (90.0, 110.0),
    flags: Sequence[EstimateFlag] = (),
    degradants: Sequence[tuple[str, float | None, str]] = (),
    placards: Sequence[Placard] = (),
    inspection: InspectionFinding | None = None,
    assays: Sequence[str] = (),
) -> PotencyEstimate:
    """A PotencyEstimate with the given 0.05 and 0.95 quantiles (and the median between
    them). ``degradants`` are (degradant_id, p_above_limit, toxicity_flag)."""
    as_of_s = iso_to_seconds(as_of_utc)
    middle = 0.5 * (q_lo + q_hi)
    quantiles = (QuantileValue(0.05, q_lo), QuantileValue(0.5, middle), QuantileValue(0.95, q_hi))
    exposure = ExposureSummary(
        as_of_utc, as_of_utc, 0.0, None, 83144.0, (), None, None, None, (), 0.0, 0.0, 0.0, (), 0.0
    )
    radiation = RadiationReport(0.0, False, 0.0, 0.0, 0.0, 10.0, "none", False, 0.9, False)
    return PotencyEstimate(
        estimate_id=f"{lot_id}:{ingredient_id}:{as_of_utc}:estimate",
        lot_id=lot_id,
        product_id=product_id,
        ingredient_id=ingredient_id,
        as_of_utc=as_of_utc,
        as_of_s=as_of_s,
        unit="percent_label_claim",
        quantiles=quantiles,
        mean=middle,
        standard_uncertainty=0.25 * (q_hi - q_lo),
        coverage_interval=CoverageInterval(0.9, q_lo, q_hi, "probabilistically_symmetric"),
        total_quantiles=None,
        specification=SpecificationLimits(*limits),
        p_within_spec=1.0,
        p_below_lower=0.0,
        p_above_upper=0.0,
        degradants=tuple(
            DegradantEstimate(
                d_id,
                d_id,
                f"{ingredient_id}:x",
                "percent_of_label_api_mass",
                (),
                0.0,
                None if p is None else 1.0,
                p,
                tox,  # type: ignore[arg-type]
                False,
            )
            for d_id, p, tox in degradants
        ),
        radiation=radiation,
        exposure=exposure,
        flags=tuple(flags),
        estimate_basis="class_prior",
        prior_basis="class_prior",
        assay_ids_used=tuple(assays),
        inspection_ids_used=(),
        inspection_override=inspection,
        pathway_contributions=(),
        placards=tuple(placards),
        monte_carlo=MonteCarloReport(20000, 2, 10000, True, 2, 0.005, 0.9, None, "0" * 32),
        pins=pins(),
        trace=(),
    )


def forecast(
    ingredient_id: str,
    crossing_s: float | None,
    *,
    lot_id: str = "lot-1",
    as_of_utc: str = "2028-06-01T00:00:00Z",
    probability: float = 0.05,
) -> Forecast:
    as_of_s = iso_to_seconds(as_of_utc)
    status: ForecastStatus = (
        "beyond_horizon"
        if crossing_s is None
        else "already_crossed"
        if crossing_s == as_of_s
        else "within_horizon"
    )
    return Forecast(
        forecast_id=f"{lot_id}:{ingredient_id}:{as_of_utc}:forecast",
        lot_id=lot_id,
        ingredient_id=ingredient_id,
        as_of_utc=as_of_utc,
        as_of_s=as_of_s,
        quantile_probability=probability,
        lower_limit_percent=90.0,
        projection=Projection("repeat_last_window", 2592000.0, "station_kit", "original_blister"),
        status=status,
        crossing_utc=None if crossing_s is None else seconds_to_iso(crossing_s),
        crossing_s=crossing_s,
        horizon_s=315360000.0,
        crossing_delay_quantiles_s=(),
        pins=pins(),
    )
