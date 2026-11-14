"""Round 2 numerical contracts and their failure paths."""

import copy
import dataclasses
import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from pharmacy import decision, fit, kinetics, microclimate, uncertainty
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K
from pharmacy.errors import ComputationError, InputError, ModelDomainError
from pharmacy.types import EngineSettingsDoc, ZoneCorrection
from tests.unit.core import builders as b
from tests.unit.core.test_fit import _hand_fit


def test_zone_correction_moments_and_union(bundle: ConfigBundle) -> None:
    zones = {z["zone_id"]: copy.deepcopy(z) for z in bundle.zones["zones"]}
    zones["station_kit"]["rate_multiplier"].update({"family": "lognormal", "mu": 0.4, "sigma": 0.3})
    model = b.thermal_model()
    # s_p^2 = 1, s_c^2 = 0.25, s_post^2 = 0.2, lambda = 0.8, w = 0.2.
    path = dataclasses.replace(
        model.pathways[0],
        parameters=b.block(["ln_k_ref_per_s"], [-18.0], [math.sqrt(0.2)], shared_weight=0.2),
        zone_correction=ZoneCorrection("station_kit", 0.8, 0.3),
    )
    model = dataclasses.replace(model, pathways=(path,))
    draws = uncertainty.draw_parameters(
        model, 100.0, 0.0, zones, ["ground_storage"], uncertainty.batch_generator(91282, 0), 300_000
    )
    assert set(draws.zone_ln_multiplier) == {"ground_storage", "station_kit"}
    rate = draws.ln_k_ref_per_s[:, 0]
    factor = draws.zone_ln_multiplier["station_kit"]
    assert np.var(rate, ddof=1) == pytest.approx(0.2, abs=0.003)
    assert np.cov(rate, factor)[0, 1] == pytest.approx(-0.8 * 0.09, abs=0.001)


def test_complete_anchor_weight_cancels_zone_variate(bundle: ConfigBundle) -> None:
    zones = {z["zone_id"]: copy.deepcopy(z) for z in bundle.zones["zones"]}
    zones["station_kit"]["rate_multiplier"].update({"family": "lognormal", "mu": 0.4, "sigma": 0.3})
    model = b.thermal_model()
    path = dataclasses.replace(
        model.pathways[0],
        parameters=b.block(["ln_k_ref_per_s"], [-18.0], [0.5]),
        zone_correction=ZoneCorrection("station_kit", 1.0, 0.3),
    )
    model = dataclasses.replace(model, pathways=(path,))
    seed, n = 831, 1000
    draws = uncertainty.draw_parameters(
        model, 100.0, 0.0, zones, ["station_kit"], uncertainty.batch_generator(seed, 0), n
    )
    # Only the pathway's own z remains. Column 10 (zone multiplier) cancels algebraically.
    z = uncertainty.batch_generator(seed, 0).standard_normal((n, 13))
    expected = -18.0 + 0.4 + math.sqrt(0.25 - 0.09) * z[:, 2]
    actual = draws.ln_k_ref_per_s[:, 0] + draws.zone_ln_multiplier["station_kit"]
    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=4e-15)


@pytest.mark.parametrize(
    ("weight", "factor_sd", "variance"), [(1.0, 0.4, 0.25), (1.0, 0.3, 0.04), (0.0, 0.3, 0.25)]
)
def test_invalid_zone_correction(
    bundle: ConfigBundle, weight: float, factor_sd: float, variance: float
) -> None:
    zones = {z["zone_id"]: copy.deepcopy(z) for z in bundle.zones["zones"]}
    zones["station_kit"]["rate_multiplier"].update({"family": "lognormal", "mu": 0.4, "sigma": 0.3})
    model = b.thermal_model()
    path = dataclasses.replace(
        model.pathways[0],
        parameters=b.block(["ln_k_ref_per_s"], [-18.0], [math.sqrt(variance)]),
        zone_correction=ZoneCorrection("station_kit", weight, factor_sd),
    )
    with pytest.raises(ModelDomainError):
        uncertainty.draw_parameters(
            dataclasses.replace(model, pathways=(path,)),
            100.0,
            0.0,
            zones,
            [],
            uncertainty.batch_generator(5, 0),
            10,
        )


