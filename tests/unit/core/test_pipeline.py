"""Unit and integration tests of pharmacy.pipeline (spec 12) with hand-built stability models:
``pharmacy.chemistry.parameters.build_stability_model`` is monkeypatched in every test, so
nothing here depends on the chemistry layer's state. Covers input validation, estimates in
declared and sensed zones, the assay update and re-anchor, inspections, forecasts (each
status, and the projection march against a brute-force evaluation), lot and inventory
assessment, determinism and replay."""

import copy
import dataclasses
import importlib.metadata
import math
from collections.abc import Callable, Sequence
from typing import Any, cast

import numpy as np
import pytest

from pharmacy import decision, exposure, kinetics, microclimate, pipeline, uncertainty, update
from pharmacy.chemistry import parameters
from pharmacy.config import ConfigBundle, canonical_json_bytes, schema_violations
from pharmacy.errors import (
    ComputationError,
    ConfigReferenceError,
    ConfigSchemaError,
    DeterminismError,
    InputError,
    ObservationError,
    SensorDataError,
)
from pharmacy.types import (
    AssayObservation,
    ComputedDetail,
    EngineInputsDoc,
    FreeShare,
    InspectionObservation,
    JSONValue,
    LotExposure,
    SensorSeries,
    StabilityModel,
    iso_to_seconds,
    to_json,
)
from tests.unit.core import builders as b

GROUND = "2027-08-01T00:00:00Z"
TRANSIT = "2028-01-12T00:00:00Z"
LAUNCH = "2028-01-10T00:00:00Z"

ModelTable = dict[tuple[str, str], Callable[[str, str], StabilityModel]]


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> ModelTable:
    """Per-test model table keyed by (product_id, ingredient_id); default: a thermal model
    with ln k* = -19.5 (and a radiolysis pathway) on the requested ingredient."""
    table: ModelTable = {}

    def build(
        bundle: ConfigBundle, product_id: str, ingredient_id: str, package_ids: Any, **_: Any
    ) -> StabilityModel:
        maker = table.get((product_id, ingredient_id))
        if maker is not None:
            return maker(product_id, ingredient_id)
        return b.thermal_model(product_id=product_id, ingredient_id=ingredient_id, ln_k=-19.5)

    monkeypatch.setattr(parameters, "build_stability_model", build)
    return table


@pytest.fixture(scope="module")
def fast_bundle(bundle: ConfigBundle) -> ConfigBundle:
    """The configuration with n_dig = 1, so the adaptive loop stops after a few batches."""
    data: Any = copy.deepcopy(bundle.documents["engine-settings"].data)
    data["monte_carlo"]["n_dig"]["value"] = 1
    data["monte_carlo"]["max_batches"]["value"] = 6
    return b.bundle_with(bundle, "engine-settings", data)


def _thermal(
    ln_k: float, *, limit: float | None = 0.15, mass: float | None = 109.13
) -> Callable[[str, str], StabilityModel]:
    """The default thermal model (hydrolysis plus radiolysis, same variate layout) with its
    degradant's limit and molar mass replaced."""

    def make(product_id: str, ingredient_id: str) -> StabilityModel:
        base = b.thermal_model(product_id=product_id, ingredient_id=ingredient_id, ln_k=ln_k)
        path = base.pathways[0]
        assert path.degradant is not None
        spec = dataclasses.replace(path.degradant, limit_percent=limit, molar_mass_g_per_mol=mass)
        return dataclasses.replace(
            base, pathways=(dataclasses.replace(path, degradant=spec), *base.pathways[1:])
        )

    return make


def _transit_series(as_of: str = TRANSIT, temperature: Any = (296.0, 297.5, 299.0)) -> list[SensorSeries]:
    return [
        b.constant_series(
            "trk-t", "trk-t1", "transit_kit", "temperature_k", "K", LAUNCH, as_of, 1800.0, list(temperature)
        ),
        b.constant_series(
            "trk-rh",
            "trk-rh1",
            "transit_kit",
            "relative_humidity",
            "percent",
            LAUNCH,
            as_of,
            1800.0,
            [45.0, 55.0],
        ),
        b.constant_series(
            "trk-d", "trk-dos1", "transit_kit", "dose_rate_gy_per_s", "Gy/s", LAUNCH, as_of, 1800.0, 3e-9
        ),
    ]


def _assay(
    assay_id: str,
    at: str,
    value: float,
    *,
    lot_id: str = "lot-apap-002",
    sd: float = 0.5,
    ingredient_id: str = "acetaminophen",
    measurand: str = "total",
) -> AssayObservation:
    return AssayObservation(
        assay_id,
        lot_id,
        ingredient_id,
        at,
        iso_to_seconds(at),
        measurand,  # type: ignore[arg-type]
        value,
        sd,
        "ground",
        "hplc",
        "lab",
        "",
    )


def _inspection(
    inspection_id: str, at: str, finding: str, lot_id: str = "lot-apap-002"
) -> InspectionObservation:
    return InspectionObservation(inspection_id, lot_id, at, iso_to_seconds(at), finding, "crew", "")  # type: ignore[arg-type]


def _inputs(
    as_of: str, series: Sequence[SensorSeries] = (), assays: Any = (), inspections: Any = ()
) -> EngineInputsDoc:
    return {
        "schema": "pharmacy/inputs.schema.json",
        "as_of_utc": as_of,
        "sensor_series": [to_json(s) for s in series],  # type: ignore[misc]
        "assays": [to_json(a) for a in assays],  # type: ignore[misc]
        "inspections": [to_json(i) for i in inspections],  # type: ignore[misc]
    }


def _quantile(estimate: Any, probability: float) -> float:
    return float(next(q.value for q in estimate.quantiles if q.probability == probability))


# =========================================================================================
# validate_inputs (spec 12.1)
# =========================================================================================


