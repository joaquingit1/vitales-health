"""Pipeline: one call from configuration and recorded inputs to ordered lot decisions.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 12.

Stages in order (spec 12.2): validate inputs; exposure per lot; microclimate; stability
model per ingredient (chemistry.parameters); seed; adaptive Monte Carlo with kinetics;
assay update; inspection override; estimate assembly (quantiles, basis, flags, placards,
trace, pins); forecasts; decisions; ordering. Everything is a pure function of the
configuration bundle, the inputs and as_of: the same pins reproduce every output bit for
bit (decision 12).
"""

import dataclasses
import importlib.metadata
import math
from collections.abc import Mapping, Sequence
from typing import Final, Literal, cast

import numpy as np
from numpy.typing import NDArray

import pharmacy
from pharmacy import decision, exposure, kinetics, microclimate, uncertainty, update
from pharmacy.chemistry import parameters, radiolysis
from pharmacy.config import ConfigBundle, canonical_json_bytes, canonical_sha256, validate_instance
from pharmacy.errors import (
    ComputationError,
    ConfigReferenceError,
    DeterminismError,
    InputError,
    ObservationError,
    SensorDataError,
)
from pharmacy.kinetics import DrawBlock, FloatArray, PartialSegment
from pharmacy.types import (
    ActiveIngredientDoc,
    AssayObservation,
    CoverageInterval,
    DegradantEstimate,
    EngineInputsDoc,
    EngineSettingsDoc,
    EstimateFlag,
    Forecast,
    ForecastStatus,
    InspectionObservation,
    InventoryAssessment,
    JSONValue,
    LotAssessment,
    LotDoc,
    LotExposure,
    MicroclimateTrace,
    MonteCarloReport,
    PackageDoc,
    PathwayContribution,
    Pins,
    Placard,
    PotencyEstimate,
    ProductDoc,
    Projection,
    ProjectionKind,
    RadiationReport,
    Segment,
    SensorPoint,
    SensorSeries,
    SeriesPin,
    SourcedValue,
    SpecificationLimits,
    StabilityModel,
    StorageZoneDoc,
    TraceEntry,
    assay_from_record,
    inspection_from_record,
    iso_to_seconds,
    seconds_to_iso,
    sensor_series_from_json,
    to_json,
)

_TIME_AGREEMENT_S: Final[float] = 1e-3
"""ISO and float timestamps must agree within 1 ms (spec 1.2)."""

_RNG: Final = "numpy.PCG64"

_FORECAST_CHUNK: Final[int] = 2048
"""Draws marched together through the forecast projection (memory bound only: every draw is
evaluated independently, so the chunk size never changes a result)."""

_FLAG_ORDER: Final[tuple[EstimateFlag, ...]] = (
    "outside_validated_range",
    "coverage_low",
    "computed_only",
    "mc_not_converged",
    "assay_model_conflict",
    "assay_reanchored",
    "inspection_override",
    "regime_switch",
    "radiosensitive_dose_exceeded",
    "degradant_profile_not_reviewed",
)

_ESTIMATE_SCHEMA: Final[str] = "pharmacy/potency-estimate.schema.json"
_INPUTS_SCHEMA: Final[str] = "pharmacy/inputs.schema.json"


# =========================================================================================
# Configuration views
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Config:
    bundle: ConfigBundle
    settings: EngineSettingsDoc
    zones: Mapping[str, StorageZoneDoc]
    packages: Mapping[str, PackageDoc]
    lots: Mapping[str, LotDoc]
    products: Mapping[str, ProductDoc]


def _config(bundle: ConfigBundle) -> _Config:
    return _Config(
        bundle=bundle,
        settings=bundle.settings,
        zones={zone["zone_id"]: zone for zone in bundle.zones["zones"]},
        packages={package["package_id"]: package for package in bundle.packages["packages"]},
        lots={lot["lot_id"]: lot for lot in bundle.lots["lots"]},
        products={product["product_id"]: product for product in bundle.formulary["products"]},
    )


def _lot(config: _Config, lot_id: str) -> LotDoc:
    if lot_id not in config.lots:
        raise InputError(f"unknown lot {lot_id!r}")
    return config.lots[lot_id]


def _product(config: _Config, lot: LotDoc) -> ProductDoc:
    if lot["product_id"] not in config.products:
        raise ConfigReferenceError(
            "lots", [f"lot {lot['lot_id']!r} names unknown product {lot['product_id']!r}"]
        )
    return config.products[lot["product_id"]]


def _ingredient(product: ProductDoc, ingredient_id: str) -> ActiveIngredientDoc:
    for ingredient in product["ingredients"]:
        if ingredient["ingredient_id"] == ingredient_id:
            return ingredient
    raise InputError(f"product {product['product_id']!r} has no ingredient {ingredient_id!r}")


def _as_of(as_of_utc: str) -> float:
    try:
        return iso_to_seconds(as_of_utc)
    except ValueError as error:
        raise InputError(f"as_of_utc is not an ISO 8601 UTC instant: {as_of_utc!r}") from error


def _seconds(text: str, label: str, error: type[InputError]) -> float:
    try:
        return iso_to_seconds(text)
    except ValueError as exc:
        raise error(f"{label}: {text!r} is not an ISO 8601 UTC instant") from exc


def _check_instant(text: str, seconds: float, label: str, error: type[InputError]) -> None:
    """Spec 1.2: the ISO string and the float seconds must agree within 1 ms."""
    if not math.isfinite(seconds) or abs(_seconds(text, label, error) - seconds) > _TIME_AGREEMENT_S:
        raise error(f"{label}: {text!r} and {seconds!r} differ by more than 1 ms")


def _library_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise InputError(f"library {name!r} is not installed; its version is part of every pin") from error


# =========================================================================================
# Input validation (spec 12.1)
# =========================================================================================


def validate_inputs(
    bundle: ConfigBundle, inputs: EngineInputsDoc
) -> tuple[tuple[SensorSeries, ...], tuple[AssayObservation, ...], tuple[InspectionObservation, ...]]:
    """Validate an inputs document (schema ``pharmacy/inputs.schema.json`` and the
    semantic rules of spec 12.1: series reference known zones and sensors, observations
    reference known lots and ingredients, observation times are at or before as_of and
    after manufacture) and convert it to runtime types. Readings after as_of are dropped
    (never used), not rejected. Raises ``ConfigSchemaError``, ``SensorDataError`` or
    ``ObservationError``.

    Observations after as_of are dropped like readings (spec 1.2: never used, never
    changing a past result; docs/requests/B1.md R-13). Outputs are sorted by id."""
    validate_instance(cast(JSONValue, inputs), _INPUTS_SCHEMA, "inputs")
    config = _config(bundle)
    as_of_s = _as_of(inputs["as_of_utc"])
    series: list[SensorSeries] = []
    seen: set[str] = set()
    for record in inputs["sensor_series"]:
        try:
            item = sensor_series_from_json(cast(Mapping[str, JSONValue], record))
        except ValueError as error:
            raise SensorDataError(f"series {record['series_id']!r}: {error}") from error
        if item.series_id in seen:
            raise SensorDataError(f"duplicate series id {item.series_id!r}")
        seen.add(item.series_id)
        if item.zone_id not in config.zones:
            raise SensorDataError(f"series {item.series_id!r} names unknown zone {item.zone_id!r}")
        sensors = [s for s in config.zones[item.zone_id]["sensors"] if s["sensor_id"] == item.sensor_id]
        if not sensors:
            raise SensorDataError(f"series {item.series_id!r} names unknown sensor {item.sensor_id!r}")
        if sensors[0]["quantity"] != item.quantity:
            raise SensorDataError(
                f"series {item.series_id!r} is {item.quantity!r} but sensor {item.sensor_id!r} measures "
                f"{sensors[0]['quantity']!r}"
            )
        for point in item.points:
            _check_instant(point.t_utc, point.t_s, f"series {item.series_id!r}", SensorDataError)
        series.append(_until(item, as_of_s))
    assays: list[AssayObservation] = []
    for assay_record in inputs["assays"]:
        assay = assay_from_record(assay_record)
        _check_observation(config, assay.assay_id, assay.lot_id, assay.measured_at_utc, assay.measured_at_s)
        lot = config.lots[assay.lot_id]
        product = _product(config, lot)
        if assay.ingredient_id not in {i["ingredient_id"] for i in product["ingredients"]}:
            raise ObservationError(
                f"assay {assay.assay_id!r} names unknown ingredient {assay.ingredient_id!r}"
            )
        update.total_sd_percent(assay, config.settings["update"])
        if assay.measured_at_s <= as_of_s:
            assays.append(assay)
    inspections: list[InspectionObservation] = []
    for inspection_record in inputs["inspections"]:
        inspection = inspection_from_record(inspection_record)
        _check_observation(
            config,
            inspection.inspection_id,
            inspection.lot_id,
            inspection.inspected_at_utc,
            inspection.inspected_at_s,
        )
        if inspection.inspected_at_s <= as_of_s:
            inspections.append(inspection)
    for label, ids in (
        ("assay", [a.assay_id for a in assays]),
        ("inspection", [i.inspection_id for i in inspections]),
    ):
        if len(set(ids)) != len(ids):
            raise ObservationError(f"duplicate {label} id")
    return (
        tuple(sorted(series, key=lambda s: s.series_id)),
        tuple(sorted(assays, key=lambda a: a.assay_id)),
        tuple(sorted(inspections, key=lambda i: i.inspection_id)),
    )