def test_one_batch_disables_stopping_and_initial_hook(bundle: ConfigBundle) -> None:
    settings = copy.deepcopy(bundle.settings["monte_carlo"])
    settings["min_batches"]["value"] = settings["max_batches"]["value"] = 1
    n = uncertainty.jcgm_batch_size(settings["coverage_probability"]["value"])
    result = uncertainty.run_adaptive(
        lambda _, rng: uncertainty.BatchOutcome(rng.normal(90, 1, n), None, {}), 5, settings
    )
    assert result.converged
    assert result.n_batches == 1
    stopped = uncertainty.run_adaptive(
        lambda _, rng: uncertainty.BatchOutcome(rng.normal(90, 1, n), None, {}),
        5,
        settings,
        stop_after_initial=lambda _: True,
    )
    assert not stopped.converged
    assert stopped.n_batches == 1


def _packaged_observations(bundle: ConfigBundle) -> list[fit.StudyObservation]:
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == "original_bottle")
    out = []
    for temperature, rh in [(298.15, 60.0), (308.15, 65.0), (318.15, 75.0), (313.15, 30.0)]:
        for day in (30, 90, 180):
            duration = day * 86400.0
            segments = (
                b.segment(0.0, duration / 2, temperature=temperature, rh=rh, package="original_bottle"),
                b.segment(
                    duration / 2,
                    duration,
                    temperature=temperature + 1.0,
                    rh=rh - 2.0,
                    package="original_bottle",
                ),
            )
            trace = microclimate.internal_rh_trace(segments, {package["package_id"]: package})
            theta = sum(
                kinetics.rate_constant(
                    -18.0, 80000.0, 0.04, seg.temperature_k, seg.relative_humidity_percent, 298.15, 40.0
                )
                * kinetics.humidity_integral(
                    0.04 * (inside - seg.relative_humidity_percent), seg.t1_s - seg.t0_s, tau
                )
                for seg, inside, tau in zip(
                    segments, trace.rh_in_start_high, trace.time_constant_s, strict=True
                )
            )
            out.append(
                fit.StudyObservation(
                    duration,
                    temperature,
                    rh,
                    100.0 * math.exp(-theta),
                    0.2,
                    str(temperature),
                    segments,
                    package,
                )
            )
    return out


def test_fit_package_and_log_share_estimate_physics(bundle: ConfigBundle) -> None:
    observations = _packaged_observations(bundle)
    result = fit.fit_stability_study(
        observations,
        "F1",
        298.15,
        40.0,
        fit_humidity=True,
        fit_initial=False,
        absolute_weights=True,
        numerics=bundle.settings["numerics"],
    )
    np.testing.assert_allclose(result.mean, [-18.0, 80000.0, 0.04], rtol=2e-7)
    assert result.rh_max_percent is not None
    assert result.rh_max_percent < 50.0  # bottle interior, never the chamber's 75 percent
    assert result.rh_min_percent is not None
    assert fit.select_model([result, dataclasses.replace(result, aicc=result.aicc)]) is result
    observation = dataclasses.replace(observations[-1], chamber_log=(), package=None, time_s=86400.0)
    interval = fit.prediction_interval(result, observation)
    assert interval == fit.prediction_interval(result, observation)
    measured = fit.prediction_interval(result, observation, assay_sd_percent=1.0)
    assert measured[1] - measured[0] > interval[1] - interval[0]
    with pytest.raises(InputError, match="hash"):
        fit.prediction_interval(dataclasses.replace(result, inputs_sha256=None), observation)
    with pytest.raises(InputError, match="sd"):
        fit.prediction_interval(result, observation, assay_sd_percent=-1.0)
    with pytest.raises(InputError, match="covariance"):
        fit.prediction_interval(dataclasses.replace(result, covariance=((math.nan,),)), observation)


@pytest.mark.parametrize("case", ["gap", "end", "temperature", "rh", "missing_rh"])
def test_fit_bad_chamber_logs(bundle: ConfigBundle, case: str) -> None:
    observations = _packaged_observations(bundle)
    first = observations[0]
    segment = first.chamber_log[0]
    changes: dict[str, Any] = {
        "gap": {"t0_s": 1.0},
        "end": {"t1_s": 1.0},
        "temperature": {"temperature_k": -1.0},
        "rh": {"relative_humidity_percent": 101.0},
    }
    observations[0] = (
        dataclasses.replace(first, rh_percent=None)
        if case == "missing_rh"
        else dataclasses.replace(first, chamber_log=(dataclasses.replace(segment, **changes[case]),))
    )
    with pytest.raises(InputError):
        fit.fit_stability_study(
            observations, "F1", 298.15, 40.0, fit_humidity=True, fit_initial=False, absolute_weights=True
        )