def test_validate_inputs_converts_sorts_and_drops_later_records(bundle: ConfigBundle) -> None:
    series = _transit_series("2028-01-12T06:00:00Z")
    assays = [
        _assay("b2", "2027-09-01T00:00:00Z", 99.0),
        _assay("a1", "2027-08-01T00:00:00Z", 99.5),
        _assay("late", "2028-02-01T00:00:00Z", 98.0),
    ]
    inspections = [
        _inspection("i2", "2027-09-01T00:00:00Z", "no_change"),
        _inspection("i9", "2028-03-01T00:00:00Z", "color_change"),
    ]
    got_series, got_assays, got_inspections = pipeline.validate_inputs(
        bundle, _inputs(TRANSIT, series[::-1], assays, inspections)
    )
    assert [s.series_id for s in got_series] == ["trk-d", "trk-rh", "trk-t"]
    assert all(p.t_s <= iso_to_seconds(TRANSIT) for s in got_series for p in s.points)
    assert len(got_series[0].points) == 97  # 48 h at 30 min, both ends included
    assert [a.assay_id for a in got_assays] == ["a1", "b2"]
    assert [i.inspection_id for i in got_inspections] == ["i2"]


def _broken_inputs(case: str) -> tuple[EngineInputsDoc, type[Exception]]:
    series = _transit_series()
    inputs = _inputs(TRANSIT, series)
    raw: Any = inputs
    if case == "schema":
        del raw["assays"]
        return inputs, ConfigSchemaError
    if case == "duplicate_series":
        raw["sensor_series"].append(copy.deepcopy(raw["sensor_series"][0]))
        return inputs, SensorDataError
    if case == "unknown_zone":
        raw["sensor_series"][0]["zone_id"] = "venus_base"
        return inputs, SensorDataError
    if case == "unknown_sensor":
        raw["sensor_series"][0]["sensor_id"] = "trk-t9"
        return inputs, SensorDataError
    if case == "wrong_quantity":
        raw["sensor_series"][0]["quantity"] = "relative_humidity"
        return inputs, SensorDataError
    if case == "time_disagreement":
        raw["sensor_series"][0]["points"][1]["t_s"] += 5.0
        return inputs, SensorDataError
    if case == "bad_record":
        raw["sensor_series"][0]["points"][1]["t_utc"] = "2028-13-40T00:00:00Z"
        return inputs, SensorDataError
    observations: dict[str, tuple[Any, type[Exception]]] = {
        "unknown_lot": (_assay("a1", "2027-08-01T00:00:00Z", 99.0, lot_id="lot-zzz"), ObservationError),
        "unknown_ingredient": (
            _assay("a1", "2027-08-01T00:00:00Z", 99.0, ingredient_id="aspirin"),
            ObservationError,
        ),
        "before_manufacture": (_assay("a1", "2027-01-01T00:00:00Z", 99.0), ObservationError),
        "zero_sd": (_assay("a1", "2027-08-01T00:00:00Z", 99.0, sd=0.0), ObservationError),
    }
    if case in observations:
        record, error = observations[case]
        raw["assays"] = [to_json(record)]
        return inputs, error
    if case == "duplicate_assay":
        raw["assays"] = [to_json(_assay("a1", "2027-08-01T00:00:00Z", 99.0))] * 2
        return inputs, ObservationError
    if case == "inspection_unknown_lot":
        raw["inspections"] = [
            to_json(_inspection("i1", "2027-08-01T00:00:00Z", "no_change", lot_id="lot-zzz"))
        ]
        return inputs, ObservationError
    if case == "inspection_time":
        record = to_json(_inspection("i1", "2027-08-01T00:00:00Z", "no_change"))
        record["inspected_at_s"] = 1.0  # type: ignore[index, call-overload]
        raw["inspections"] = [record]
        return inputs, ObservationError
    if case == "duplicate_inspection":
        raw["inspections"] = [to_json(_inspection("i1", "2027-08-01T00:00:00Z", "no_change"))] * 2
        return inputs, ObservationError
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "schema",
        "duplicate_series",
        "unknown_zone",
        "unknown_sensor",
        "wrong_quantity",
        "time_disagreement",
        "bad_record",
        "unknown_lot",
        "unknown_ingredient",
        "before_manufacture",
        "zero_sd",
        "duplicate_assay",
        "inspection_unknown_lot",
        "inspection_time",
        "duplicate_inspection",
    ],
)
def test_validate_inputs_rejects(case: str, bundle: ConfigBundle) -> None:
    inputs, error = _broken_inputs(case)
    with pytest.raises(error):
        pipeline.validate_inputs(bundle, inputs)


# =========================================================================================
# Estimates (spec 12.2 steps 2 to 9)
# =========================================================================================


