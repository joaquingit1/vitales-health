"""Storage-aware anchors, correlated zone correction and single prior conversion."""

import copy
import math
from typing import Any

import numpy as np
import pytest

from pharmacy import microclimate, uncertainty
from pharmacy.chemistry import parameters as par
from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError, ModelDomainError
from pharmacy.types import Anchor
from tests.unit.chemistry.support import anchor_quadrature, find, reaction_class, unvalidated


def _anchor(bundle: ConfigBundle) -> Anchor:
    return copy.deepcopy(reaction_class(bundle, "ester_hydrolysis")["anchors"][0])


def _convert(bundle: ConfigBundle, anchor: Anchor, b: float = 0.04) -> tuple[float, float]:
    return par.anchor_at_reference(anchor, 120000.0, 24500.0, b, 0.025, 298.15, 40.0, bundle=bundle)


def _zone(bundle: ConfigBundle, mean: float, sd: float) -> ConfigBundle:
    def change(doc: dict[str, Any]) -> None:
        prior = find(doc["zones"], "zone_id", "station_kit")["rate_multiplier"]
        prior.update(family="lognormal", mu=mean, sigma=sd)

    return unvalidated(bundle, "storage-zones", change)


@pytest.mark.parametrize("package", [None, "repackaged_bag", "original_bottle", "original_blister"])
@pytest.mark.parametrize("b", [0.04, -0.04, 0.0])
def test_anchor_matches_mean_rate_quadrature(bundle: ConfigBundle, package: str | None, b: float) -> None:
    anchor = _anchor(bundle)
    anchor["package_id"] = package
    expected = anchor_quadrature(bundle, anchor, 120000.0, 24500.0, b, 0.025)
    assert _convert(bundle, anchor, b) == pytest.approx(expected, rel=1e-13)