def test_measured_range_margin_distance(bundle: ConfigBundle) -> None:
    model = b.thermal_model()
    path = model.pathways[0]
    block = dataclasses.replace(
        path.parameters,
        estimates=tuple(
            dataclasses.replace(e, source_layer="measured_model") for e in path.parameters.estimates
        ),
    )
    path = dataclasses.replace(path, parameters=block)
    model = dataclasses.replace(model, pathways=(path,))
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    minimum = path.validated_range.temperature_min_k
    margin = bundle.settings["validated_range"]["temperature_below_min_margin_k"]["value"]
    for delta in (0.0, 1.0):
        segments = (b.segment(0.0, 86400.0, temperature=minimum - margin - delta),)
        exposure = b.lot_exposure(segments)
        trace = microclimate.internal_rh_trace(segments, packages)
        nominal = kinetics.nominal_thetas(model, exposure, trace, zones, packages, bundle.settings)
        placards = kinetics.placards_for(model, exposure, nominal, bundle.settings)
        temperature = [p for p in placards if p.kind == "temperature_range"]
        assert len(temperature) == int(delta > 0)
        if temperature:
            assert temperature[0].extent == delta
    broken: EngineSettingsDoc = copy.deepcopy(bundle.settings)
    del broken["validated_range"]
    with pytest.raises(ModelDomainError, match="validated_range"):
        kinetics.nominal_thetas(model, exposure, trace, zones, packages, broken)


def _evaluation_inputs(
    bundle: ConfigBundle, *, package: str = "original_vial"
) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    model = b.thermal_model()
    segments = tuple(
        b.segment(
            float(i * 1800),
            float((i + 1) * 1800),
            package=package,
            temperature=298.15 + i % 3,
            rh=45.0 + i % 5,
        )
        for i in range(600)
    )
    history = b.lot_exposure(segments)
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    trace = microclimate.internal_rh_trace(segments, packages)
    steps = {
        "station_kit": microclimate.unit_step_response(
            segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=False
        )
    }
    draws = uncertainty.draw_parameters(
        model, 100.0, 0.0, zones, ["station_kit"], uncertainty.batch_generator(338, 0), 31
    )
    return model, history, trace, steps, draws, zones, packages


def test_exact_threads_are_bit_identical(bundle: ConfigBundle) -> None:
    args = _evaluation_inputs(bundle, package="repackaged_bag")
    settings = copy.deepcopy(bundle.settings)
    settings["numerics"]["worker_threads"]["value"] = 1
    reference = kinetics.evaluate_draws(*args, settings)
    for threads in (2, 4, 0):
        settings["numerics"]["worker_threads"]["value"] = threads
        actual = kinetics.evaluate_draws(*args, settings)
        for field in dataclasses.fields(reference):
            np.testing.assert_array_equal(getattr(actual, field.name), getattr(reference, field.name))
    settings["numerics"]["worker_threads"]["value"] = -1
    with pytest.raises(ModelDomainError, match="worker_threads"):
        kinetics.evaluate_draws(*args, settings)
    del settings["numerics"]["worker_threads"]
    with pytest.raises(ModelDomainError, match="worker_threads"):
        kinetics.evaluate_draws(*args, settings)


def test_checked_dictionary_preserves_theta_and_degradant_sequence(bundle: ConfigBundle) -> None:
    args = _evaluation_inputs(bundle)
    checked = kinetics.evaluate_draws_checked(*args, bundle.settings)
    reference = kinetics.evaluate_draws(*args, bundle.settings)
    assert checked.history_evaluator == "compressed"
    assert not checked.fell_back
    assert checked.probe_relative_error is not None
    assert checked.probe_relative_error < 1e-12
    for field in dataclasses.fields(reference):
        np.testing.assert_allclose(
            getattr(checked.result, field.name), getattr(reference, field.name), rtol=1e-12, atol=1e-14
        )
    exchange = kinetics.evaluate_draws_checked(
        *_evaluation_inputs(bundle, package="original_bottle"), bundle.settings
    )
    assert exchange.history_evaluator == "exact"