def _check_observation(config: _Config, observation_id: str, lot_id: str, t_utc: str, t_s: float) -> None:
    if lot_id not in config.lots:
        raise ObservationError(f"observation {observation_id!r} names unknown lot {lot_id!r}")
    _check_instant(t_utc, t_s, f"observation {observation_id!r}", ObservationError)
    manufacture = iso_to_seconds(config.lots[lot_id]["manufactured_at_utc"])
    if t_s < manufacture:
        raise ObservationError(f"observation {observation_id!r} precedes the manufacture of lot {lot_id!r}")


def _until(series: SensorSeries, as_of_s: float) -> SensorSeries:
    """The series with readings after as_of dropped (spec 1.2, 12.1)."""
    kept: tuple[SensorPoint, ...] = tuple(p for p in series.points if p.t_s <= as_of_s)
    return series if len(kept) == len(series.points) else dataclasses.replace(series, points=kept)


# =========================================================================================
# Lot history at as_of (spec 12.2 steps 2 and 3)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _History:
    lot: LotDoc
    product: ProductDoc
    as_of_utc: str
    as_of_s: float
    exposure: LotExposure
    trace: MicroclimateTrace
    steps: Mapping[str, tuple[float, ...]]
    zone_ids: tuple[str, ...]
    current_zone: str
    current_package: str
    package_ids: tuple[str, ...]
    first_tracks_outside: bool
    series_pins: tuple[SeriesPin, ...]
    observations_sha256: str
    summary_zone: str


def _current(lot: LotDoc, as_of_s: float) -> tuple[str, str]:
    """The zone and package holding the lot at as_of (the last intervals started at or
    before it)."""
    zone = lot["custody"][0]["zone_id"]
    for interval in lot["custody"]:
        if iso_to_seconds(interval["from_utc"]) <= as_of_s:
            zone = interval["zone_id"]
    package = lot["package_history"][0]["package_id"]
    for entry in lot["package_history"]:
        if iso_to_seconds(entry["from_utc"]) <= as_of_s:
            package = entry["package_id"]
    return zone, package


def _observations_sha256(
    lot_id: str,
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
    as_of_s: float,
) -> str:
    """Spec 1.4: sha256 of {"assays", "inspections"} holding the lot's records (all
    ingredients) at or before as_of, each list sorted by id."""
    material: JSONValue = {
        "assays": [
            to_json(a)
            for a in sorted(assays, key=lambda a: a.assay_id)
            if a.lot_id == lot_id and a.measured_at_s <= as_of_s
        ],
        "inspections": [
            to_json(i)
            for i in sorted(inspections, key=lambda i: i.inspection_id)
            if i.lot_id == lot_id and i.inspected_at_s <= as_of_s
        ],
    }
    return canonical_sha256(material)