def test_anchor_uses_engine_microclimate(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    original = microclimate.internal_rh_trace

    def record(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0][0].package_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(microclimate, "internal_rh_trace", record)
    _convert(bundle, _anchor(bundle))
    assert calls == ["repackaged_bag"]


def test_ground_anchor_zone_is_identity_and_has_no_correction(bundle: ConfigBundle) -> None:
    anchor = _anchor(bundle)
    anchor.update({"package_id": None, "storage_zone_id": "ground_storage"})
    mu, sd = _convert(bundle, anchor)
    assert mu == pytest.approx(-19.9192709843727849, rel=1e-13)
    assert sd == pytest.approx(0.759921373433750255, rel=1e-13)
    model = par.build_stability_model(
        bundle, "amoxicillin_clavulanate_875_125_tab", "amoxicillin", ["original_bottle"]
    )
    beta = next(p for p in model.pathways if p.reaction_class_id == "beta_lactam_hydrolysis")
    assert beta.computed is not None
    assert beta.zone_correction is None


def test_station_anchor_removes_mean_and_adds_variance(bundle: ConfigBundle) -> None:
    bundle = _zone(bundle, 0.4, 0.6)
    flight = _anchor(bundle)
    ground = copy.deepcopy(flight)
    ground["storage_zone_id"] = "ground_storage"
    mu_g, sd_g = _convert(bundle, ground)
    mu_f, sd_f = _convert(bundle, flight)
    assert mu_f - mu_g == pytest.approx(-0.4, abs=1e-14)
    assert sd_f**2 - sd_g**2 == pytest.approx(0.6**2, abs=1e-14)
    changed = _zone(bundle, 1.1, 0.6)
    assert _convert(changed, flight)[0] - mu_f == pytest.approx(-0.7, abs=1e-14)
    assert _convert(changed, flight)[1] == sd_f


def test_correction_weight_matches_closed_form_and_live_config(bundle: ConfigBundle) -> None:
    bundle = _zone(bundle, 0.4, 0.6)
    anchor = _anchor(bundle)
    _, sd_a = _convert(bundle, anchor)
    total = par.total_rate_prior(bundle, "solid")["ln_k_ref_per_s"]
    var_p = total["sigma"] ** 2
    method_sd = bundle.settings["computed_layer"]["same_molecule_sd_log10"]["value"] * math.log(10.0)
    var_c = sd_a**2 + method_sd**2
    var_post = 1.0 / (1.0 / var_p + 1.0 / var_c)
    model = par.build_stability_model(bundle, "aspirin_325_tab", "aspirin", ["original_bottle"])
    pathway = next(p for p in model.pathways if p.reaction_class_id == "ester_hydrolysis")
    correction = pathway.zone_correction
    assert correction is not None
    assert correction.zone_id == "station_kit"
    assert correction.factor_sd_ln == 0.6
    assert correction.weight == pytest.approx(var_post / var_c, rel=1e-14)
    assert correction.weight == pytest.approx(var_p / (var_p + var_c), rel=1e-14)
    changed = par.build_stability_model(
        _zone(bundle, 1.1, 0.6), "aspirin_325_tab", "aspirin", ["original_bottle"]
    )
    after = next(p for p in changed.pathways if p.reaction_class_id == "ester_hydrolysis")
    assert after.parameters.mean[0] - pathway.parameters.mean[0] == pytest.approx(
        -correction.weight * 0.7, abs=1e-14
    )
    assert after.zone_correction == correction


def test_resolved_anchor_uses_shared_zone_draw_even_for_a_ground_lot(bundle: ConfigBundle) -> None:
    """Exercise the chemistry/core interface against the literal 9.3 and 9.7 formulas."""
    bundle = _zone(bundle, 0.4, 0.6)
    model = par.build_stability_model(bundle, "aspirin_325_tab", "aspirin", ["original_bottle"])
    pathway = next(p for p in model.pathways if p.reaction_class_id == "ester_hydrolysis")
    correction = pathway.zone_correction
    assert correction is not None
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    seed, count = 5091, 32
    draws = uncertainty.draw_parameters(
        model, 100.0, 0.0, zones, ["ground_storage"], np.random.default_rng(seed), count
    )
    assert sorted(draws.zone_ln_multiplier) == ["ground_storage", "station_kit"]
    # The layout constants are specified in 9.7, including the otherwise unused slots.
    normal = np.random.default_rng(seed).standard_normal((count, 2 + 8 * len(model.pathways) + 3 * 2))
    index = model.pathways.index(pathway)
    slot = 2 + 8 * index
    station = 2 + 8 * len(model.pathways) + 3
    variance = pathway.parameters.covariance[0][0]
    prior_sd = par.total_rate_prior(bundle, "solid")["ln_k_ref_per_s"]["sigma"]
    evidence_variance = variance / correction.weight
    expected = (
        pathway.parameters.mean[0]
        + variance / prior_sd * normal[:, 1]
        + correction.weight * math.sqrt(evidence_variance - correction.factor_sd_ln**2) * normal[:, slot]
        - correction.weight * correction.factor_sd_ln * normal[:, station]
    )
    np.testing.assert_allclose(draws.ln_k_ref_per_s[:, index], expected, rtol=1e-14, atol=0.0)
    np.testing.assert_allclose(
        draws.zone_ln_multiplier["station_kit"], 0.4 + 0.6 * normal[:, station], rtol=1e-14
    )
    independent_variance = (variance / prior_sd) ** 2 + correction.weight**2 * (
        evidence_variance - correction.factor_sd_ln**2
    )
    assert independent_variance + (correction.weight * correction.factor_sd_ln) ** 2 == pytest.approx(
        variance
    )


def test_fixed_multiplier_uses_log_and_no_correction(bundle: ConfigBundle) -> None:
    def change(doc: dict[str, Any]) -> None:
        find(doc["zones"], "zone_id", "station_kit")["rate_multiplier"].update(
            family="fixed", mu=2.0, sigma=0.0
        )

    fixed = unvalidated(bundle, "storage-zones", change)
    anchor = _anchor(bundle)
    ground = copy.deepcopy(anchor)
    ground["storage_zone_id"] = "ground_storage"
    assert _convert(fixed, anchor)[0] - _convert(fixed, ground)[0] == pytest.approx(-math.log(2.0), abs=1e-14)
    model = par.build_stability_model(fixed, "aspirin_325_tab", "aspirin", ["original_bottle"])
    assert all(p.zone_correction is None for p in model.pathways)


def test_none_package_kind_tracks_ambient(bundle: ConfigBundle) -> None:
    def change(doc: dict[str, Any]) -> None:
        find(doc["packages"], "package_id", "repackaged_bag")["moisture_model"]["kind"] = "none"

    edited = unvalidated(bundle, "packages", change)
    anchor = _anchor(bundle)
    packaged = _convert(edited, anchor)
    anchor["package_id"] = None
    assert _convert(edited, anchor) == packaged


def test_stored_prior_not_converted_again(bundle: ConfigBundle) -> None:
    def change(doc: dict[str, Any]) -> None:
        total = find(doc["total_rate_priors"], "dose_form_family", "solid")
        total["temperature_k"]["value"] = 313.15
        total["temperature_sd_k"]["value"] = 7.0
        total["rh_percent"]["value"] = 75.0
        total["rh_sd_percent"]["value"] = 5.0

    edited = unvalidated(bundle, "reaction-classes", change)
    before = par.build_stability_model(bundle, "ibuprofen_400_tab", "ibuprofen", ["original_bottle"])
    after = par.build_stability_model(edited, "ibuprofen_400_tab", "ibuprofen", ["original_bottle"])
    assert before == after
    rate = next(p for p in after.pathways if p.driver == "thermal")
    prior = par.total_rate_prior(edited, "solid")["ln_k_ref_per_s"]
    assert rate.parameters.mean[0] == prior["mu"]
    assert rate.parameters.covariance[0][0] == prior["sigma"] ** 2


@pytest.mark.parametrize("duration", [None, 0.0, -1.0, math.inf, math.nan, 1e308])
def test_packaged_anchor_rejects_bad_duration(bundle: ConfigBundle, duration: float | None) -> None:
    anchor = _anchor(bundle)
    assert anchor["duration_days"] is not None
    if duration is None:
        anchor["duration_days"] = None
    else:
        anchor["duration_days"]["value"] = duration
    with pytest.raises(ModelDomainError):
        _convert(bundle, anchor)


@pytest.mark.parametrize("field", ["package_id", "storage_zone_id"])
def test_missing_storage_reference_raises(bundle: ConfigBundle, field: str) -> None:
    anchor: Any = _anchor(bundle)
    anchor[field] = "missing"
    with pytest.raises(ConfigReferenceError, match="unknown anchor"):
        _convert(bundle, anchor)


@pytest.mark.parametrize(
    ("family", "mean", "sd"),
    [
        ("normal", 0.0, 1.0),
        ("fixed", 0.0, 0.0),
        ("fixed", 1.0, 1.0),
        ("lognormal", 0.0, -1.0),
        ("lognormal", math.nan, 1.0),
        ("lognormal", 0.0, math.inf),
    ],
)
def test_bad_zone_multiplier_raises(bundle: ConfigBundle, family: str, mean: float, sd: float) -> None:
    def change(doc: dict[str, Any]) -> None:
        find(doc["zones"], "zone_id", "station_kit")["rate_multiplier"].update(
            family=family, mu=mean, sigma=sd
        )

    edited = unvalidated(bundle, "storage-zones", change)
    with pytest.raises(ModelDomainError, match="multiplier"):
        _convert(edited, _anchor(edited))


def test_negative_uncertainty_and_nonfinite_conversion_raise(bundle: ConfigBundle) -> None:
    anchor = _anchor(bundle)
    anchor["temperature_sd_k"]["value"] = -1.0
    with pytest.raises(ModelDomainError, match="nonnegative"):
        _convert(bundle, anchor)
    anchor["temperature_sd_k"]["value"] = 2.0
    with pytest.raises(ModelDomainError, match="finite"):
        par.anchor_at_reference(anchor, 1e308, 24500.0, 0.04, 0.025, 298.15, 40.0, bundle=bundle)


def test_nonpositive_total_prior_sd_raises(bundle: ConfigBundle) -> None:
    total = copy.deepcopy(par.total_rate_prior(bundle, "solid"))
    total["ln_k_ref_per_s"]["sigma"] = 0.0
    with pytest.raises(ModelDomainError, match="positive"):
        par.class_prior_ln_k(total, 1)


@pytest.mark.parametrize("integral", [-1.0, 1e-320])
def test_unusable_humidity_integral_raises(
    bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch, integral: float
) -> None:
    monkeypatch.setattr(par, "humidity_integral", lambda *_args, **_kwargs: integral)
    with pytest.raises(ModelDomainError):
        _convert(bundle, _anchor(bundle))


def test_optional_anchor_storage_context(bundle: ConfigBundle) -> None:
    def change(doc: dict[str, Any]) -> None:
        for cls in doc["classes"]:
            for anchor in cls["anchors"]:
                anchor.pop("storage_context", None)

    edited = unvalidated(bundle, "reaction-classes", change)
    model = par.build_stability_model(edited, "aspirin_325_tab", "aspirin", ["original_bottle"])
    assert any(p.zone_correction is not None for p in model.pathways)