@pytest.mark.parametrize("error", [None, 1e-3])
def test_checked_history_falls_back(
    bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch, error: float | None
) -> None:
    args = _evaluation_inputs(bundle)
    monkeypatch.setattr(kinetics, "_probe_error", lambda *_: error)
    checked = kinetics.evaluate_draws_checked(*args, bundle.settings)
    assert checked.history_evaluator == "exact"
    assert checked.fell_back
    reference = kinetics.evaluate_draws(*args, bundle.settings)
    np.testing.assert_array_equal(checked.result.theta, reference.theta)


def test_checked_history_errors(bundle: ConfigBundle) -> None:
    args = _evaluation_inputs(bundle)
    settings = copy.deepcopy(bundle.settings)
    settings["numerics"]["compression_relative_tolerance"]["value"] = -1.0
    with pytest.raises(ModelDomainError, match="compression tolerance"):
        kinetics.evaluate_draws_checked(*args, settings)
    del settings["numerics"]["compression_relative_tolerance"]
    with pytest.raises(ModelDomainError, match="compression_relative_tolerance"):
        kinetics.evaluate_draws_checked(*args, settings)
    bad = (*args[:4], dataclasses.replace(args[4], n=0), *args[5:])
    with pytest.raises(InputError, match="at least one draw"):
        kinetics.evaluate_draws_checked(*bad, bundle.settings)
    assert kinetics._probe_error(np.zeros(2), np.zeros(2)) == 0.0
    assert kinetics._probe_error(np.ones(2), np.zeros(2)) is None


def test_q3b_written_precision_and_invalid_bands(bundle: ConfigBundle) -> None:
    assert decision.q3b_exceeds(0.101, 0.10, decimals=2) is False
    assert decision.q3b_exceeds(0.105, 0.10, decimals=2) is True
    with pytest.raises(InputError, match="decimals"):
        decision.q3b_exceeds(0.1, 0.1, decimals=-1)
    settings = copy.deepcopy(bundle.settings["q3b"])
    settings["reporting"] = []
    with pytest.raises(InputError, match="no Q3B row"):
        decision.q3b_thresholds(1.0, settings=settings)
    settings = copy.deepcopy(bundle.settings["q3b"])
    tdi = settings["identification"][0]["tdi"]
    assert tdi is not None
    tdi["unit"] = "kg"
    with pytest.raises(InputError, match="TDI unit"):
        decision.q3b_thresholds(0.5, settings=settings)


def test_nullable_information_criteria_and_prediction_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    times = np.array([1e6, 2e6, 1e6, 2e6])
    temperatures = np.array([298.15, 298.15, 308.15, 308.15])
    x = (temperatures - 298.15) / (temperatures * 298.15 * GAS_CONSTANT_J_PER_MOL_K)
    theta = np.exp(-18.0 + 80000 * x + np.log(times))
    responses = 100 * (1 - np.asarray(kinetics.conversion_from_theta_array("F0", theta)))
    observations = [
        fit.StudyObservation(float(t), float(temp), None, float(response), None, str(temp))
        for t, temp, response in zip(times, temperatures, responses, strict=True)
    ]
    monkeypatch.setattr(
        fit, "least_squares", lambda *_, **__: SimpleNamespace(status=1, x=np.array([-18.0, 80000.0]))
    )
    result = fit.fit_stability_study(
        observations, "F0", 298.15, 40.0, fit_humidity=False, fit_initial=False, absolute_weights=False
    )
    assert result.aic is None
    assert result.aicc is None
    assert result.bic is None
    observation = observations[0]
    with pytest.raises(InputError, match="hash or covariance"):
        fit.prediction_interval(dataclasses.replace(result, inputs_sha256="invalid"), observation)
    with pytest.raises(InputError, match="hash or covariance"):
        fit.prediction_interval(
            dataclasses.replace(result, covariance=((-1.0, 0.0), (0.0, 1.0))), observation
        )
    with pytest.raises(InputError, match="requires ln_k"):
        fit.prediction_interval(dataclasses.replace(result, names=("missing", "ea_j_per_mol")), observation)
    with pytest.raises(InputError, match="initial potency"):
        fit.prediction_interval(dataclasses.replace(result, initial_percent=None), observation)
    smaller = fit.fit_stability_study(
        [dataclasses.replace(o, sd_percent=1.0) for o in observations[:3]],
        "F0",
        298.15,
        40.0,
        fit_humidity=False,
        fit_initial=False,
        absolute_weights=True,
    )
    assert smaller.aicc is None