def test_ground_estimate_matches_a_direct_evaluation(bundle: ConfigBundle, models: ModelTable) -> None:
    """The reported quantiles are those of kinetics.evaluate_draws on the draws of
    batch_generator(seed, r), with the zones and steps of the lot's own history."""
    estimate = pipeline.estimate_potency(bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    model = b.thermal_model(product_id="acetaminophen_325_tab", ingredient_id="acetaminophen", ln_k=-19.5)
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    lot = next(lot for lot in bundle.lots["lots"] if lot["lot_id"] == "lot-apap-002")
    as_of_s = iso_to_seconds(GROUND)
    found = exposure.build_lot_exposure(lot, zones, packages, {}, as_of_s)
    trace = microclimate.internal_rh_trace(found.segments, packages)
    steps = {
        "ground_storage": microclimate.unit_step_response(
            found.segments, trace.time_constant_s, "ground_storage", first_start_tracks_outside=False
        )
    }
    seed = int(estimate.monte_carlo.seed_hex, 16)
    values = []
    for r in range(estimate.monte_carlo.n_batches):
        draws = uncertainty.draw_parameters(
            model,
            100.0,
            1.5,
            zones,
            ["ground_storage"],
            uncertainty.batch_generator(seed, r),
            estimate.monte_carlo.batch_size,
        )
        values.append(
            kinetics.evaluate_draws(
                model, found, trace, steps, draws, zones, packages, bundle.settings
            ).potency_active_percent
        )
    joined = np.concatenate(values)
    for q in estimate.quantiles:
        assert q.value == uncertainty.weighted_quantile(joined, None, q.probability)
    assert estimate.mean == pytest.approx(float(np.mean(joined)), rel=1e-12)
    assert estimate.monte_carlo.n_draws == joined.size
    assert estimate.monte_carlo.converged
    assert estimate.exposure.mkt_k == pytest.approx(298.15, rel=1e-12)
    assert estimate.pins.sensor_series == ()
    assert estimate.pins.config == bundle.pins()
    assert estimate.p_within_spec + estimate.p_below_lower + estimate.p_above_upper == pytest.approx(1.0)
    assert estimate.coverage_interval.low == _quantile(estimate, 0.05)
    assert "degradant_profile_not_reviewed" in estimate.flags
    assert "coverage_low" not in estimate.flags
    assert estimate.total_quantiles is None
    assert estimate.assay_ids_used == ()
    assert estimate.radiation.method == "none"
    assert not estimate.radiation.included_in_potency
    assert estimate.radiation.cumulative_absorbed_dose_gy == estimate.exposure.cumulative_absorbed_dose_gy


def test_estimate_is_deterministic_and_the_seed_follows_the_pins(
    fast_bundle: ConfigBundle, models: ModelTable
) -> None:
    def run(as_of: str = GROUND, assays: Any = ()) -> Any:
        return pipeline.estimate_potency(
            fast_bundle, "lot-apap-002", "acetaminophen", as_of, [], list(assays), []
        )

    first, second = run(), run()
    assert canonical_json_bytes(to_json(first)) == canonical_json_bytes(to_json(second))
    assert run("2027-08-02T00:00:00Z").monte_carlo.seed_hex != first.monte_carlo.seed_hex
    other_lot = _assay("x1", "2027-07-01T00:00:00Z", 99.0, lot_id="lot-apap-001")
    assert run(assays=[other_lot]).monte_carlo.seed_hex == first.monte_carlo.seed_hex
    own = _assay("x1", "2027-07-01T00:00:00Z", 99.0)
    assert run(assays=[own]).monte_carlo.seed_hex != first.monte_carlo.seed_hex
    later = _assay("x2", "2027-09-01T00:00:00Z", 50.0)
    assert run(assays=[later]).monte_carlo.seed_hex == first.monte_carlo.seed_hex  # after as_of: never used


def test_transit_estimate_pins_only_custody_series(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    series = _transit_series()
    moon = b.constant_series(
        "mbp-t", "mbp-t1", "moon_base_pharmacy", "temperature_k", "K", LAUNCH, TRANSIT, 3600.0, 295.0
    )
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-apap-001", "acetaminophen", TRANSIT, [*series, moon], [], []
    )
    assert [p.series_id for p in estimate.pins.sensor_series] == ["trk-d", "trk-rh", "trk-t"]
    assert estimate.pins.sensor_series[2] == exposure.series_pin(series[0], iso_to_seconds(TRANSIT))
    assert "coverage_low" not in estimate.flags
    trace = {t.quantity: t.value for t in estimate.trace}
    assert trace["current_zone_id"] == "transit_kit"
    assert trace["current_package_id"] == "repackaged_bag"
    assert trace["degradant_mass_ratio:deg_amide_hydrolysis"] == pytest.approx(109.13 / 151.16)


def test_missing_sensor_data_flags_coverage(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    estimate = pipeline.estimate_potency(fast_bundle, "lot-apap-001", "acetaminophen", TRANSIT, [], [], [])
    assert "coverage_low" in estimate.flags
    (placard,) = [p for p in estimate.placards if p.kind == "coverage"]
    assert placard.extent == 2 * 86400.0
    assert placard.unit == "s"


def test_outside_the_validated_range(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    hot = _transit_series(temperature=(318.0,))
    estimate = pipeline.estimate_potency(fast_bundle, "lot-apap-001", "acetaminophen", TRANSIT, hot, [], [])
    assert "outside_validated_range" in estimate.flags
    (placard,) = [p for p in estimate.placards if p.kind == "temperature_range"]
    assert placard.extent == pytest.approx(318.0 - 313.15)


def test_degradants_on_a_mass_basis_and_unknown_molar_mass(
    fast_bundle: ConfigBundle, models: ModelTable
) -> None:
    known = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(-19.5, limit=None, mass=None)
    molar = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    (with_mass,) = known.degradants
    (without,) = molar.degradants
    assert with_mass.mean == pytest.approx(without.mean * 109.13 / 151.16, rel=1e-12)
    assert with_mass.p_above_limit is not None
    assert without.p_above_limit is None
    note = next(t for t in molar.trace if t.quantity == "degradant_mass_ratio:deg_amide_hydrolysis")
    assert note.value == 1.0
    assert "R-08" in note.note
    # Molar basis, yield 1: the degradant equals the drug lost, C0 times alpha.
    assert without.mean == pytest.approx(100.0 - molar.mean, abs=0.05)


def test_racemizing_ingredient_reports_total_quantiles(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    estimate = pipeline.estimate_potency(fast_bundle, "lot-epi-syr-001", "epinephrine", GROUND, [], [], [])
    assert estimate.total_quantiles is not None
    # No racemization pathway in this model and f0 = 1: total equals active.
    assert [q.value for q in estimate.total_quantiles] == [q.value for q in estimate.quantiles]


def test_model_and_configuration_mismatches(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    models[("acetaminophen_325_tab", "acetaminophen")] = lambda product_id, ingredient_id: b.thermal_model(
        product_id=product_id, ingredient_id="ibuprofen"
    )
    with pytest.raises(InputError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    lots: Any = copy.deepcopy(fast_bundle.documents["lots"].data)
    lots["lots"][1]["initial_assay"] = []
    stripped = b.bundle_with(fast_bundle, "lots", lots)
    del models[("acetaminophen_325_tab", "acetaminophen")]
    with pytest.raises(ConfigReferenceError):
        pipeline.estimate_potency(stripped, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    with pytest.raises(InputError):
        pipeline.estimate_potency(fast_bundle, "lot-nope", "acetaminophen", GROUND, [], [], [])
    with pytest.raises(InputError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "ibuprofen", GROUND, [], [], [])


# =========================================================================================
# Assays and inspections (spec 10)
# =========================================================================================


def test_consistent_assay_narrows_the_estimate(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    prior = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assay = _assay("a1", "2027-07-31T00:00:00Z", _quantile(prior, 0.5), sd=0.5)
    posterior = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [assay], []
    )
    assert posterior.assay_ids_used == ("a1",)
    assert posterior.standard_uncertainty < 0.75 * prior.standard_uncertainty
    assert posterior.monte_carlo.effective_sample_size is not None
    assert posterior.monte_carlo.effective_sample_size >= 1000.0
    assert "assay_model_conflict" not in posterior.flags
    assert "assay_reanchored" not in posterior.flags
    tail = next(t for t in posterior.trace if t.quantity == "assay_tail_probability:a1")
    assert isinstance(tail.value, float)
    assert tail.value > 0.5


def test_conflicting_assay_reanchors_to_the_latest(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    early = _assay("a1", "2027-07-01T00:00:00Z", 99.0, sd=0.5)
    wild = _assay("a2", "2027-07-31T00:00:00Z", 80.0, sd=0.3)
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [early, wild], []
    )
    assert {"assay_model_conflict", "assay_reanchored"} <= set(estimate.flags)
    assert estimate.assay_ids_used == ("a2",)
    assert estimate.monte_carlo.effective_sample_size is None
    (placard,) = [p for p in estimate.placards if p.kind == "assay_conflict"]
    assert placard.extent < 1e-3
    # Re-anchored at a2 one day before as_of: the level sits near 80 with the assay's total sd.
    sd = math.hypot(0.3, fast_bundle.settings["update"]["ground_inter_laboratory_sd_percent"]["value"])
    assert _quantile(estimate, 0.5) == pytest.approx(80.0, abs=0.1)
    assert estimate.standard_uncertainty == pytest.approx(sd, rel=0.05)


def test_active_measurand_reanchor(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    wild = _assay(
        "a1",
        "2027-07-31T00:00:00Z",
        80.0,
        sd=0.3,
        lot_id="lot-epi-syr-001",
        ingredient_id="epinephrine",
        measurand="active",
    )
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-epi-syr-001", "epinephrine", GROUND, [], [wild], []
    )
    assert "assay_reanchored" in estimate.flags
    assert _quantile(estimate, 0.5) == pytest.approx(80.0, abs=0.1)


def test_inspection_override(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    found = _inspection("i1", "2027-07-15T00:00:00Z", "color_change")
    later = _inspection("i2", "2027-09-01T00:00:00Z", "cracking")
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [later, found]
    )
    assert estimate.inspection_override == "color_change"
    assert estimate.inspection_ids_used == ("i1",)
    assert "inspection_override" in estimate.flags
    clean = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [later])
    assert clean.inspection_override is None
    assert "inspection_override" not in clean.flags


# =========================================================================================
# Forecasts (spec 11.4)
# =========================================================================================


@pytest.mark.parametrize(
    ("ln_k", "status"), [(-15.0, "already_crossed"), (-19.0, "within_horizon"), (-30.0, "beyond_horizon")]
)
def test_forecast_statuses(ln_k: float, status: str, fast_bundle: ConfigBundle, models: ModelTable) -> None:
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(ln_k)
    assessment = pipeline.assess_lot(fast_bundle, "lot-apap-002", GROUND, [], [], [])
    (forecast,) = assessment.forecasts
    horizon = forecast.horizon_s
    assert forecast.status == status
    assert forecast.projection.kind == "declared_constant"
    assert forecast.projection.zone_id == "ground_storage"
    assert forecast.projection.package_id == "original_bottle"
    assert forecast.projection.window_s is None
    delays = [q.value for q in forecast.crossing_delay_quantiles_s]
    assert delays == sorted(delays)
    assert all(0.0 <= d <= horizon for d in delays)
    if status == "already_crossed":
        assert forecast.crossing_s == forecast.as_of_s
        assert delays[0] == 0.0
    elif status == "beyond_horizon":
        assert forecast.crossing_s is None
        assert forecast.crossing_utc is None
        assert delays == [horizon] * len(delays)
    else:
        assert forecast.crossing_s is not None
        assert forecast.as_of_s < forecast.crossing_s < forecast.as_of_s + horizon
        assert forecast.crossing_utc is not None
        assert iso_to_seconds(forecast.crossing_utc) == pytest.approx(forecast.crossing_s, abs=1e-3)
    assert forecast.pins == assessment.estimates[0].pins


def test_declared_forecast_agrees_with_a_later_estimate(bundle: ConfigBundle, models: ModelTable) -> None:
    """Ground storage is declared and so is its projection: at the forecast crossing time the
    lower quantile of a fresh estimate sits at the lower limit (up to Monte Carlo noise)."""
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(-19.0)
    (forecast,) = pipeline.assess_lot(bundle, "lot-apap-002", GROUND, [], [], []).forecasts
    assert forecast.status == "within_horizon"
    assert forecast.crossing_s is not None
    assert forecast.crossing_s < iso_to_seconds(LAUNCH)  # still in ground storage then
    when = forecast.crossing_utc
    assert when is not None
    later = pipeline.estimate_potency(bundle, "lot-apap-002", "acetaminophen", when[:19] + "Z", [], [], [])
    assert _quantile(later, 0.05) == pytest.approx(90.0, abs=0.25)


def test_forecast_lot_reuses_the_estimate_run(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(-18.5)
    series = _transit_series()
    assessment = pipeline.assess_lot(fast_bundle, "lot-apap-001", TRANSIT, series, [], [])
    (estimate,) = assessment.estimates
    forecast = pipeline.forecast_lot(fast_bundle, estimate, series, [])
    assert forecast == assessment.forecasts[0]
    assert forecast.projection.kind == "repeat_last_window"
    assert forecast.projection.window_s == 2592000.0
    assert (forecast.projection.zone_id, forecast.projection.package_id) == ("transit_kit", "repackaged_bag")
    with pytest.raises(DeterminismError):
        pipeline.forecast_lot(
            fast_bundle, estimate, series, [_assay("a1", "2027-07-01T00:00:00Z", 99.0, lot_id="lot-apap-001")]
        )


def test_repeat_projection_falls_back_at_arrival(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    """As of the arrival instant there is no recorded segment in the current zone yet: the
    projection falls back to declared conditions, which the station zones do not declare."""
    config = pipeline._config(fast_bundle)
    history = pipeline._history(config, "lot-apap-001", LAUNCH, [], [], [])
    with pytest.raises(ConfigReferenceError):
        pipeline._projection(config, history)


def _brute_potency(
    config: Any, history: Any, projection: Any, model: Any, draws: Any, j: int, t: float
) -> float:
    """Active potency of draw j at as_of + t from one explicit exposure: the history plus the
    projection repeated back to back (absolute times), evaluated in one evaluate_draws call."""
    as_of = history.as_of_s
    segments = list(history.exposure.segments)
    length = projection.length_s
    r = 0
    while r * length < t:
        for s in projection.segments:
            t0 = r * length + s.t0_s
            if t0 >= t:
                break
            t1 = min(r * length + s.t1_s, t)
            segments.append(dataclasses.replace(s, t0_s=as_of + t0, t1_s=as_of + t1))
        r += 1
    full = LotExposure(
        history.exposure.lot_id, history.exposure.t_start_s, as_of + t, tuple(segments), (), False
    )
    trace = microclimate.internal_rh_trace(full.segments, config.packages)
    steps = {
        z: microclimate.unit_step_response(
            full.segments, trace.time_constant_s, z, first_start_tracks_outside=history.first_tracks_outside
        )
        for z in history.zone_ids
    }
    one = kinetics.slice_draws(draws, np.array([j], dtype=np.intp))
    result = kinetics.evaluate_draws(
        model, full, trace, steps, one, config.zones, config.packages, config.settings
    )
    return float(result.potency_active_percent[0])


def test_projection_march_against_brute_force(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    """Every draw's crossing segment brackets the brute-force crossing, and the exact partial
    evaluation inside it equals the brute-force potency; the periodic jump is exercised
    (crossings 50 to 430 days out with a 2-day window that turns periodic after ~45 windows)."""
    config = pipeline._config(fast_bundle)
    history = pipeline._history(config, "lot-apap-001", TRANSIT, _transit_series(), [], [])
    projection = pipeline._projection(config, history)
    assert projection.kind == "repeat_last_window"
    assert projection.length_s == 2 * 86400.0
    model = b.thermal_model(product_id="acetaminophen_325_tab", ingredient_id="acetaminophen", ln_k=-19.8)
    n = 12
    draws = uncertainty.draw_parameters(
        model, 100.0, 1.5, config.zones, history.zone_ids, uncertainty.batch_generator(1, 0), n
    )
    result = kinetics.evaluate_draws(
        model,
        history.exposure,
        history.trace,
        history.steps,
        draws,
        config.zones,
        config.packages,
        config.settings,
    )
    theta_d, theta_r, _ = kinetics.pathway_sums(model, result.theta, config.settings)
    c0 = draws.c0_percent.copy()
    c0[0], c0[1] = 85.0, 1000.0  # already below; never crosses within the horizon
    crossings = pipeline._march(config, history, model, projection, draws, c0, theta_d, theta_r, 90.0)
    assert crossings.kind[0] == 0
    assert crossings.kind[1] == 2
    assert crossings.fixed[1] >= 90.0
    crossing = list(crossings.kind[2:] == 1)
    assert all(crossing)
    assert float(np.max(crossings.t_start)) > 100 * 86400.0  # past the periodic switch
    assert crossings.partial is not None
    position = {int(j): i for i, j in enumerate(crossings.crossing)}
    scaled = dataclasses.replace(draws, c0_percent=c0)
    for j in range(2, n):
        lo, hi = float(crossings.t_start[j]), float(crossings.t_end[j])
        at_lo = _brute_potency(config, history, projection, model, scaled, j, lo)
        at_hi = _brute_potency(config, history, projection, model, scaled, j, hi)
        assert at_lo >= 90.0 > at_hi
        assert at_lo == pytest.approx(crossings.p_start[j], rel=1e-9)
        assert at_hi == pytest.approx(crossings.p_end[j], rel=1e-9)
        mid = 0.5 * (lo + hi)
        part = crossings.partial.select(np.array([position[j]], dtype=np.intp))
        add_d, add_r = kinetics.partial_thetas(part, np.array([mid - lo]))
        _, _, exact = kinetics.potency_from_thetas(
            model,
            c0[j : j + 1],
            crossings.theta_d[j : j + 1] + add_d,
            crossings.theta_r[j : j + 1] + add_r,
            config.settings,
        )
        assert float(exact[0]) == pytest.approx(
            _brute_potency(config, history, projection, model, scaled, j, mid), rel=1e-9
        )


# =========================================================================================
# Lot and inventory assessment, replay (spec 11, 12.4)
# =========================================================================================


def test_assess_lot_decision_and_alternatives(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    assessment = pipeline.assess_lot(fast_bundle, "lot-apap-001", GROUND, [], [], [])
    found = assessment.decision
    assert found.alternative_lot_ids == ("lot-apap-002",)
    assert found.alternative_product_ids == ()
    # Unidentified loss warns and blocks adjustment; potency remains in specification.
    assert found.state == "in_specification"
    assert [r.code for r in found.reasons if r.severity == "warning"] == [
        "degradant_unidentified_above_limit"
    ]
    assert not found.adjustment.eligible  # acetaminophen lists a toxic degradant
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(-19.5, limit=None)
    clean = pipeline.assess_lot(fast_bundle, "lot-apap-001", GROUND, [], [], []).decision
    assert clean.state == "in_specification"
    assert clean.reasons[0].code == "within_specification"
    assert found.pins.seed_hex != assessment.estimates[0].pins.seed_hex
    assert found.forecast_ids == (assessment.forecasts[0].forecast_id,)


def test_combination_lot(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    models[("amoxicillin_clavulanate_875_125_tab", "amoxicillin")] = _thermal(-19.5, limit=None)
    models[("amoxicillin_clavulanate_875_125_tab", "clavulanic_acid")] = _thermal(-15.5, limit=None)
    assessment = pipeline.assess_lot(fast_bundle, "lot-amc-001", "2027-10-01T00:00:00Z", [], [], [])
    assert [e.ingredient_id for e in assessment.estimates] == ["amoxicillin", "clavulanic_acid"]
    assert [f.ingredient_id for f in assessment.forecasts] == ["amoxicillin", "clavulanic_acid"]
    found = assessment.decision
    assert found.state == "adjust"
    assert [(r.code, r.ingredient_id) for r in found.reasons if r.severity == "blocking"] == [
        ("below_lower_limit", "clavulanic_acid")
    ]
    assert found.alternative_lot_ids == ("lot-amc-002",)


def _three_lots(bundle: ConfigBundle) -> ConfigBundle:
    lots: Any = copy.deepcopy(bundle.documents["lots"].data)
    keep = {"lot-apap-001", "lot-apap-002", "lot-ibu-001"}
    lots["lots"] = [lot for lot in lots["lots"] if lot["lot_id"] in keep]
    return b.bundle_with(bundle, "lots", lots)


def test_assess_inventory_orders_and_replays(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    small = _three_lots(fast_bundle)
    models[("acetaminophen_325_tab", "acetaminophen")] = _thermal(-19.0, limit=None)
    models[("ibuprofen_400_tab", "ibuprofen")] = _thermal(-17.5, limit=None)
    as_of = "2027-11-01T00:00:00Z"
    inputs = _inputs(
        as_of, assays=[_assay("a1", "2027-10-01T00:00:00Z", 97.0, lot_id="lot-apap-001", sd=1.0)]
    )
    inventory = pipeline.assess_inventory(small, inputs)
    states = [(a.lot_id, a.decision.state) for a in inventory.assessments]
    assert [state for _, state in states] == sorted(
        (s for _, s in states), key=["in_specification", "adjust", "do_not_use"].index
    )
    keys = [a.decision.ordering_key for a in inventory.assessments]
    assert list(inventory.assessments) == [
        next(x for x in inventory.assessments if x.lot_id == d.lot_id)
        for d in decision.sort_lots([a.decision for a in inventory.assessments])
    ]
    assert keys[-1].lot_id == "lot-ibu-001"
    assert inventory.assessments[-1].decision.state == "adjust"
    apap = {a.lot_id: a for a in inventory.assessments}
    first = next(a.lot_id for a in inventory.assessments if a.lot_id.startswith("lot-apap"))
    other = "lot-apap-002" if first == "lot-apap-001" else "lot-apap-001"
    assert apap[other].decision.alternative_lot_ids == (first,)
    recorded = to_json(inventory)
    assert pipeline.replay(small, inputs, recorded)
    tampered: Any = copy.deepcopy(recorded)
    tampered["assessments"][0]["estimates"][0]["mean"] += 1e-9
    assert not pipeline.replay(small, inputs, tampered)
    repinned: Any = copy.deepcopy(recorded)
    repinned["assessments"][0]["estimates"][0]["pins"]["seed_hex"] = "f" * 32
    with pytest.raises(DeterminismError):
        pipeline.replay(small, inputs, repinned)
    assert canonical_json_bytes(to_json(pipeline.assess_inventory(small, inputs))) == canonical_json_bytes(
        recorded
    )
    # Spec 15.6: every output validates against its schema.
    problems = schema_violations(recorded, "pharmacy/inventory-assessment.schema.json")
    for assessment in inventory.assessments:
        for estimate in assessment.estimates:
            problems += schema_violations(to_json(estimate), "pharmacy/potency-estimate.schema.json")
        for forecast in assessment.forecasts:
            problems += schema_violations(to_json(forecast), "pharmacy/forecast.schema.json")
        problems += schema_violations(to_json(assessment.decision), "pharmacy/lot-decision.schema.json")
    assert problems == []
    assert schema_violations(cast(JSONValue, inputs), "pharmacy/inputs.schema.json") == []


def test_inventory_skips_inactive_lots(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    lots: Any = copy.deepcopy(_three_lots(fast_bundle).documents["lots"].data)
    for lot in lots["lots"]:
        if lot["lot_id"] != "lot-ibu-001":
            lot["status"] = "consumed"
    small = b.bundle_with(fast_bundle, "lots", lots)
    inventory = pipeline.assess_inventory(small, _inputs(GROUND))
    assert [a.lot_id for a in inventory.assessments] == ["lot-ibu-001"]
    assert inventory.assessments[0].decision.alternative_lot_ids == ()


# =========================================================================================
# Flags, regimes, radiation and edge cases (spec 6, 8, 9.9, 12)
# =========================================================================================


def test_unconverged_monte_carlo_is_flagged(bundle: ConfigBundle, models: ModelTable) -> None:
    data: Any = copy.deepcopy(bundle.documents["engine-settings"].data)
    data["monte_carlo"]["n_dig"]["value"] = 4
    data["monte_carlo"]["max_batches"]["value"] = 2
    strict = b.bundle_with(bundle, "engine-settings", data)
    estimate = pipeline.estimate_potency(strict, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assert not estimate.monte_carlo.converged
    assert "mc_not_converged" in estimate.flags
    (placard,) = [p for p in estimate.placards if p.kind == "monte_carlo"]
    assert placard.unit == "percent"
    assert placard.extent >= 0.0


def _solution(product_id: str, ingredient_id: str) -> StabilityModel:
    base = _thermal(-19.5, limit=None)(product_id, ingredient_id)
    return dataclasses.replace(base, dose_form_family="solution", freezing_point_k=272.15)


def test_frozen_solution_blocks_a_do_not_freeze_product(
    fast_bundle: ConfigBundle, models: ModelTable
) -> None:
    models[("epinephrine_0_1mg_ml_syringe", "epinephrine")] = _solution
    cold = _transit_series(temperature=(270.0, 290.0))
    assessment = pipeline.assess_lot(fast_bundle, "lot-epi-syr-001", TRANSIT, cold, [], [])
    (estimate,) = assessment.estimates
    assert "regime_switch" in estimate.flags
    assert "outside_validated_range" in estimate.flags
    assert any(p.kind == "regime_frozen" for p in estimate.placards)
    assert assessment.decision.state == "do_not_use"
    assert "regime_switch" in [r.code for r in assessment.decision.reasons]


def test_radiosensitive_dose_and_reviewed_profile(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    formulary: Any = copy.deepcopy(fast_bundle.documents["formulary"].data)
    product = next(p for p in formulary["products"] if p["product_id"] == "acetaminophen_325_tab")
    product["radiosensitive"] = True
    product["degradant_profile_reviewed"] = True
    changed = b.bundle_with(fast_bundle, "formulary", formulary)
    hot = _transit_series()
    hot[2] = b.constant_series(
        "trk-d", "trk-dos1", "transit_kit", "dose_rate_gy_per_s", "Gy/s", LAUNCH, TRANSIT, 1800.0, 1e-5
    )
    estimate = pipeline.estimate_potency(changed, "lot-apap-001", "acetaminophen", TRANSIT, hot, [], [])
    assert estimate.radiation.radiosensitive
    assert estimate.radiation.threshold_exceeded
    assert estimate.radiation.cumulative_absorbed_dose_gy > estimate.radiation.radiosensitive_threshold_gy
    assert "radiosensitive_dose_exceeded" in estimate.flags
    assert "degradant_profile_not_reviewed" not in estimate.flags
    assert 0.0 < estimate.radiation.upper_bound_loss_fraction < 1.0


@pytest.mark.parametrize("method", ["g_value_direct", "g_value_indirect"])
def test_radiation_method_and_cache_keys_come_from_the_model(
    method: str, fast_bundle: ConfigBundle, models: ModelTable
) -> None:
    def with_detail(product_id: str, ingredient_id: str) -> StabilityModel:
        base = _thermal(-19.5)(product_id, ingredient_id)
        detail = ComputedDetail(method, None, 0.0, 0.5, (), (), ("cache-b", "cache-a"))  # type: ignore[arg-type]
        rad = dataclasses.replace(base.pathways[1], computed=detail)
        return dataclasses.replace(base, pathways=(base.pathways[0], rad))

    models[("acetaminophen_325_tab", "acetaminophen")] = with_detail
    estimate = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assert estimate.radiation.method == method
    assert estimate.pins.computed_cache_keys == ("cache-a", "cache-b")


def test_explicit_model_argument_skips_the_builder(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    def refuse(product_id: str, ingredient_id: str) -> StabilityModel:
        raise AssertionError("the builder must not be called")

    models[("acetaminophen_325_tab", "acetaminophen")] = refuse
    model = _thermal(-19.5)("acetaminophen_325_tab", "acetaminophen")
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [], model=model
    )
    assert estimate.monte_carlo.converged


def test_free_group_shares_are_traced(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    """Spec 9.5: the trace reports delta_i, s_m,i and pi_i of each free group member, and
    nothing for anchored members."""
    base = _thermal(-19.5)("acetaminophen_325_tab", "acetaminophen")
    path = base.pathways[0]
    lse = math.log(1.0 + math.exp(-2.0))
    members = []
    for suffix, delta, sd in (("a", 0.0, 0.5), ("b", -2.0, 1.5)):
        mean = (-19.5 + delta - lse, *path.parameters.mean[1:])
        block = dataclasses.replace(path.parameters, mean=mean, shared_weight=1.0)
        members.append(
            dataclasses.replace(
                path,
                pathway_id=f"acetaminophen:free_{suffix}",
                parameters=block,
                free_share=FreeShare(delta, sd),
                degradant=None,
            )
        )
    model = dataclasses.replace(
        base, pathways=tuple(sorted((*base.pathways, *members), key=lambda p: p.pathway_id))
    )
    estimate = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [], model=model
    )
    trace = {t.quantity: t.value for t in estimate.trace}
    assert trace["free_share_delta_ln:acetaminophen:free_b"] == -2.0
    assert trace["free_share_method_sd_ln:acetaminophen:free_a"] == 0.5
    assert trace["free_share_pi:acetaminophen:free_a"] == pytest.approx(
        1.0 / (1.0 + math.exp(-2.0)), rel=1e-15
    )
    assert trace["free_share_pi:acetaminophen:free_b"] == pytest.approx(
        math.exp(-2.0) / (1.0 + math.exp(-2.0)), rel=1e-14
    )
    assert not any(key.startswith("free_share") and path.pathway_id in key for key in trace)
    plain = pipeline.estimate_potency(
        fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [], model=base
    )
    assert not any(t.quantity.startswith("free_share") for t in plain.trace)


def test_estimate_and_forecast_at_manufacture(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    made = "2027-06-24T00:00:00Z"
    assessment = pipeline.assess_lot(fast_bundle, "lot-apap-002", made, [], [], [])
    (estimate,) = assessment.estimates
    assert estimate.exposure.mkt_k is None
    assert estimate.exposure.duration_s == 0.0
    assert _quantile(estimate, 0.5) == pytest.approx(100.0, abs=0.1)  # the initial assay alone
    assert assessment.forecasts[0].status == "within_horizon"


def test_repeat_projection_with_a_partial_last_window(fast_bundle: ConfigBundle, models: ModelTable) -> None:
    """A 55-hour window does not divide the 10-year horizon, so the last window is clipped;
    draws that never cross end at the horizon potency of a brute-force evaluation of the
    whole projected history (periodic jump plus the clipped window)."""
    as_of = "2028-01-12T07:00:00Z"
    config = pipeline._config(fast_bundle)
    history = pipeline._history(config, "lot-apap-001", as_of, _transit_series(as_of), [], [])
    projection = pipeline._projection(config, history)
    horizon = float(fast_bundle.settings["forecast"]["horizon_s"]["value"])
    assert projection.length_s == 55 * 3600.0
    assert math.fmod(horizon, projection.length_s) > 0.0
    model = b.thermal_model(product_id="acetaminophen_325_tab", ingredient_id="acetaminophen", ln_k=-21.0)
    n = 2
    draws = uncertainty.draw_parameters(
        model, 100.0, 1.5, config.zones, history.zone_ids, uncertainty.batch_generator(3, 0), n
    )
    result = kinetics.evaluate_draws(
        model,
        history.exposure,
        history.trace,
        history.steps,
        draws,
        config.zones,
        config.packages,
        config.settings,
    )
    theta_d, theta_r, _ = kinetics.pathway_sums(model, result.theta, config.settings)
    c0 = np.full(n, 1000.0)
    crossings = pipeline._march(config, history, model, projection, draws, c0, theta_d, theta_r, 90.0)
    assert crossings.kind.tolist() == [2, 2]
    assert crossings.partial is None
    scaled = dataclasses.replace(draws, c0_percent=c0)
    for j in range(n):
        brute = _brute_potency(config, history, projection, model, scaled, j, horizon)
        assert crossings.fixed[j] == pytest.approx(brute, rel=1e-9)
        assert crossings.fixed[j] < 1000.0


def test_pipeline_error_paths(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(InputError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", "yesterday", [], [], [])
    early = _assay("a0", "2027-01-01T00:00:00Z", 99.0)
    with pytest.raises(ObservationError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [early], [])
    negative = _assay("a1", "2027-07-31T00:00:00Z", -40.0, sd=0.3)
    with pytest.raises(ComputationError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [negative], [])
    lots: Any = copy.deepcopy(fast_bundle.documents["lots"].data)
    lots["lots"][1]["custody"][0]["zone_id"] = "venus_base"
    with pytest.raises(ConfigReferenceError):
        pipeline.estimate_potency(
            b.bundle_with(fast_bundle, "lots", lots), "lot-apap-002", "acetaminophen", GROUND, [], [], []
        )
    lots = copy.deepcopy(fast_bundle.documents["lots"].data)
    lots["lots"][1]["product_id"] = "unobtainium_tab"
    with pytest.raises(ConfigReferenceError):
        pipeline.estimate_potency(
            b.bundle_with(fast_bundle, "lots", lots), "lot-apap-002", "acetaminophen", GROUND, [], [], []
        )

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(InputError):
        pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])


def test_conflict_checked_at_initial_batches(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = pipeline._Evaluation.__call__
    first_pass: list[int] = []

    def record(
        self: pipeline._Evaluation, batch: int, generator: np.random.Generator
    ) -> uncertainty.BatchOutcome:
        if self.reanchor is None:
            first_pass.append(batch)
        return original(self, batch, generator)

    monkeypatch.setattr(pipeline._Evaluation, "__call__", record)
    wild = _assay("early-conflict", "2027-07-31T00:00:00Z", 60.0, sd=0.3)
    result = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [wild], [])
    assert "assay_model_conflict" in result.flags
    assert len(first_pass) == int(fast_bundle.settings["monte_carlo"]["min_batches"]["value"])


def test_forecast_reuses_draws(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = uncertainty.draw_parameters
    calls: list[int] = []

    def record(*args: Any, **kwargs: Any) -> kinetics.DrawBlock:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(uncertainty, "draw_parameters", record)
    result = pipeline.assess_lot(fast_bundle, "lot-apap-002", GROUND, [], [], [])
    assert len(calls) == sum(e.monte_carlo.n_batches for e in result.estimates)


def test_failed_probe_discards_compressed_estimate(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def rejected(*args: Any, **kwargs: Any) -> kinetics.CheckedDrawResult:
        calls.append(1)
        result = kinetics.evaluate_draws(*args, **kwargs)
        altered = dataclasses.replace(result, theta=result.theta * 2.0)
        return kinetics.CheckedDrawResult(altered, "exact", None, True)

    monkeypatch.setattr(kinetics, "evaluate_draws_checked", rejected)
    result = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assert calls
    entries = {entry.quantity: entry.value for entry in result.trace}
    assert entries["history_evaluator"] == "exact"
    assert entries["compression_probe_relative_error"] is None
    assert entries["compression_fell_back"] == 1.0

    def exact(*args: Any, **kwargs: Any) -> kinetics.CheckedDrawResult:
        return kinetics.CheckedDrawResult(kinetics.evaluate_draws(*args, **kwargs), "exact", 0.0, False)

    monkeypatch.setattr(kinetics, "evaluate_draws_checked", exact)
    reference = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assert result.quantiles == reference.quantiles
    assert result.monte_carlo == reference.monte_carlo


def test_one_batch_still_reanchors_starved_assay_weights(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings: Any = copy.deepcopy(fast_bundle.settings)
    settings["monte_carlo"]["min_batches"]["value"] = 1
    settings["monte_carlo"]["max_batches"]["value"] = 1
    single = b.bundle_with(fast_bundle, "engine-settings", settings)
    monkeypatch.setattr(update, "conflict_probability", lambda *_: 1.0)
    monkeypatch.setattr(update, "effective_sample_size", lambda *_: 1.0)
    assay = _assay("starved", "2027-07-31T00:00:00Z", 98.0, sd=0.3)
    result = pipeline.estimate_potency(single, "lot-apap-002", "acetaminophen", GROUND, [], [assay], [])
    assert "assay_reanchored" in result.flags
    assert "assay_model_conflict" not in result.flags
    assert "mc_not_converged" not in result.flags


def test_mixed_checked_batches_report_compression(
    fast_bundle: ConfigBundle, models: ModelTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def alternating(*args: Any, **kwargs: Any) -> kinetics.CheckedDrawResult:
        calls.append(1)
        result = kinetics.evaluate_draws(*args, **kwargs)
        evaluator = "compressed" if len(calls) % 2 else "exact"
        return kinetics.CheckedDrawResult(result, evaluator, 0.0, False)

    monkeypatch.setattr(kinetics, "evaluate_draws_checked", alternating)
    result = pipeline.estimate_potency(fast_bundle, "lot-apap-002", "acetaminophen", GROUND, [], [], [])
    assert len(calls) >= 2
    entries = {entry.quantity: entry.value for entry in result.trace}
    assert entries["history_evaluator"] == "compressed"
    assert entries["compression_fell_back"] == 0.0