def _history(
    config: _Config,
    lot_id: str,
    as_of_utc: str,
    series: Sequence[SensorSeries],
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
) -> _History:
    lot = _lot(config, lot_id)
    product = _product(config, lot)
    as_of_s = _as_of(as_of_utc)
    windows = exposure.custody_windows(lot, as_of_s)
    current_zone, current_package = _current(lot, as_of_s)
    zone_ids = tuple(sorted({zone_id for zone_id, _, _ in windows} or {current_zone}))
    for zone_id in zone_ids:
        if zone_id not in config.zones:
            raise ConfigReferenceError("lots", [f"lot {lot_id!r} names unknown zone {zone_id!r}"])
    kept = [
        _until(item, as_of_s)
        for item in sorted(series, key=lambda s: s.series_id)
        if item.zone_id in zone_ids
    ]
    by_zone: dict[str, list[SensorSeries]] = {zone_id: [] for zone_id in zone_ids}
    for item in kept:
        by_zone[item.zone_id].append(item)
    lot_exposure = exposure.build_lot_exposure(lot, config.zones, config.packages, by_zone, as_of_s)
    segments = lot_exposure.segments
    trace = microclimate.internal_rh_trace(segments, config.packages)
    tracks = microclimate.initial_rh_of_first_package(segments, config.packages) is None
    steps = {
        zone_id: microclimate.unit_step_response(
            segments, trace.time_constant_s, zone_id, first_start_tracks_outside=tracks
        )
        for zone_id in zone_ids
    }
    package_ids = tuple(
        sorted({p for p, _, _ in exposure.package_windows(lot, as_of_s)} or {current_package})
    )
    return _History(
        lot=lot,
        product=product,
        as_of_utc=as_of_utc,
        as_of_s=as_of_s,
        exposure=lot_exposure,
        trace=trace,
        steps=steps,
        zone_ids=zone_ids,
        current_zone=current_zone,
        current_package=current_package,
        package_ids=package_ids,
        first_tracks_outside=tracks,
        series_pins=tuple(exposure.series_pin(item, as_of_s) for item in kept),
        observations_sha256=_observations_sha256(lot_id, assays, inspections, as_of_s),
        summary_zone=current_zone,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Prefix:
    exposure: LotExposure
    trace: MicroclimateTrace
    steps: Mapping[str, tuple[float, ...]]


def _prefix(config: _Config, history: _History, t_s: float) -> _Prefix:
    """The lot history up to an assay time (segments clipped at t_s)."""
    segments: list[Segment] = []
    for segment in history.exposure.segments:
        if segment.t0_s >= t_s:
            break
        segments.append(segment if segment.t1_s <= t_s else dataclasses.replace(segment, t1_s=t_s))
    lot_exposure = LotExposure(
        lot_id=history.exposure.lot_id,
        t_start_s=history.exposure.t_start_s,
        t_end_s=t_s,
        segments=tuple(segments),
        zone_histories=(),
        coverage_low=history.exposure.coverage_low,
    )
    trace = microclimate.internal_rh_trace(segments, config.packages)
    steps = {
        zone_id: microclimate.unit_step_response(
            segments, trace.time_constant_s, zone_id, first_start_tracks_outside=history.first_tracks_outside
        )
        for zone_id in history.zone_ids
    }
    return _Prefix(exposure=lot_exposure, trace=trace, steps=steps)


# =========================================================================================
# Monte Carlo per ingredient (spec 12.2 steps 4 to 8)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Run:
    """Everything an estimate computed that a forecast reuses."""

    history: _History
    ingredient: ActiveIngredientDoc
    model: StabilityModel
    seed: int
    initial_mean: float
    initial_sd: float
    adaptive: uncertainty.AdaptiveResult
    weights: FloatArray | None
    assays_used: tuple[str, ...]
    reanchored: bool
    conflict: bool
    smallest_tail: float | None
    tail_probabilities: tuple[tuple[str, float], ...]
    draw_blocks: tuple[DrawBlock, ...]
    compression: tuple[kinetics.CheckedDrawResult, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Estimated:
    run: _Run
    estimate: PotencyEstimate


def _initial_assay(
    config: _Config, lot: LotDoc, ingredient_id: str
) -> tuple[float, float, SourcedValue, SourcedValue]:
    for entry in lot["initial_assay"]:
        if entry["ingredient_id"] == ingredient_id:
            return (
                float(entry["mean_percent"]["value"]),
                float(entry["sd_percent"]["value"]),
                entry["mean_percent"],
                entry["sd_percent"],
            )
    raise ConfigReferenceError("lots", [f"lot {lot['lot_id']!r} has no initial assay of {ingredient_id!r}"])


def _assays_for(
    history: _History, ingredient_id: str, assays: Sequence[AssayObservation]
) -> tuple[AssayObservation, ...]:
    """Assays of the lot and ingredient in [manufacture, as_of] (spec 10.1)."""
    manufacture = iso_to_seconds(history.lot["manufactured_at_utc"])
    chosen: list[AssayObservation] = []
    for assay in sorted(assays, key=lambda a: a.assay_id):
        if assay.lot_id != history.lot["lot_id"] or assay.ingredient_id != ingredient_id:
            continue
        if assay.measured_at_s < manufacture:
            raise ObservationError(f"assay {assay.assay_id!r} precedes the manufacture of the lot")
        if assay.measured_at_s <= history.as_of_s:
            chosen.append(assay)
    return tuple(chosen)


def _seed_material(history: _History, ingredient_id: str, purpose: str, config: _Config) -> JSONValue:
    return {
        "engine_version": pharmacy.__version__,
        "config": to_json(config.bundle.pins()),
        "sensor_series": to_json(history.series_pins),
        "observations_sha256": history.observations_sha256,
        "lot_id": history.lot["lot_id"],
        "ingredient_id": ingredient_id,
        "as_of_utc": history.as_of_utc,
        "purpose": purpose,
    }


def _pins(config: _Config, history: _History, seed: int, cache_keys: Sequence[str]) -> Pins:
    return Pins(
        engine_version=pharmacy.__version__,
        config=config.bundle.pins(),
        sensor_series=history.series_pins,
        observations_sha256=history.observations_sha256,
        seed_hex=uncertainty.seed_hex(seed),
        rng=_RNG,
        numpy_version=np.__version__,
        rdkit_version=_library_version("rdkit"),
        tblite_version=_library_version("tblite"),
        computed_cache_keys=tuple(sorted(set(cache_keys))),
    )


def _degradant_columns(model: StabilityModel, settings: EngineSettingsDoc) -> tuple[int, ...]:
    return tuple(
        p
        for p, pathway in enumerate(model.pathways)
        if pathway.degradant is not None
        and pathway.role == "destructive"
        and (pathway.driver != "radiation" or settings["radiation"]["include_in_potency"])
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Evaluation:
    """The per-batch work of the first (weighted) and second (re-anchored) passes."""

    config: _Config
    history: _History
    model: StabilityModel
    initial_mean: float
    initial_sd: float
    assays: tuple[AssayObservation, ...]
    prefixes: tuple[_Prefix, ...]
    reanchor: update.ReanchorPlan | None
    anchor_prefix: _Prefix | None
    anchor_measurand: str
    retained_draws: list[DrawBlock] = dataclasses.field(default_factory=list)
    compression: list[kinetics.CheckedDrawResult] = dataclasses.field(default_factory=list)
    exact_only: bool = False

    def draws(self, generator: np.random.Generator) -> DrawBlock:
        batch = uncertainty.jcgm_batch_size(
            float(self.config.settings["monte_carlo"]["coverage_probability"]["value"])
        )
        return uncertainty.draw_parameters(
            self.model,
            self.initial_mean,
            self.initial_sd,
            self.config.zones,
            self.history.zone_ids,
            generator,
            batch,
        )

    def evaluate(self, prefix: _Prefix | None, draws: DrawBlock) -> kinetics.DrawResult:
        source = (
            prefix
            if prefix is not None
            else _Prefix(self.history.exposure, self.history.trace, self.history.steps)
        )
        args = (
            self.model,
            source.exposure,
            source.trace,
            source.steps,
            draws,
            self.config.zones,
            self.config.packages,
            self.config.settings,
        )
        if self.exact_only:
            return kinetics.evaluate_draws(*args)
        checked = kinetics.evaluate_draws_checked(*args)
        self.compression.append(checked)
        return checked.result

    def __call__(self, batch: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        settings = self.config.settings
        draws = self.draws(generator)
        self.retained_draws.append(draws)
        result = self.evaluate(None, draws)
        theta_d, theta_r, _ = kinetics.pathway_sums(self.model, result.theta, settings)
        c0 = draws.c0_percent
        extras: dict[str, FloatArray] = {}
        log_weights: FloatArray | None = None
        if self.reanchor is not None and self.anchor_prefix is not None:
            # Spec 10.4: the level at t_a* becomes y* = y_a* + sd_total z_C0 and
            # C0' = y* / ((1 - alpha(t_a*)) rho(t_a*)), rho only for an active measurand.
            anchored = self.evaluate(self.anchor_prefix, draws)
            level = self.reanchor.mean_percent + self.reanchor.sd_percent * draws.z_c0
            remaining = 1.0 - anchored.alpha
            if self.anchor_measurand == "active":
                remaining = remaining * anchored.active_fraction
            with np.errstate(divide="ignore", invalid="ignore"):
                c0 = level / remaining
            if not bool(np.all(np.isfinite(c0))) or bool(np.any(c0 <= 0.0)):
                raise ComputationError("the re-anchored initial content C0' must be positive and finite")
        elif self.assays:
            predictions = []
            for assay, prefix in zip(self.assays, self.prefixes, strict=True):
                predicted = self.evaluate(prefix, draws)
                values = (
                    predicted.potency_active_percent
                    if assay.measurand == "active"
                    else predicted.potency_total_percent
                )
                predictions.append(update.AssayPrediction(assay.assay_id, values))
                extras[f"prediction:{assay.assay_id}"] = values
            log_weights = update.assay_log_likelihood(self.assays, predictions, settings["update"])
        _, total, active = kinetics.potency_from_thetas(self.model, c0, theta_d, theta_r, settings)
        scale = c0 / draws.c0_percent
        for p in _degradant_columns(self.model, settings):
            extras[f"degradant:{p}"] = result.degradant_percent[:, p] * scale
        extras["total"] = total
        extras["theta_destructive"] = theta_d
        extras["theta_racemization"] = theta_r
        extras["c0_effective"] = c0
        extras["radiation_loss"] = result.radiation_loss_fraction
        return uncertainty.BatchOutcome(values=active, log_weights=log_weights, extras=extras)


def _model_for(
    config: _Config, history: _History, ingredient_id: str, model: StabilityModel | None
) -> StabilityModel:
    if model is None:
        # Module attribute on purpose: tests monkeypatch parameters.build_stability_model.
        model = parameters.build_stability_model(
            config.bundle, history.product["product_id"], ingredient_id, list(history.package_ids)
        )
    if model.ingredient_id != ingredient_id or model.product_id != history.product["product_id"]:
        raise InputError(
            f"the stability model is for {model.product_id!r}/{model.ingredient_id!r}, not "
            f"{history.product['product_id']!r}/{ingredient_id!r}"
        )
    return model


def _run(
    config: _Config,
    history: _History,
    ingredient_id: str,
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
    model: StabilityModel | None,
) -> _Estimated:
    settings = config.settings
    ingredient = _ingredient(history.product, ingredient_id)
    resolved = _model_for(config, history, ingredient_id, model)
    initial_mean, initial_sd, mean_value, sd_value = _initial_assay(config, history.lot, ingredient_id)
    used = _assays_for(history, ingredient_id, assays)
    seed = uncertainty.derive_seed(_seed_material(history, ingredient_id, "potency_estimate", config))
    update_settings = settings["update"]
    mc_settings = settings["monte_carlo"]
    evaluation = _Evaluation(
        config=config,
        history=history,
        model=resolved,
        initial_mean=initial_mean,
        initial_sd=initial_sd,
        assays=used,
        prefixes=tuple(_prefix(config, history, a.measured_at_s) for a in used),
        reanchor=None,
        anchor_prefix=None,
        anchor_measurand="total",
    )
    ess_floor = float(update_settings["ess_floor"]["value"])
    threshold = float(update_settings["conflict_tail_probability"]["value"])

    def initial_conflict(extras: Mapping[str, FloatArray]) -> bool:
        return any(
            update.conflict_probability(
                assay, extras[f"prediction:{assay.assay_id}"], update.total_sd_percent(assay, update_settings)
            )
            < threshold
            for assay in used
        )

    adaptive = uncertainty.run_adaptive(
        evaluation,
        seed,
        mc_settings,
        ess_floor=ess_floor if used else None,
        stop_after_initial=initial_conflict if used else None,
    )
    weights: FloatArray | None = None
    reanchored = False
    conflict = False
    tails: list[tuple[str, float]] = []
    assays_used = tuple(a.assay_id for a in used)
    if used:
        assert adaptive.log_weights is not None
        weights = update.normalized_weights(adaptive.log_weights)
        threshold = float(update_settings["conflict_tail_probability"]["value"])
        for assay in used:
            tail = update.conflict_probability(
                assay,
                adaptive.extras[f"prediction:{assay.assay_id}"][
                    : adaptive.batch_size * int(mc_settings["min_batches"]["value"])
                ],
                update.total_sd_percent(assay, update_settings),
            )
            tails.append((assay.assay_id, tail))
            conflict = conflict or tail < threshold
        starved = (
            adaptive.n_batches == int(mc_settings["max_batches"]["value"])
            and update.effective_sample_size(weights) < ess_floor
        )
        if conflict or starved:
            plan = update.plan_reanchor(used, update_settings)
            anchor = next(a for a in used if a.assay_id == plan.assay_id)
            second = dataclasses.replace(
                evaluation,
                assays=(),
                prefixes=(),
                reanchor=plan,
                anchor_prefix=_prefix(config, history, plan.anchor_time_s),
                anchor_measurand=anchor.measurand,
                retained_draws=[],
                compression=[],
            )
            adaptive = uncertainty.run_adaptive(second, seed, mc_settings)
            evaluation = second
            weights = None
            reanchored = True
            assays_used = (plan.assay_id,)
    if any(check.fell_back for check in evaluation.compression):
        # A failed check anywhere rejects compression for the entire final estimate.
        exact_evaluation = dataclasses.replace(evaluation, exact_only=True, retained_draws=[])
        adaptive = uncertainty.run_adaptive(
            exact_evaluation, seed, mc_settings, ess_floor=ess_floor if used and not reanchored else None
        )
        weights = None if adaptive.log_weights is None else update.normalized_weights(adaptive.log_weights)
        evaluation = exact_evaluation
    smallest = min((tail for _, tail in tails), default=None)
    run = _Run(
        history=history,
        ingredient=ingredient,
        model=resolved,
        seed=seed,
        initial_mean=initial_mean,
        initial_sd=initial_sd,
        adaptive=adaptive,
        weights=weights,
        assays_used=assays_used,
        reanchored=reanchored,
        conflict=conflict,
        smallest_tail=smallest,
        tail_probabilities=tuple(tails),
        draw_blocks=tuple(evaluation.retained_draws),
        compression=tuple(evaluation.compression),
    )
    return _Estimated(run=run, estimate=_assemble(config, run, inspections, mean_value, sd_value))


# =========================================================================================
# Estimate assembly (spec 9.6, 9.10, 6, 8.4, 10.5)
# =========================================================================================


def _probabilities(settings: EngineSettingsDoc) -> tuple[float, ...]:
    reported = [float(q) for q in settings["monte_carlo"]["reported_quantiles"]]
    decision_settings = settings["decision"]
    extra = [
        float(decision_settings["lower_quantile"]["value"]),
        float(decision_settings["upper_quantile"]["value"]),
    ]
    return tuple(sorted(set(reported) | set(extra)))


def _weighted_mean(values: FloatArray, weights: FloatArray | None) -> tuple[float, float]:
    w = np.full(values.size, 1.0 / values.size) if weights is None else weights
    mean = float(np.sum(w * values))
    deviation = values - mean
    return mean, math.sqrt(float(np.sum(w * deviation * deviation)))


def _probability(mask: NDArray[np.bool_], weights: FloatArray | None) -> float:
    if weights is None:
        return float(np.count_nonzero(mask)) / mask.size
    return float(min(1.0, max(0.0, float(np.sum(np.where(mask, weights, 0.0))))))


def _sourced_trace(quantity: str, value: SourcedValue, origin: str) -> TraceEntry:
    return TraceEntry(
        quantity=quantity,
        value=float(value["value"]),
        unit=value["unit"] or "1",
        origin=origin,
        source_ids=tuple(value["source_ids"]),
        note=value["note"],
    )


def _computed_trace(quantity: str, value: float | str | None, unit: str, note: str = "") -> TraceEntry:
    return TraceEntry(quantity=quantity, value=value, unit=unit, origin="computed", source_ids=(), note=note)


def _free_share_trace(model: StabilityModel) -> list[TraceEntry]:
    """Spec 9.5: delta_i, s_m,i and pi_i = exp(delta_i) / sum_F exp(delta_j) of every free
    group member (maximum subtracted before exponentiating). The quantity names are B1's
    (docs/requests/B1.md R-17)."""
    members = [model.pathways[p] for p in uncertainty.free_group(model)]
    shares = [(p.pathway_id, p.free_share) for p in members if p.free_share is not None]
    if not shares:
        return []
    top = max(share.delta_ln for _, share in shares)
    total = math.fsum(math.exp(share.delta_ln - top) for _, share in shares)
    out: list[TraceEntry] = []
    for pathway_id, share in shares:
        out.append(_computed_trace(f"free_share_delta_ln:{pathway_id}", share.delta_ln, "1"))
        out.append(_computed_trace(f"free_share_method_sd_ln:{pathway_id}", share.method_sd_ln, "1"))
        out.append(
            _computed_trace(f"free_share_pi:{pathway_id}", math.exp(share.delta_ln - top) / total, "1")
        )
    return out


def _radiation(config: _Config, run: _Run, dose_gy: float) -> RadiationReport:
    settings = config.settings["radiation"]
    product = run.history.product
    family = product["dose_form_family"]
    k_upper = radiolysis.upper_bound_k_per_gy(config.bundle.radiation, family)
    factor = float(settings["dose_rate_safety_factor"]["value"])
    threshold = float(settings["radiosensitive_dose_threshold_gy"]["value"])
    method: Literal["g_value_direct", "g_value_indirect", "none"] = "none"
    for pathway in run.model.pathways:
        if pathway.driver == "radiation" and pathway.computed is not None:
            if pathway.computed.method == "g_value_direct":
                method = "g_value_direct"
            elif pathway.computed.method == "g_value_indirect":
                method = "g_value_indirect"
    loss = run.adaptive.extras["radiation_loss"]
    median = uncertainty.weighted_quantile(loss, run.weights, 0.5)
    return RadiationReport(
        cumulative_absorbed_dose_gy=dose_gy,
        included_in_potency=settings["include_in_potency"],
        central_loss_fraction_median=median,
        upper_bound_loss_fraction=radiolysis.upper_bound_loss_fraction(k_upper, factor, dose_gy),
        upper_bound_k_per_gy=k_upper,
        dose_rate_safety_factor=factor,
        method=method,
        radiosensitive=product["radiosensitive"],
        radiosensitive_threshold_gy=threshold,
        threshold_exceeded=radiolysis.radiosensitive_dose_exceeded(
            product["radiosensitive"], dose_gy, threshold
        ),
    )


def _degradants(
    config: _Config, run: _Run, probabilities: Sequence[float]
) -> tuple[tuple[DegradantEstimate, ...], tuple[TraceEntry, ...]]:
    api_mass = float(run.ingredient["molar_mass_g_per_mol"]["value"])
    out: list[DegradantEstimate] = []
    notes: list[TraceEntry] = []
    for p in _degradant_columns(run.model, config.settings):
        pathway = run.model.pathways[p]
        spec = pathway.degradant
        assert spec is not None
        if spec.molar_mass_g_per_mol is None:
            ratio = 1.0
            notes.append(
                _computed_trace(
                    f"degradant_mass_ratio:{spec.degradant_id}",
                    ratio,
                    "1",
                    "degradant molar mass unknown: reported on a molar basis (docs/requests/B1.md R-08)",
                )
            )
        else:
            ratio = spec.molar_mass_g_per_mol / api_mass
            notes.append(_computed_trace(f"degradant_mass_ratio:{spec.degradant_id}", ratio, "1"))
        evidence = pathway.degradant_identification
        notes.append(
            _computed_trace(
                f"degradant_identification:{spec.degradant_id}",
                "identified" if evidence.identified else "unidentified",
                "text",
                f"{evidence.evidence_kind}:{evidence.evidence_id}; sources: {', '.join(evidence.source_ids)}"
                if evidence.identified
                else "No evidence specific to this degradant's forming pathway.",
            )
        )
        values = run.adaptive.extras[f"degradant:{p}"] * ratio
        mean, _ = _weighted_mean(values, run.weights)
        limit = spec.limit_percent
        out.append(
            DegradantEstimate(
                degradant_id=spec.degradant_id,
                name=spec.name,
                pathway_id=pathway.pathway_id,
                unit="percent_of_label_api_mass",
                quantiles=uncertainty.quantiles(values, run.weights, probabilities),
                mean=mean,
                limit_percent=limit,
                p_above_limit=None if limit is None else _probability(values > limit, run.weights),
                toxicity_flag=spec.toxicity_flag,
                product_specific=spec.product_specific,
                identification=pathway.degradant_identification,
            )
        )
    return tuple(out), tuple(notes)


def _assemble(
    config: _Config,
    run: _Run,
    inspections: Sequence[InspectionObservation],
    mean_value: SourcedValue,
    sd_value: SourcedValue,
) -> PotencyEstimate:
    settings = config.settings
    history = run.history
    model = run.model
    lot_id = history.lot["lot_id"]
    ingredient_id = run.ingredient["ingredient_id"]
    zone = config.zones[history.summary_zone]
    summary = exposure.summarize_exposure(history.exposure, zone, settings)
    nominal = kinetics.nominal_thetas(
        model, history.exposure, history.trace, config.zones, config.packages, settings
    )
    assessment = kinetics.assess_validated_range(model, history.exposure, nominal, settings)
    shares = dict(
        zip(
            (p.pathway_id for p in model.pathways),
            kinetics.nominal_shares(model, nominal, settings),
            strict=True,
        )
    )
    threshold = float(settings["basis"]["share_threshold"]["value"])
    basis = parameters.basis_summary(model, shares, threshold, bool(run.assays_used))
    contributions = tuple(
        PathwayContribution(
            pathway_id=pathway.pathway_id,
            reaction_class_id=pathway.reaction_class_id,
            driver=pathway.driver,
            role=pathway.role,
            share_nominal=min(1.0, max(0.0, shares[pathway.pathway_id])),
            contributes=pathway.pathway_id in basis.contributing,
            layer=parameters.pathway_layer(pathway),
            parameter_layers=parameters.parameter_layers(pathway),
        )
        for pathway in model.pathways
    )
    probabilities = _probabilities(settings)
    values = run.adaptive.values
    weights = run.weights
    mean, u = _weighted_mean(values, weights)
    coverage_p = float(settings["monte_carlo"]["coverage_probability"]["value"])
    interval: CoverageInterval = uncertainty.coverage_interval(values, weights, coverage_p)
    lower = float(run.ingredient["specification"]["lower_percent"]["value"])
    upper = float(run.ingredient["specification"]["upper_percent"]["value"])
    racemizing = run.ingredient["racemization"] is not None
    degradants, mass_notes = _degradants(config, run, probabilities)
    radiation = _radiation(config, run, summary.cumulative_absorbed_dose_gy)
    override = update.inspection_override(inspections, lot_id, history.as_of_s)
    present: set[EstimateFlag] = set()
    if assessment.outside_validated_range:
        present.add("outside_validated_range")
    if history.exposure.coverage_low:
        present.add("coverage_low")
    if basis.computed_only:
        present.add("computed_only")
    if not run.adaptive.converged:
        present.add("mc_not_converged")
    if run.conflict:
        present.add("assay_model_conflict")
    if run.reanchored:
        present.add("assay_reanchored")
    if override is not None:
        present.add("inspection_override")
    if assessment.regime_switch:
        present.add("regime_switch")
    if radiation.threshold_exceeded:
        present.add("radiosensitive_dose_exceeded")
    if not history.product["degradant_profile_reviewed"]:
        present.add("degradant_profile_not_reviewed")
    placards = list(assessment.placards)
    if history.exposure.coverage_low:
        placards.append(
            Placard(
                kind="coverage",
                extent=summary.longest_gap_s,
                unit="s",
                duration_s=summary.longest_gap_s,
                share_nominal=0.0,
                message=(
                    f"Sensor coverage is low (longest gap {summary.longest_gap_s:.0f} s); gaps were "
                    "filled with worst-case values."
                ),
            )
        )
    if not run.adaptive.converged:
        statistics = np.array(run.adaptive.batch_statistics, dtype=np.float64)
        h = statistics.shape[0]
        spread = 2.0 * float(np.std(statistics[:, 1], ddof=1)) / math.sqrt(h) if h > 1 else 0.0
        placards.append(
            Placard(
                kind="monte_carlo",
                extent=spread,
                unit="percent",
                duration_s=0.0,
                share_nominal=0.0,
                message=(
                    f"The Monte Carlo estimate did not converge in {h} batches; the standard "
                    f"uncertainty is known to about {spread:.3g} percentage points."
                ),
            )
        )
    if run.conflict and run.smallest_tail is not None:
        placards.append(
            Placard(
                kind="assay_conflict",
                extent=run.smallest_tail,
                unit="1",
                duration_s=0.0,
                share_nominal=0.0,
                message=(
                    f"An assay conflicts with the model (tail probability {run.smallest_tail:.2e}); the "
                    "level was re-anchored to the latest assay."
                ),
            )
        )
    cache_keys = parameters.computed_cache_keys(model)
    pins = _pins(config, history, run.seed, cache_keys)
    lot_index = sorted(config.lots).index(lot_id)
    trace: list[TraceEntry] = [
        _sourced_trace(
            "initial_assay_mean_percent", mean_value, f"config/pharmacy/lots.json#/lots/{lot_index}"
        ),
        _sourced_trace("initial_assay_sd_percent", sd_value, f"config/pharmacy/lots.json#/lots/{lot_index}"),
        _sourced_trace(
            "reference_temperature_k",
            settings["reference_conditions"]["temperature_k"],
            "config/pharmacy/engine-settings.json#/reference_conditions/temperature_k",
        ),
        _sourced_trace(
            "reference_rh_percent",
            settings["reference_conditions"]["relative_humidity_percent"],
            "config/pharmacy/engine-settings.json#/reference_conditions/relative_humidity_percent",
        ),
        _computed_trace("stability_model_id", model.model_id, "text"),
        _computed_trace("rate_law_kind", model.rate_law_kind, "text"),
        _computed_trace("current_zone_id", history.current_zone, "text"),
        _computed_trace("current_package_id", history.current_package, "text"),
        _computed_trace("segments", float(len(history.exposure.segments)), "1"),
        _computed_trace(
            "history_evaluator",
            "compressed"
            if any(c.history_evaluator == "compressed" for c in run.compression)
            and not any(c.fell_back for c in run.compression)
            else "exact",
            "text",
        ),
        _computed_trace(
            "compression_probe_relative_error",
            None
            if any(c.probe_relative_error is None for c in run.compression)
            else max(
                (c.probe_relative_error for c in run.compression if c.probe_relative_error is not None),
                default=0.0,
            ),
            "1",
            "Zero exact Theta or degradant with a nonzero approximation forces exact evaluation."
            if any(c.probe_relative_error is None for c in run.compression)
            else "Maximum over every batch and assay prefix.",
        ),
        _computed_trace("compression_fell_back", float(any(c.fell_back for c in run.compression)), "1"),
        _computed_trace("numerical_tolerance", run.adaptive.numerical_tolerance, "percent"),
        *_free_share_trace(model),
        *mass_notes,
        *(
            _computed_trace(f"assay_tail_probability:{assay_id}", tail, "1")
            for assay_id, tail in run.tail_probabilities
        ),
    ]
    estimate = PotencyEstimate(
        estimate_id=f"{lot_id}:{ingredient_id}:{history.as_of_utc}",
        lot_id=lot_id,
        product_id=history.product["product_id"],
        ingredient_id=ingredient_id,
        as_of_utc=history.as_of_utc,
        as_of_s=history.as_of_s,
        unit="percent_label_claim",
        quantiles=uncertainty.quantiles(values, weights, probabilities),
        mean=mean,
        standard_uncertainty=u,
        coverage_interval=interval,
        total_quantiles=(
            uncertainty.quantiles(run.adaptive.extras["total"], weights, probabilities)
            if racemizing
            else None
        ),
        specification=SpecificationLimits(lower_percent=lower, upper_percent=upper),
        p_within_spec=_probability((values >= lower) & (values <= upper), weights),
        p_below_lower=_probability(values < lower, weights),
        p_above_upper=_probability(values > upper, weights),
        degradants=degradants,
        radiation=radiation,
        exposure=summary,
        flags=tuple(flag for flag in _FLAG_ORDER if flag in present),
        estimate_basis=basis.estimate_basis,
        prior_basis=basis.prior_basis,
        assay_ids_used=run.assays_used,
        inspection_ids_used=() if override is None else (override.inspection_id,),
        inspection_override=None if override is None else override.finding,
        pathway_contributions=contributions,
        placards=tuple(placards),
        monte_carlo=MonteCarloReport(
            n_draws=int(values.size),
            n_batches=run.adaptive.n_batches,
            batch_size=run.adaptive.batch_size,
            converged=run.adaptive.converged,
            n_dig=int(settings["monte_carlo"]["n_dig"]["value"]),
            numerical_tolerance=run.adaptive.numerical_tolerance,
            coverage_probability=coverage_p,
            effective_sample_size=None if weights is None else update.effective_sample_size(weights),
            seed_hex=uncertainty.seed_hex(run.seed),
        ),
        pins=pins,
        trace=tuple(trace),
    )
    validate_instance(to_json(estimate), _ESTIMATE_SCHEMA, estimate.estimate_id)
    return estimate


def estimate_potency(
    bundle: ConfigBundle,
    lot_id: str,
    ingredient_id: str,
    as_of_utc: str,
    series: Sequence[SensorSeries],
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
    model: StabilityModel | None = None,
) -> PotencyEstimate:
    """PotencyEstimate of one ingredient of one lot (spec 12.2 steps 2 to 9). ``model``
    overrides chemistry.parameters.build_stability_model (tests only)."""
    config = _config(bundle)
    history = _history(config, lot_id, as_of_utc, series, assays, inspections)
    return _run(config, history, ingredient_id, assays, inspections, model).estimate


# =========================================================================================
# Forecast (spec 11.4)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Projection:
    kind: ProjectionKind
    window_s: float | None
    zone_id: str
    package_id: str
    segments: tuple[Segment, ...]
    length_s: float


def _projection(config: _Config, history: _History) -> _Projection:
    """Future conditions per the current zone's forecast_projection (spec 11.4), as segments
    on a relative clock starting at 0. repeat_last_window: the lot's last window_s of
    recorded segments in that zone (shorter if the lot arrived later), back to back with
    the current package. declared_constant: one segment of the declared means over the
    horizon. A repeat with no recorded segment in the zone (as_of at arrival) falls back to
    declared_constant (docs/requests/B1.md R-14)."""
    zone = config.zones[history.current_zone]
    projection = zone["forecast_projection"]
    horizon = float(config.settings["forecast"]["horizon_s"]["value"])
    package_id = history.current_package
    if projection["kind"] == "repeat_last_window":
        window_value = projection["window_s"]
        window = float(
            window_value["value"]
            if window_value is not None
            else config.settings["forecast"]["window_s"]["value"]
        )
        start = history.as_of_s - window
        pieces: list[Segment] = []
        clock = 0.0
        for segment in history.exposure.segments:
            if segment.zone_id != history.current_zone or segment.t1_s <= start:
                continue
            duration = segment.t1_s - max(segment.t0_s, start)
            if duration <= 0.0:
                continue
            pieces.append(
                dataclasses.replace(segment, t0_s=clock, t1_s=clock + duration, package_id=package_id)
            )
            clock = clock + duration
        if pieces:
            return _Projection(
                "repeat_last_window", window, zone["zone_id"], package_id, tuple(pieces), clock
            )
    segment = exposure.declared_segment(zone, 0.0, horizon, package_id)
    return _Projection("declared_constant", None, zone["zone_id"], package_id, (segment,), horizon)


@dataclasses.dataclass(frozen=True, slots=True)
class _State:
    """Microclimate state at a window start: nominal RH_in (high, low variants; None tracks
    RH_out) and the unit-step responses per zone (None: a fresh start)."""

    high: float | None
    low: float | None
    h: tuple[float, ...] | None


@dataclasses.dataclass(frozen=True, slots=True)
class _Window:
    exposure: LotExposure
    trace: MicroclimateTrace
    steps: Mapping[str, tuple[float, ...]]
    end: _State
    starts: FloatArray
    ends: FloatArray


def _window(config: _Config, history: _History, segments: tuple[Segment, ...], state: _State) -> _Window:
    packages = config.packages
    trace = microclimate.trace_from(segments, packages, state.high, state.low)
    taus = trace.time_constant_s
    if state.h is None:
        steps = {
            zone_id: microclimate.unit_step_response(
                segments, taus, zone_id, first_start_tracks_outside=state.high is None
            )
            for zone_id in history.zone_ids
        }
    else:
        steps = {
            zone_id: microclimate.continue_step_response(segments, taus, zone_id, h)
            for zone_id, h in zip(history.zone_ids, state.h, strict=True)
        }
    end = _State(
        high=trace.rh_in_end_high[-1],
        low=trace.rh_in_end_low[-1],
        h=tuple(microclimate.step_response_end(segments, taus, z, steps[z]) for z in history.zone_ids),
    )
    window_exposure = LotExposure(
        lot_id=history.exposure.lot_id,
        t_start_s=segments[0].t0_s,
        t_end_s=segments[-1].t1_s,
        segments=segments,
        zone_histories=(),
        coverage_low=False,
    )
    return _Window(
        exposure=window_exposure,
        trace=trace,
        steps=steps,
        end=end,
        starts=np.array([s.t0_s for s in segments], dtype=np.float64),
        ends=np.array([s.t1_s for s in segments], dtype=np.float64),
    )


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= float(np.finfo(np.float64).eps) * max(1.0, abs(b))


def _periodic(start: _State, end: _State) -> bool:
    """The window maps its start state to itself up to float rounding, so every later
    repetition sees the same inputs (docs/requests/B1.md R-14)."""
    if start.h is None or end.h is None:
        return False
    return (
        _same(start.high, end.high)
        and _same(start.low, end.low)
        and all(_same(a, b) for a, b in zip(start.h, end.h, strict=True))
    )


def _clip(segments: tuple[Segment, ...], limit: float) -> tuple[Segment, ...]:
    out: list[Segment] = []
    for segment in segments:
        if segment.t0_s >= limit:
            break
        out.append(segment if segment.t1_s <= limit else dataclasses.replace(segment, t1_s=limit))
    return tuple(out)


def _start_state(config: _Config, history: _History, projection: _Projection) -> _State:
    segments = history.exposure.segments
    if not segments:
        initial = microclimate.initial_rh_of_first_package(projection.segments, config.packages)
        return _State(high=initial, low=initial, h=None)
    trace = history.trace
    return _State(
        high=trace.rh_in_end_high[-1],
        low=trace.rh_in_end_low[-1],
        h=tuple(
            microclimate.step_response_end(segments, trace.time_constant_s, z, history.steps[z])
            for z in history.zone_ids
        ),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Crossings:
    """Per draw of one chunk: kind 0 already below at as_of, 1 crosses inside the segment
    [t_start, t_end), 2 still at or above the limit at the horizon."""

    kind: NDArray[np.int8]
    fixed: FloatArray
    t_start: FloatArray
    t_end: FloatArray
    p_start: FloatArray
    p_end: FloatArray
    theta_d: FloatArray
    theta_r: FloatArray
    c0: FloatArray
    crossing: NDArray[np.intp]
    partial: PartialSegment | None


def _march(
    config: _Config,
    history: _History,
    model: StabilityModel,
    projection: _Projection,
    draws: DrawBlock,
    c0: FloatArray,
    theta_d0: FloatArray,
    theta_r0: FloatArray,
    lower: float,
    start_state: _State | None = None,
    windows: dict[int, _Window] | None = None,
) -> _Crossings:
    """Walk the projected windows for one chunk of draws and locate, per draw, the segment
    in which the active potency first falls below ``lower`` (spec 11.4). Every window is
    evaluated exactly from its own microclimate start; once a window maps its start state
    to itself the stored per-segment Theta is reused for the remaining full windows."""
    settings = config.settings
    zones = config.zones
    packages = config.packages
    horizon = float(settings["forecast"]["horizon_s"]["value"])
    m = draws.n

    def potency(c: FloatArray, td: FloatArray, tr: FloatArray) -> FloatArray:
        return kinetics.potency_from_thetas(model, c, td, tr, settings)[2]

    kind = np.full(m, 2, dtype=np.int8)
    fixed = potency(c0, theta_d0, theta_r0)
    kind[fixed < lower] = 0
    t_start = np.zeros(m)
    t_end = np.zeros(m)
    p_start = np.zeros(m)
    p_end = np.zeros(m)
    theta_d_at = np.zeros(m)
    theta_r_at = np.zeros(m)
    td = theta_d0.copy()
    tr = theta_r0.copy()
    active = np.flatnonzero(kind == 2)
    partials: list[tuple[NDArray[np.intp], PartialSegment]] = []
    state = _start_state(config, history, projection) if start_state is None else start_state
    length = projection.length_s
    stored: tuple[_Window, FloatArray, FloatArray] | None = None
    r = 0

    def record(
        who: NDArray[np.intp],
        base_d: FloatArray,
        base_r: FloatArray,
        cum_d: FloatArray,
        cum_r: FloatArray,
        offset: float | FloatArray,
        window: _Window,
        end_potency: FloatArray,
    ) -> None:
        """Locate the crossing segment of draws ``who`` inside one window that starts at
        Theta (base_d, base_r) and whose end potency is ``end_potency`` (< lower)."""
        within_d = base_d[None, :] + cum_d
        within_r = base_r[None, :] + cum_r
        below = potency(c0[who][None, :], within_d, within_r) < lower
        last = cum_d.shape[0] - 1
        # A periodic jump adds q x (window Theta) in one product, which can round a hair
        # differently from the running sum: then the crossing is put in the last segment,
        # whose end potency is taken from the window end (below the limit by construction).
        found = np.any(below, axis=0)
        k = np.where(found, np.argmax(below, axis=0), last).astype(np.intp)
        columns = np.arange(who.size)
        previous = np.maximum(k - 1, 0)
        start_d = np.where(k > 0, within_d[previous, columns], base_d)
        start_r = np.where(k > 0, within_r[previous, columns], base_r)
        theta_d_at[who] = start_d
        theta_r_at[who] = start_r
        p_start[who] = potency(c0[who], start_d, start_r)
        segment_end = potency(c0[who], within_d[k, columns], within_r[k, columns])
        p_end[who] = np.where(found, segment_end, np.minimum(segment_end, end_potency))
        t_start[who] = offset + window.starts[k]
        t_end[who] = np.minimum(offset + window.ends[k], horizon)
        kind[who] = 1
        partials.append(
            (
                who,
                kinetics.partial_segment(
                    model,
                    window.exposure,
                    window.trace,
                    window.steps,
                    kinetics.slice_draws(draws, who),
                    zones,
                    packages,
                    settings,
                    k,
                ),
            )
        )

    while active.size and r * length < horizon:
        offset = r * length
        remaining = horizon - offset
        full = length <= remaining
        if full and stored is not None:
            # Every remaining full window repeats the stored one: find per draw the first
            # window whose end potency is below the limit by bisection on the window count.
            window, cum_d_all, cum_r_all = stored
            count = math.floor(remaining / length)
            while count > 0 and (r + count) * length > horizon:
                count -= 1
            step_d = cum_d_all[-1, active]
            step_r = cum_r_all[-1, active]
            base_d = td[active]
            base_r = tr[active]
            c_active = c0[active]
            hit = potency(c_active, base_d + count * step_d, base_r + count * step_r) < lower
            lo = np.zeros(active.size, dtype=np.int64)
            hi = np.full(active.size, count, dtype=np.int64)
            while bool(np.any(hit & (hi - lo > 1))):
                mid = (lo + hi) // 2
                below = potency(c_active, base_d + mid * step_d, base_r + mid * step_r) < lower
                move = hit & (hi - lo > 1)
                hi = np.where(move & below, mid, hi)
                lo = np.where(move & ~below, mid, lo)
            if bool(np.any(hit)):
                who = active[hit]
                q = hi[hit]
                record(
                    who,
                    base_d[hit] + (q - 1) * step_d[hit],
                    base_r[hit] + (q - 1) * step_r[hit],
                    cum_d_all[:, who],
                    cum_r_all[:, who],
                    np.asarray((r + q - 1) * length, dtype=np.float64),
                    window,
                    potency(c0[who], base_d[hit] + q * step_d[hit], base_r[hit] + q * step_r[hit]),
                )
            stay = ~hit
            td[active[stay]] = base_d[stay] + count * step_d[stay]
            tr[active[stay]] = base_r[stay] + count * step_r[stay]
            active = active[stay]
            state = window.end
            r += max(count, 1)
            continue
        segments = projection.segments if full else _clip(projection.segments, remaining)
        if windows is not None and r in windows:
            window = windows[r]
        else:
            window = _window(config, history, segments, state)
            if windows is not None:
                windows[r] = window
        seg_d, seg_r = kinetics.projected_thetas(
            model,
            window.exposure,
            window.trace,
            window.steps,
            kinetics.slice_draws(draws, active),
            zones,
            packages,
            settings,
        )
        cum_d = np.cumsum(seg_d, axis=0)
        cum_r = np.cumsum(seg_r, axis=0)
        if full and _periodic(state, window.end):
            cum_d_all = np.zeros((cum_d.shape[0], m))
            cum_r_all = np.zeros((cum_r.shape[0], m))
            cum_d_all[:, active] = cum_d
            cum_r_all[:, active] = cum_r
            stored = (window, cum_d_all, cum_r_all)
        end_d = td[active] + cum_d[-1]
        end_r = tr[active] + cum_r[-1]
        end_potency = potency(c0[active], end_d, end_r)
        crossing = end_potency < lower
        if bool(np.any(crossing)):
            who = active[crossing]
            record(
                who,
                td[who],
                tr[who],
                cum_d[:, crossing],
                cum_r[:, crossing],
                offset,
                window,
                end_potency[crossing],
            )
        td[active] = end_d
        tr[active] = end_r
        active = active[~crossing]
        state = window.end
        r += 1
    fixed = np.where(kind == 2, potency(c0, td, tr), fixed)
    crossing_index = np.concatenate([who for who, _ in partials]) if partials else np.zeros(0, dtype=np.intp)
    joined = kinetics.concatenate_partials([p for _, p in partials]) if partials else None
    return _Crossings(
        kind=kind,
        fixed=fixed,
        t_start=t_start,
        t_end=t_end,
        p_start=p_start,
        p_end=p_end,
        theta_d=theta_d_at,
        theta_r=theta_r_at,
        c0=c0,
        crossing=crossing_index,
        partial=joined,
    )


def _crossing_times(config: _Config, run: _Run, projection: _Projection, lower: float) -> FloatArray:
    """Per-draw crossing times T_j (spec 11.4), retaining the estimate's draws, weights
    and re-anchoring state."""
    settings = config.settings
    model = run.model
    history = run.history
    batch_size = run.adaptive.batch_size
    extras = run.adaptive.extras
    start_state = _start_state(config, history, projection)
    windows: dict[int, _Window] = {}
    chunks: list[tuple[int, _Crossings]] = []
    for batch in range(run.adaptive.n_batches):
        draws = run.draw_blocks[batch]
        base = batch * batch_size
        for lo in range(0, batch_size, _FORECAST_CHUNK):
            hi = min(batch_size, lo + _FORECAST_CHUNK)
            index = np.arange(lo, hi, dtype=np.intp)
            chunk = kinetics.slice_draws(draws, index)
            chunks.append(
                (
                    base + lo,
                    _march(
                        config,
                        history,
                        model,
                        projection,
                        chunk,
                        extras["c0_effective"][base + lo : base + hi],
                        extras["theta_destructive"][base + lo : base + hi],
                        extras["theta_racemization"][base + lo : base + hi],
                        lower,
                        start_state,
                        windows,
                    ),
                )
            )
    n = run.adaptive.values.size
    kind = np.concatenate([c.kind for _, c in chunks])
    fixed = np.concatenate([c.fixed for _, c in chunks])
    t_start = np.concatenate([c.t_start for _, c in chunks])
    t_end = np.concatenate([c.t_end for _, c in chunks])
    p_start = np.concatenate([c.p_start for _, c in chunks])
    p_end = np.concatenate([c.p_end for _, c in chunks])
    theta_d = np.concatenate([c.theta_d for _, c in chunks])
    theta_r = np.concatenate([c.theta_r for _, c in chunks])
    c0 = np.concatenate([c.c0 for _, c in chunks])
    crossing_index = np.concatenate([c.crossing + start for start, c in chunks])
    pieces = [c.partial for _, c in chunks if c.partial is not None]
    partial = kinetics.concatenate_partials(pieces) if pieces else None
    position = np.full(n, -1, dtype=np.intp)
    position[crossing_index] = np.arange(crossing_index.size, dtype=np.intp)

    def potency_at(t: FloatArray) -> FloatArray:
        # Exact inside each draw's crossing segment; the segment-start (>= L) and segment-end
        # (< L) potencies outside it, which keeps every comparison with L of the bisection
        # identical to the exact potency (potency is non-increasing in t).
        out = np.where(kind == 1, np.where(t < t_start, p_start, p_end), fixed)
        inside = np.flatnonzero((kind == 1) & (t >= t_start) & (t < t_end))
        if inside.size and partial is not None:
            selected = partial.select(position[inside])
            add_d, add_r = kinetics.partial_thetas(selected, t[inside] - t_start[inside])
            out[inside] = kinetics.potency_from_thetas(
                model, c0[inside], theta_d[inside] + add_d, theta_r[inside] + add_r, settings
            )[2]
        return np.asarray(out, dtype=np.float64)

    return decision.crossing_times(potency_at, n, lower, settings["forecast"])


def _forecast(config: _Config, run: _Run, estimate: PotencyEstimate) -> Forecast:
    settings = config.settings
    history = run.history
    projection = _projection(config, history)
    lower = estimate.specification.lower_percent
    times = _crossing_times(config, run, projection, lower)
    horizon = float(settings["forecast"]["horizon_s"]["value"])
    probability = float(settings["decision"]["lower_quantile"]["value"])
    t_q = uncertainty.weighted_quantile(times, run.weights, probability)
    status: ForecastStatus
    if t_q == 0.0:
        status, crossing_s = "already_crossed", history.as_of_s
    elif math.isinf(t_q):
        status, crossing_s = "beyond_horizon", None
    else:
        status, crossing_s = "within_horizon", history.as_of_s + t_q
    capped = np.where(np.isinf(times), horizon, times)
    return Forecast(
        forecast_id=f"{estimate.estimate_id}:forecast",
        lot_id=estimate.lot_id,
        ingredient_id=estimate.ingredient_id,
        as_of_utc=estimate.as_of_utc,
        as_of_s=estimate.as_of_s,
        quantile_probability=probability,
        lower_limit_percent=lower,
        projection=Projection(
            kind=projection.kind,
            window_s=projection.window_s,
            zone_id=projection.zone_id,
            package_id=projection.package_id,
        ),
        status=status,
        crossing_utc=None if crossing_s is None else seconds_to_iso(crossing_s),
        crossing_s=crossing_s,
        horizon_s=horizon,
        crossing_delay_quantiles_s=uncertainty.quantiles(capped, run.weights, _probabilities(settings)),
        pins=estimate.pins,
    )


def forecast_lot(
    bundle: ConfigBundle,
    estimate: PotencyEstimate,
    series: Sequence[SensorSeries],
    assays: Sequence[AssayObservation],
) -> Forecast:
    """Forecast of spec 11.4 for one estimate, reusing its seed and draws (the projection
    re-evaluates the same draws forward in time). The estimate's Monte Carlo run is
    recomputed from the same inputs; a different seed means different inputs and raises
    ``DeterminismError``."""
    config = _config(bundle)
    history = _history(config, estimate.lot_id, estimate.as_of_utc, series, assays, ())
    done = _run(config, history, estimate.ingredient_id, assays, (), None)
    if done.estimate.monte_carlo.seed_hex != estimate.monte_carlo.seed_hex:
        raise DeterminismError(f"estimate {estimate.estimate_id!r} was computed from other inputs")
    return _forecast(config, done.run, estimate)


# =========================================================================================
# Decisions and ordering (spec 11.1 to 11.5, 12.2 steps 10 to 12)
# =========================================================================================


def _alternative_products(config: _Config, product: ProductDoc) -> tuple[str, ...]:
    own = sorted(i["ingredient_id"] for i in product["ingredients"])
    return tuple(
        sorted(
            other["product_id"]
            for other in config.products.values()
            if other["product_id"] != product["product_id"]
            and sorted(i["ingredient_id"] for i in other["ingredients"]) == own
        )
    )


def _decision_pins(config: _Config, history: _History, runs: Sequence[_Estimated]) -> Pins:
    """A lot decision has no draws of its own; its seed_hex comes from the same material
    with purpose "lot_decision" (docs/requests/B1.md R-15)."""
    seed = uncertainty.derive_seed(_seed_material(history, "", "lot_decision", config))
    keys = [key for run in runs for key in run.estimate.pins.computed_cache_keys]
    return _pins(config, history, seed, keys)


def _assess(
    config: _Config,
    lot_id: str,
    as_of_utc: str,
    series: Sequence[SensorSeries],
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
    alternative_lots: tuple[str, ...],
) -> LotAssessment:
    history = _history(config, lot_id, as_of_utc, series, assays, inspections)
    product = history.product
    runs = [
        _run(config, history, ingredient["ingredient_id"], assays, inspections, None)
        for ingredient in sorted(product["ingredients"], key=lambda i: i["ingredient_id"])
    ]
    estimates = tuple(done.estimate for done in runs)
    forecasts = tuple(_forecast(config, done.run, done.estimate) for done in runs)
    lot_decision = decision.decide_lot(
        history.lot,
        product,
        estimates,
        forecasts,
        (alternative_lots, _alternative_products(config, product)),
        config.settings["decision"],
        _decision_pins(config, history, runs),
    )
    return LotAssessment(
        lot_id=lot_id, as_of_utc=as_of_utc, estimates=estimates, forecasts=forecasts, decision=lot_decision
    )


def _same_product_lots(config: _Config, lot: LotDoc) -> tuple[str, ...]:
    return tuple(
        sorted(
            other["lot_id"]
            for other in config.lots.values()
            if other["product_id"] == lot["product_id"]
            and other["lot_id"] != lot["lot_id"]
            and other["status"] == "active"
        )
    )


def assess_lot(
    bundle: ConfigBundle,
    lot_id: str,
    as_of_utc: str,
    series: Sequence[SensorSeries],
    assays: Sequence[AssayObservation],
    inspections: Sequence[InspectionObservation],
) -> LotAssessment:
    """Estimates, forecasts and decision of one lot. Without the other lots' decisions the
    alternative lots are the other active lots of the product sorted by id;
    ``assess_inventory`` reorders them by spec 11.5 (docs/requests/B1.md R-16)."""
    config = _config(bundle)
    lot = _lot(config, lot_id)
    return _assess(config, lot_id, as_of_utc, series, assays, inspections, _same_product_lots(config, lot))


def assess_inventory(bundle: ConfigBundle, inputs: EngineInputsDoc) -> InventoryAssessment:
    """Every lot with status ``active`` assessed at ``inputs.as_of_utc`` and ordered by
    spec 11.5. The top-level engine entry point for the hub."""
    series, assays, inspections = validate_inputs(bundle, inputs)
    config = _config(bundle)
    as_of_utc = inputs["as_of_utc"]
    active = sorted(lot_id for lot_id, lot in config.lots.items() if lot["status"] == "active")
    assessments = {
        lot_id: _assess(config, lot_id, as_of_utc, series, assays, inspections, ()) for lot_id in active
    }
    ordered = decision.sort_lots([a.decision for a in assessments.values()])
    rank = {d.lot_id: index for index, d in enumerate(ordered)}
    out: list[LotAssessment] = []
    for lot_decision in ordered:
        lot = config.lots[lot_decision.lot_id]
        alternatives = tuple(
            sorted(_same_product_lots(config, lot), key=lambda other: rank.get(other, len(rank)))
        )
        assessment = assessments[lot_decision.lot_id]
        out.append(
            dataclasses.replace(
                assessment,
                decision=dataclasses.replace(assessment.decision, alternative_lot_ids=alternatives),
            )
        )
    return InventoryAssessment(as_of_utc=as_of_utc, assessments=tuple(out))


# =========================================================================================
# Replay (spec 12.4)
# =========================================================================================


def _collect_pins(value: JSONValue, out: list[JSONValue]) -> None:
    if isinstance(value, dict):
        for key in sorted(value):
            if key == "pins":
                out.append(value[key])
            else:
                _collect_pins(value[key], out)
    elif isinstance(value, list):
        for item in value:
            _collect_pins(item, out)


def replay(bundle: ConfigBundle, inputs: EngineInputsDoc, recorded: JSONValue) -> bool:
    """Recompute an InventoryAssessment and compare its canonical JSON with ``recorded``
    (spec 12.4). Returns True on a bit-identical match. Raises ``DeterminismError`` if the
    pins of ``recorded`` do not match the bundle and inputs (wrong configuration, not a
    nondeterminism)."""
    computed = to_json(assess_inventory(bundle, inputs))
    recorded_pins: list[JSONValue] = []
    computed_pins: list[JSONValue] = []
    _collect_pins(recorded, recorded_pins)
    _collect_pins(computed, computed_pins)
    if canonical_json_bytes(recorded_pins) != canonical_json_bytes(computed_pins):
        raise DeterminismError("the recorded pins do not match this configuration and these inputs")
    return canonical_json_bytes(recorded) == canonical_json_bytes(computed)