def test_prediction_rejects_nonfinite_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    fitted = dataclasses.replace(_hand_fit("F1", 1.0), inputs_sha256="a" * 64, initial_percent=100.0)
    observation = fit.StudyObservation(100.0, 298.15, None, 0.0, None, "prediction")
    monkeypatch.setattr(fit, "_predict", lambda *_: math.nan)
    with pytest.raises(ComputationError, match="draws are nonfinite"):
        fit.prediction_interval(fitted, observation)
    monkeypatch.setattr(fit, "_predict", lambda *_: 100.0)
    monkeypatch.setattr(np, "quantile", lambda *_: (math.inf, math.inf))
    with pytest.raises(ComputationError, match="quantiles are nonfinite"):
        fit.prediction_interval(fitted, observation)


def test_logged_fit_requires_rh_even_without_humidity_term(bundle: ConfigBundle) -> None:
    observations = _packaged_observations(bundle)
    observations[0] = dataclasses.replace(observations[0], rh_percent=None)
    with pytest.raises(InputError, match="require RH"):
        fit.fit_stability_study(
            observations, "F1", 298.15, 40.0, fit_humidity=False, fit_initial=False, absolute_weights=True
        )


def test_logged_fit_rejects_nonfinite_humidity_integral(
    bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fit, "_humidity_integral_array", lambda x, *_: np.full_like(x, math.nan))
    with pytest.raises(ComputationError, match="humidity integral is nonfinite"):
        fit.fit_stability_study(
            _packaged_observations(bundle),
            "F1",
            298.15,
            40.0,
            fit_humidity=True,
            fit_initial=False,
            absolute_weights=True,
        )


def test_bounded_temperature_compression(bundle: ConfigBundle) -> None:
    model, history, _, _, draws, zones, packages = _evaluation_inputs(bundle)
    segments = tuple(
        dataclasses.replace(s, temperature_k=292.0 + 18.0 * i / len(history.segments))
        for i, s in enumerate(history.segments)
    )
    history = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(segments, packages)
    steps = {
        "station_kit": microclimate.unit_step_response(
            segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=False
        )
    }
    args = model, history, trace, steps, draws, zones, packages
    checked = kinetics.evaluate_draws_checked(*args, bundle.settings)
    exact = kinetics.evaluate_draws(*args, bundle.settings)
    assert checked.history_evaluator == "compressed"
    assert not checked.fell_back
    np.testing.assert_allclose(checked.result.theta, exact.theta, rtol=1e-7, atol=1e-14)
    np.testing.assert_allclose(
        checked.result.degradant_percent, exact.degradant_percent, rtol=1e-7, atol=1e-14
    )
    settings = copy.deepcopy(bundle.settings)
    settings["numerics"]["compression_relative_tolerance"]["value"] = 0.0
    found = kinetics.evaluate_draws_checked(*args, settings)
    assert found.history_evaluator == "exact"


def _continuous_inputs(
    bundle: ConfigBundle, low: float = 292.0, high: float = 310.0
) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    model, history, _, _, draws, zones, packages = _evaluation_inputs(bundle)
    segments = tuple(
        dataclasses.replace(s, temperature_k=low + (high - low) * i / (len(history.segments) - 1))
        for i, s in enumerate(history.segments)
    )
    history = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(segments, packages)
    steps = {
        "station_kit": microclimate.unit_step_response(
            segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=False
        )
    }
    return model, history, trace, steps, draws, zones, packages


@pytest.mark.parametrize("case", ["rounding", "underflow", "nonpositive", "nonfinite", "outside"])
def test_interpolation_domain_and_arithmetic_guards(bundle: ConfigBundle, case: str) -> None:
    args = _continuous_inputs(bundle, 318.0, 322.0) if case == "outside" else _continuous_inputs(bundle)
    model, history, trace, steps, draws, zones, packages = args
    settings = copy.deepcopy(bundle.settings)
    if case == "rounding":
        settings["numerics"]["compression_relative_tolerance"]["value"] = float(np.finfo(np.float64).eps)
    if case == "underflow":
        values = draws.ln_k_ref_per_s.copy()
        values[:, 0] = -1000.0
        draws = dataclasses.replace(draws, ln_k_ref_per_s=values)
    if case == "nonpositive":
        draws = dataclasses.replace(draws, temperature_offset_k={"station_kit": np.full(draws.n, -300.0)})
    if case == "nonfinite":
        values = draws.ln_k_ref_per_s.copy()
        values[:, 0] = math.nan
        draws = dataclasses.replace(draws, ln_k_ref_per_s=values)
    if case in ("nonpositive", "nonfinite"):
        with pytest.raises(ComputationError):
            kinetics.evaluate_draws_checked(model, history, trace, steps, draws, zones, packages, settings)
    else:
        found = kinetics.evaluate_draws_checked(
            model, history, trace, steps, draws, zones, packages, settings
        )
        reference = kinetics.evaluate_draws(model, history, trace, steps, draws, zones, packages, settings)
        np.testing.assert_allclose(found.result.theta, reference.theta, rtol=1e-7, atol=1e-14)
        if case != "outside":
            assert found.history_evaluator == "exact"


def test_interpolation_small_constant_groups_and_group_memory_bound(bundle: ConfigBundle) -> None:
    model, history, _, _, draws, zones, packages = _continuous_inputs(bundle)
    for many_groups in (False, True):
        segments = tuple(
            dataclasses.replace(
                s,
                relative_humidity_percent=45.0 + i / 1000 if many_groups else (30.0 if i < 10 else 50.0),
                temperature_k=298.15 if i < 10 else s.temperature_k,
            )
            for i, s in enumerate(history.segments)
        )
        history = b.lot_exposure(segments)
        trace = microclimate.internal_rh_trace(segments, packages)
        steps = {"station_kit": (0.0,) * len(segments)}
        found = kinetics.evaluate_draws_checked(
            model, history, trace, steps, draws, zones, packages, bundle.settings
        )
        reference = kinetics.evaluate_draws(
            model, history, trace, steps, draws, zones, packages, bundle.settings
        )
        np.testing.assert_allclose(found.result.theta, reference.theta, rtol=1e-7, atol=1e-14)
        assert found.history_evaluator == ("exact" if many_groups else "compressed")


def test_humidity_free_pathway_compresses_exchanging_package(bundle: ConfigBundle) -> None:
    model, history, _, _, _, zones, packages = _continuous_inputs(bundle)
    path = dataclasses.replace(
        model.pathways[0],
        parameters=b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-18.0, 90000.0], [0.3, 12000.0]),
    )
    model = dataclasses.replace(model, pathways=(path,))
    segments = tuple(dataclasses.replace(s, package_id="original_bottle") for s in history.segments)
    history = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(segments, packages)
    steps = {
        "station_kit": microclimate.unit_step_response(
            segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=False
        )
    }
    draws = uncertainty.draw_parameters(
        model, 100, 0, zones, ["station_kit"], uncertainty.batch_generator(555, 0), 31
    )
    found = kinetics.evaluate_draws_checked(
        model, history, trace, steps, draws, zones, packages, bundle.settings
    )
    reference = kinetics.evaluate_draws(model, history, trace, steps, draws, zones, packages, bundle.settings)
    assert found.history_evaluator == "compressed"
    np.testing.assert_allclose(found.result.theta, reference.theta, rtol=1e-7, atol=1e-14)


def test_interpolation_preserves_rh_gap_offset_masks(bundle: ConfigBundle) -> None:
    model, history, _, _, draws, zones, packages = _continuous_inputs(bundle)
    assert packages["original_vial"]["moisture_model"]["kind"] == "none"
    segments = tuple(
        dataclasses.replace(s, gap_quantities=("relative_humidity",) if i % 2 else ())
        for i, s in enumerate(history.segments)
    )
    history = b.lot_exposure(segments)
    trace = microclimate.internal_rh_trace(segments, packages)
    steps = {
        "station_kit": microclimate.unit_step_response(
            segments, trace.time_constant_s, "station_kit", first_start_tracks_outside=True
        )
    }
    found = kinetics.evaluate_draws_checked(
        model, history, trace, steps, draws, zones, packages, bundle.settings
    )
    reference = kinetics.evaluate_draws(model, history, trace, steps, draws, zones, packages, bundle.settings)
    assert found.history_evaluator == "compressed"
    assert not found.fell_back
    np.testing.assert_allclose(found.result.theta, reference.theta, rtol=1e-7, atol=1e-14)
