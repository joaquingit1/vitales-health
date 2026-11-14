"""Unit tests of pharmacy.chemistry.parameters (spec 9.1 to 9.6) and integration steps 1
and 2 of BUILD-PLAN section 4 (a StabilityModel for every formulary product).

Hand values are from mpmath at 30 digits with R = N_A k_B and the configured inputs written
in the comments (anchor ln k, temperature, RH; class Ea 120000 +- 24500 J/mol and
B 0.04 +- 0.025 /%RH for solids; total-rate prior -20.3811 +- 0.545129 for solids)."""

import copy
import dataclasses
import json
import math
from typing import Any

import numpy as np
import pytest
from scipy.special import logsumexp

from pharmacy.chemistry import bde
from pharmacy.chemistry import parameters as par
from pharmacy.chemistry.bde import DEFAULT_CACHE_DIR
from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError, InputError, ModelDomainError
from pharmacy.types import Anchor, FreeShare, Pathway, StabilityModel, TotalRatePrior, to_json
from tests.unit.chemistry.support import (
    EXPECTED,
    BundleMutator,
    anchor_posterior,
    find,
    product,
    reaction_class,
    sourced,
    unvalidated,
)

LN10 = 2.30258509299404568


# --- scalar functions -----------------------------------------------------------------------------
def test_convert_reference_hand_values() -> None:
    names: list[Any] = ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"]
    mean = [-14.0, 90000.0, 0.03]
    cov = [[0.04, -500.0, 0.0001], [-500.0, 1.0e7, 0.0], [0.0001, 0.0, 0.0004]]
    new_mean, new_cov = par.convert_reference(names, mean, cov, 313.15, 75.0, 298.15, 40.0)
    assert new_mean[0] == pytest.approx(-16.7890511411748269, rel=1e-14)
    assert new_mean[1:] == (90000.0, 0.03)
    assert new_cov[0][0] == pytest.approx(0.546056492768141864, rel=1e-12)
    assert new_cov[0][1] == pytest.approx(-693.227904574980769, rel=1e-12)
    assert new_cov[0][2] == pytest.approx(-0.0139, rel=1e-12)
    assert new_cov[1][1] == 1.0e7
    assert new_cov[2][2] == 0.0004
    assert all(new_cov[i][j] == new_cov[j][i] for i in range(3) for j in range(3))


def test_convert_reference_order_identity_and_missing_terms() -> None:
    names: list[Any] = ["ea_j_per_mol", "ln_k_ref_per_s"]
    mean, cov = par.convert_reference(
        names, [90000.0, -14.0], [[1e7, -500.0], [-500.0, 0.04]], 313.15, 75.0, 298.15, 40.0
    )
    assert cov[0][0] == 1e7
    # Without B the RH term vanishes: ln k' = ln k - Ea x.
    x = (1.0 / 298.15 - 1.0 / 313.15) / (6.02214076e23 * 1.380649e-23)
    assert mean[1] == pytest.approx(-14.0 - 90000.0 * x, rel=1e-14)
    assert mean[0] == 90000.0
    same_mean, same_cov = par.convert_reference(
        ["ln_k_ref_per_s"], [-14.0], [[0.04]], 313.15, 75.0, 313.15, 75.0
    )
    assert same_mean == (-14.0,)
    assert same_cov == ((0.04,),)
    # Only ln k: temperature changes nothing without Ea.
    assert par.convert_reference(["ln_k_ref_per_s"], [-14.0], [[0.04]], 313.15, 75.0, 298.15, 40.0)[0] == (
        -14.0,
    )


@pytest.mark.parametrize(
    ("names", "mean", "cov", "temps"),
    [
        (["ea_j_per_mol"], [1.0], [[1.0]], (300.0, 298.15)),
        (["ln_k_ref_per_s", "ln_k_ref_per_s"], [1.0, 1.0], [[1.0, 0.0], [0.0, 1.0]], (300.0, 298.15)),
        (["ln_k_ref_per_s"], [1.0, 2.0], [[1.0]], (300.0, 298.15)),
        (["ln_k_ref_per_s"], [1.0], [[1.0, 0.0]], (300.0, 298.15)),
        (["ln_k_ref_per_s"], [math.nan], [[1.0]], (300.0, 298.15)),
        (["ln_k_ref_per_s"], [1.0], [[math.inf]], (300.0, 298.15)),
        (["ln_k_ref_per_s"], [1.0], [[1.0]], (0.0, 298.15)),
        (["ln_k_ref_per_s"], [1.0], [[1.0]], (300.0, -1.0)),
    ],
)
def test_convert_reference_errors(
    names: list[Any], mean: list[float], cov: list[list[float]], temps: tuple[float, float]
) -> None:
    with pytest.raises(ModelDomainError):
        par.convert_reference(names, mean, cov, temps[0], 40.0, temps[1], 40.0)


def test_precision_weighted_hand_values() -> None:
    mu, sd = par.precision_weighted(-20.0, 0.5, -21.0, 1.0)
    assert mu == pytest.approx(-20.2, rel=1e-15)
    assert sd == pytest.approx(0.447213595499957939, rel=1e-15)
    for bad in [
        (-20.0, 0.0, -21.0, 1.0),
        (-20.0, 0.5, -21.0, -1.0),
        (math.nan, 0.5, -21.0, 1.0),
        (-20.0, math.inf, -21.0, 1.0),
    ]:
        with pytest.raises(ModelDomainError):
            par.precision_weighted(*bad)


def _hand_total(bundle: ConfigBundle) -> TotalRatePrior:
    """Fixed arithmetic fixture, independent of offline recalibration of the formulary."""
    total = copy.deepcopy(par.total_rate_prior(bundle, "solid"))
    total["ln_k_ref_per_s"].update({"mu": -20.3811, "sigma": 0.545129})
    return total


@pytest.mark.parametrize("sd", [1e-300, 1e300])
def test_precision_weighted_extreme_finite_scales(sd: float) -> None:
    mean, combined_sd = par.precision_weighted(1e308, sd, -1e308, sd)
    assert mean == 0.0
    assert combined_sd == pytest.approx(sd / math.sqrt(2.0), rel=1e-14, abs=0.0)
    mean, combined_sd = par.precision_weighted(-20.0, sd, -20.0, 1.0)
    assert mean == -20.0
    assert combined_sd == min(sd, 1.0)


def test_class_prior_ln_k(bundle: ConfigBundle) -> None:
    solid = _hand_total(bundle)
    assert par.class_prior_ln_k(solid, 1) == (-20.3811, 0.545129)
    mu, sd = par.class_prior_ln_k(solid, 2)
    assert mu == pytest.approx(-21.0742471805599453, rel=1e-15)
    assert sd == 0.545129
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ModelDomainError):
            par.class_prior_ln_k(solid, bad)  # type: ignore[arg-type]
    lognormal: TotalRatePrior = copy.deepcopy(solid)
    lognormal["ln_k_ref_per_s"]["family"] = "lognormal"
    with pytest.raises(ModelDomainError):
        par.class_prior_ln_k(lognormal, 1)
    nan: TotalRatePrior = copy.deepcopy(solid)
    nan["ln_k_ref_per_s"]["mu"] = math.nan
    with pytest.raises(ModelDomainError):
        par.class_prior_ln_k(nan, 1)


def test_split_class_priors_hand_values(bundle: ConfigBundle) -> None:
    """Spec 9.5 with one anchored member and a free group of three (shifts 1, 0, -2):
    mpmath at 30 digits."""
    solid = _hand_total(bundle)
    anchored_mu, s_p, free = par.split_class_priors(solid, 1, (1.0, 0.0, -2.0))
    assert anchored_mu == pytest.approx(-21.7673943611198906, rel=1e-15)
    assert s_p == 0.545129
    expected = (
        (-21.0177942892199673, 0.705384512698241158),
        (-22.0177942892199673, 0.259496460342419117),
        (-24.0177942892199673, 0.0351190269593397242),
    )
    for prior, (mu, pi) in zip(free, expected, strict=True):
        assert prior.mu_ln_k == pytest.approx(mu, rel=1e-15)
        assert prior.share == pytest.approx(pi, rel=1e-14)
    total = math.exp(anchored_mu) + math.fsum(math.exp(s.mu_ln_k) for s in free)
    assert total == pytest.approx(math.exp(-20.3811), rel=1e-14)


def test_split_class_priors_limits(bundle: ConfigBundle) -> None:
    solid = _hand_total(bundle)
    # No shift anywhere: the equal split.
    anchored_mu, _s_p, free = par.split_class_priors(solid, 1, (0.0, 0.0))
    equal = par.class_prior_ln_k(solid, 3)[0]
    assert anchored_mu == equal
    assert all(s.mu_ln_k == pytest.approx(equal, rel=1e-15) and s.share == 0.5 for s in free)
    assert par.split_class_priors(solid, 0, (0.0,)) == (-20.3811, 0.545129, (par.FreePrior(-20.3811, 1.0),))
    # A free group of one keeps mu_total + ln(1/N) whatever its shift.
    _mu, _sd, (alone,) = par.split_class_priors(solid, 1, (3.7,))
    assert alone.mu_ln_k == pytest.approx(-20.3811 - math.log(2.0), rel=1e-15)
    assert alone.share == 1.0
    # Only anchored members.
    assert par.split_class_priors(solid, 2, ()) == (par.class_prior_ln_k(solid, 2)[0], 0.545129, ())
    # A large shift does not overflow.
    _mu, _sd, (big, small) = par.split_class_priors(solid, 0, (800.0, 0.0))
    assert big.mu_ln_k == pytest.approx(-20.3811, rel=1e-15)
    assert small.share == 0.0
    for bad in ((-1, (0.0,)), (True, (0.0,)), (0, ()), (0, (math.nan,)), (0, (math.inf, 0.0))):
        with pytest.raises(ModelDomainError):
            par.split_class_priors(solid, *bad)
    lognormal: TotalRatePrior = copy.deepcopy(solid)
    lognormal["ln_k_ref_per_s"]["family"] = "lognormal"
    with pytest.raises(ModelDomainError):
        par.split_class_priors(lognormal, 0, (0.0,))


def _aspirin_anchor(bundle: ConfigBundle) -> Anchor:
    return find(reaction_class(bundle, "ester_hydrolysis")["anchors"], "anchor_id", "aspirin_tablet_iss_550d")  # type: ignore[arg-type,return-value]


def test_anchor_at_reference_hand_values(bundle: ConfigBundle) -> None:
    # Synthetic open-dish ground anchor with the original hand-calculated inputs.
    anchor = copy.deepcopy(_aspirin_anchor(bundle))
    anchor.update({"package_id": None, "storage_zone_id": "ground_storage", "duration_days": None})
    mean, sd = par.anchor_at_reference(anchor, 120000.0, 24500.0, 0.04, 0.025, 298.15, 40.0, bundle=bundle)
    assert mean == pytest.approx(-19.9192709843727849, rel=1e-13)
    assert sd == pytest.approx(0.759921373433750255, rel=1e-13)
    # At the anchor's own conditions only the temperature-uncertainty term is added.
    mean0, sd0 = par.anchor_at_reference(anchor, 120000.0, 24500.0, 0.04, 0.025, 295.15, 25.0, bundle=bundle)
    assert mean0 == -21.0113
    r = 6.02214076e23 * 1.380649e-23
    assert sd0 == pytest.approx(math.hypot(0.563, 120000.0 * 2.0 / (r * 295.15**2)), rel=1e-14)


def test_anchor_at_reference_errors(bundle: ConfigBundle) -> None:
    anchor = copy.deepcopy(_aspirin_anchor(bundle))
    with pytest.raises(ModelDomainError):
        par.anchor_at_reference(anchor, math.nan, 24500.0, 0.04, 0.025, 298.15, 40.0, bundle=bundle)
    with pytest.raises(ModelDomainError):
        par.anchor_at_reference(anchor, 120000.0, 24500.0, 0.04, 0.025, 0.0, 40.0, bundle=bundle)
    anchor["ln_k_per_s"]["family"] = "lognormal"
    with pytest.raises(ModelDomainError, match="must be normal"):
        par.anchor_at_reference(anchor, 120000.0, 24500.0, 0.04, 0.025, 298.15, 40.0, bundle=bundle)


# --- integration: a model for every formulary product ---------------------------------------------
def _model(
    bundle: ConfigBundle, product_id: str, ingredient_id: str, packages: list[str] | None = None
) -> StabilityModel:
    ids = packages if packages is not None else [product(bundle, product_id)["default_package_id"]]
    return par.build_stability_model(bundle, product_id, ingredient_id, ids)


def _pathway(model: StabilityModel, class_id: str) -> Pathway:
    return next(p for p in model.pathways if p.reaction_class_id == class_id)


def _value(pathway: Pathway, name: str) -> tuple[float, float]:
    i = pathway.parameters.names.index(name)
    return pathway.parameters.mean[i], math.sqrt(pathway.parameters.covariance[i][i])


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_every_formulary_product_builds(bundle: ConfigBundle, key: tuple[str, str]) -> None:
    product_id, ingredient_id = key
    prod = product(bundle, product_id)
    model = _model(bundle, product_id, ingredient_id)
    assert model.model_id == f"{product_id}:{ingredient_id}:{prod['default_package_id']}"
    assert model.product_id == product_id
    assert model.ingredient_id == ingredient_id
    assert model.dose_form_family == prod["dose_form_family"]
    assert model.rate_law_kind == "F1"
    assert (model.order_n, model.alpha0, model.avrami_n) == (None, None, None)
    assert model.reference_temperature_k == 298.15
    assert model.reference_rh_percent == 40.0
    assert model.measured_model_id is None
    assert model.max_study_duration_s is None
    ids = [p.pathway_id for p in model.pathways]
    assert ids == sorted(ids)
    assert {p.reaction_class_id for p in model.pathways} == {c for c, _ in EXPECTED[key]}
    sites = dict(EXPECTED[key])
    split = [p for p in model.pathways if p.driver == "thermal" and p.role == "destructive"]
    assert split
    for pathway in model.pathways:
        block = pathway.parameters
        assert pathway.sites == tuple(s for s in pathway.sites)
        assert tuple(s.site_atom for s in pathway.sites) == sites[pathway.reaction_class_id]
        assert pathway.pathway_id == f"{ingredient_id}:{pathway.reaction_class_id}"
        assert list(block.names) == sorted(block.names, key=par.PARAMETER_ORDER.index)
        assert [e.name for e in block.estimates] == list(block.names)
        n = len(block.names)
        for i in range(n):
            assert math.isfinite(block.mean[i])
            for j in range(n):
                assert block.covariance[i][j] == (block.covariance[i][i] if i == j else 0.0)
        assert 0.0 <= block.shared_weight <= 1.0
        if pathway in split:
            assert block.shared_weight > 0.0
            assert "ea_j_per_mol" in block.names
            assert block.ea_lower_j_per_mol == 0.0
        else:
            assert block.shared_weight == 0.0
        if pathway.driver == "radiation":
            assert pathway.computed is not None
            assert pathway.computed.method in ("g_value_direct", "g_value_indirect")
        for estimate in block.estimates:
            assert estimate.unit == par.PARAMETER_UNITS[estimate.name]
            assert estimate.source_layer in ("computed", "class_prior")
    assert par.computed_cache_keys(model) == tuple(sorted(set(par.computed_cache_keys(model))))
    json.dumps(to_json(model))


def test_model_is_deterministic(bundle: ConfigBundle) -> None:
    first = to_json(_model(bundle, "epinephrine_0_3mg_autoinjector", "epinephrine"))
    second = to_json(_model(bundle, "epinephrine_0_3mg_autoinjector", "epinephrine"))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_aspirin_anchor_resolution_hand_values(bundle: ConfigBundle) -> None:
    """Package-aware anchor and configured prior combine with the specified precision weights."""
    pathway = _pathway(_model(bundle, "aspirin_325_tab", "aspirin"), "ester_hydrolysis")
    mu, sd = _value(pathway, "ln_k_ref_per_s")
    expected_mu, expected_sd, weight = anchor_posterior(bundle, "ester_hydrolysis", "solid", 1)
    assert mu == pytest.approx(expected_mu, rel=1e-13)
    assert sd == pytest.approx(expected_sd, rel=1e-13)
    assert pathway.parameters.shared_weight == pytest.approx(weight, rel=1e-13)
    assert par.pathway_layer(pathway) == "computed"
    assert pathway.computed is not None
    assert pathway.computed.method == "anchor_only"
    assert pathway.computed.flags == ("same_molecule",)
    assert pathway.degradant is not None
    assert pathway.degradant.degradant_id == "salicylic_acid"
    assert pathway.degradant.product_specific


def test_acetaminophen_resolution(bundle: ConfigBundle) -> None:
    """Split N = 2: anchored amide, one free phenol member. No shift moves either."""
    model = _model(bundle, "acetaminophen_325_tab", "acetaminophen")
    amide = _pathway(model, "amide_hydrolysis")
    mu, sd = _value(amide, "ln_k_ref_per_s")
    expected_mu, expected_sd, weight = anchor_posterior(bundle, "amide_hydrolysis", "solid", 2)
    assert mu == pytest.approx(expected_mu, rel=1e-13)
    assert sd == pytest.approx(expected_sd, rel=1e-13)
    assert amide.parameters.shared_weight == pytest.approx(weight, rel=1e-13)
    phenol = _pathway(model, "phenol_autoxidation")
    mu, sd = _value(phenol, "ln_k_ref_per_s")
    total = par.total_rate_prior(bundle, "solid")["ln_k_ref_per_s"]
    assert mu == pytest.approx(total["mu"] - math.log(2), rel=1e-15)
    assert sd == total["sigma"]
    assert phenol.parameters.shared_weight == 1.0
    assert phenol.computed is None
    assert phenol.free_share is None
    assert amide.free_share is None
    assert phenol.parameters.estimates[0].method == "total_rate_split"
    assert par.pathway_layer(phenol) == "class_prior"
    assert par.computed_cache_keys(model) == ()


def test_free_group_of_one_without_anchor(bundle: ConfigBundle) -> None:
    """Levofloxacin: N = N_F = 1, an Evans-Polanyi class, nothing to redistribute: mu_total,
    s_p and w = 1, no BDE consulted (spec 9.5)."""
    model = _model(bundle, "levofloxacin_500_tab", "levofloxacin")
    amine = _pathway(model, "amine_alpha_oxidation")
    assert _value(amine, "ln_k_ref_per_s") == par.class_prior_ln_k(par.total_rate_prior(bundle, "solid"), 1)
    assert amine.parameters.shared_weight == 1.0
    assert amine.computed is None
    assert amine.free_share is None
    assert par.computed_cache_keys(model) == ()


def test_anchored_members_ignore_every_shift(bundle: ConfigBundle, mutate_bundle: BundleMutator) -> None:
    """Amoxicillin: the anchored amide and beta-lactam keep mu_total - ln 5 before 9.4,
    with or without the Evans-Polanyi entries, so their blocks are identical."""

    def drop(data: dict[str, Any]) -> None:
        data["evans_polanyi"] = []

    plain = mutate_bundle({"lfer-constants": drop})
    key = ("amoxicillin_clavulanate_875_125_tab", "amoxicillin")
    shifted = _model(bundle, *key)
    unshifted = _model(plain, *key)
    split = par.total_rate_split(bundle, *key, [product(bundle, key[0])["default_package_id"]])
    assert split is not None
    assert split.shifted
    for class_id in ("amide_hydrolysis", "beta_lactam_hydrolysis"):
        member = split.member(f"amoxicillin:{class_id}")
        assert member.anchored
        assert (member.mu_ln_k, member.sd_ln_k) == par.class_prior_ln_k(split.total, 5)
        assert member.share is None
        assert member.free_share is None
        assert _pathway(shifted, class_id).free_share is None
        a = _pathway(shifted, class_id).parameters
        b = _pathway(unshifted, class_id).parameters
        assert (a.names, a.mean, a.covariance, a.shared_weight) == (
            b.names,
            b.mean,
            b.covariance,
            b.shared_weight,
        )
        assert "anchored, so no shift moves it" in a.estimates[0].note
    for class_id in ("phenol_autoxidation", "amine_alpha_oxidation", "thioether_s_oxidation"):
        assert _pathway(shifted, class_id).parameters != _pathway(unshifted, class_id).parameters


def _bde_value(bundle: ConfigBundle, smiles: str, atom: int, bond: str) -> float:
    return bde.bond_dissociation_energy(smiles, atom, bond, bundle.settings["computed_layer"]).bde_kj_per_mol


def test_free_group_blocks_and_free_shares(bundle: ConfigBundle) -> None:
    """Amoxicillin's free group: phenol (shifted, BDE inside the domain), amine alpha (C12
    outside the domain: only the hydrogen-count term ln(1/9), exact, s_m = 0) and thioether
    (unshifted). Each block is mu_p,i +- s_p with w = 1; free_share carries (delta_i, s_m,i)
    and the trace descriptors carry delta_i, s_m,i and pi_i (spec 9.5)."""
    key = ("amoxicillin_clavulanate_875_125_tab", "amoxicillin")
    smiles = "CC1([C@@H](N2[C@H](S1)[C@@H](C2=O)NC(=O)[C@@H](C3=CC=C(C=C3)O)N)C(=O)O)C"
    model = _model(bundle, *key)
    phenol_bde = _bde_value(bundle, smiles, 19, "O-H")
    reference = _bde_value(bundle, "Oc1ccccc1", 0, "O-H")
    r = 6.02214076e23 * 1.380649e-23
    delta_phenol = -0.5 * (phenol_bde - reference) * 1000.0 / (r * 298.15)
    # mpmath, 30 digits, from the committed entries (356.9486002239845 and 365.2073870337029).
    assert delta_phenol == pytest.approx(1.66577853328967682, rel=1e-14)
    expected = {
        "phenol_autoxidation": (delta_phenol, LN10),
        "amine_alpha_oxidation": (math.log(1.0 / 9.0), 0.0),
        "thioether_s_oxidation": (0.0, 0.0),
    }
    log_sum = math.log(math.fsum(math.exp(d) for d, _ in expected.values()))
    total = par.total_rate_prior(bundle, "solid")["ln_k_ref_per_s"]
    s_p = total["sigma"]
    for class_id, (delta, s_m) in expected.items():
        pathway = _pathway(model, class_id)
        assert pathway.free_share is not None
        assert pathway.free_share.delta_ln == pytest.approx(delta, rel=1e-13, abs=1e-15)
        assert pathway.free_share.method_sd_ln == pytest.approx(s_m, rel=1e-15)
        mu, sd = _value(pathway, "ln_k_ref_per_s")
        assert mu == pytest.approx(total["mu"] + math.log(3.0 / 5.0) + delta - log_sum, rel=1e-14)
        assert sd == s_p
        assert pathway.parameters.shared_weight == 1.0
        assert pathway.parameters.estimates[0].method == "total_rate_split+evans_polanyi_bde"
        if class_id == "thioether_s_oxidation":
            assert pathway.computed is None
            continue
        assert pathway.computed is not None
        descriptors = dict(pathway.computed.descriptors)
        assert descriptors["split_delta_ln"] == pathway.free_share.delta_ln
        assert descriptors["split_method_sd_ln"] == pathway.free_share.method_sd_ln
        assert descriptors["split_share"] == pytest.approx(math.exp(delta - log_sum), rel=1e-14)
    amine = _pathway(model, "amine_alpha_oxidation")
    assert amine.computed is not None
    assert "outside_applicability_domain" in amine.computed.flags
    assert dict(amine.computed.descriptors)["n_sites_in_domain"] == 0.0


def _free_group_draws(model: StabilityModel, z_cls: Any, z_free: Any) -> Any:
    """ln k* of the free members by spec 9.5, drawn exactly with numpy's log-softmax:
    mu_p,i + s_p z_cls + log_softmax(delta + s_m z)_i - log_softmax(delta)_i."""
    free = [p for p in model.pathways if p.free_share is not None]
    mu = np.array([_value(p, "ln_k_ref_per_s")[0] for p in free])
    s_p = np.array([_value(p, "ln_k_ref_per_s")[1] for p in free])
    delta = np.array([p.free_share.delta_ln for p in free if p.free_share is not None])
    s_m = np.array([p.free_share.method_sd_ln for p in free if p.free_share is not None])
    perturbed = delta + s_m * z_free
    log_share = perturbed - logsumexp(perturbed, axis=1, keepdims=True)
    nominal = delta - logsumexp(delta)
    return mu + s_p * z_cls[:, None] + log_share - nominal


@pytest.mark.parametrize(
    ("product_id", "ingredient_id", "n_free", "n"),
    [
        ("amoxicillin_clavulanate_875_125_tab", "amoxicillin", 3, 5),
        ("promethazine_25_tab", "promethazine", 2, 2),
        ("promethazine_25mg_ml_inj", "promethazine", 2, 2),
        ("epinephrine_0_1mg_ml_syringe", "epinephrine", 2, 2),
    ],
)
def test_free_group_draws_keep_the_total(
    bundle: ConfigBundle, product_id: str, ingredient_id: str, n_free: int, n: int
) -> None:
    """Spec 9.5: in every draw the free group's rates sum to exp(mu_total + s_p z_cls) N_F/N."""
    model = _model(bundle, product_id, ingredient_id)
    assert sum(1 for p in model.pathways if p.free_share is not None) == n_free
    split = par.total_rate_split(
        bundle, product_id, ingredient_id, [product(bundle, product_id)["default_package_id"]]
    )
    assert split is not None
    mu_total = split.total["ln_k_ref_per_s"]["mu"]
    s_p = split.sd_class_prior
    rng = np.random.default_rng(20261114)
    z_cls = rng.standard_normal(2000)
    z_free = rng.standard_normal((2000, n_free))
    ln_k = _free_group_draws(model, z_cls, z_free)
    totals = np.exp(ln_k).sum(axis=1)
    expected = np.exp(mu_total + s_p * z_cls) * n_free / n
    assert np.max(np.abs(totals / expected - 1.0)) < 1e-12
    # At z = 0 the draw is mu_p,i.
    zero = _free_group_draws(model, np.zeros(1), np.zeros((1, n_free)))[0]
    free = [p for p in model.pathways if p.free_share is not None]
    assert np.allclose(zero, [_value(p, "ln_k_ref_per_s")[0] for p in free], rtol=0.0, atol=1e-13)


def test_pure_class_prior_and_split(bundle: ConfigBundle) -> None:
    ibuprofen = _pathway(_model(bundle, "ibuprofen_400_tab", "ibuprofen"), "unclassified_thermal")
    total = par.total_rate_prior(bundle, "solid")["ln_k_ref_per_s"]
    assert _value(ibuprofen, "ln_k_ref_per_s") == (total["mu"], total["sigma"])
    assert ibuprofen.parameters.shared_weight == 1.0
    assert ibuprofen.computed is None
    assert ibuprofen.free_share is None
    assert ibuprofen.degradant is None
    model = _model(bundle, "promethazine_25_tab", "promethazine")
    thioether = _pathway(model, "thioether_s_oxidation")
    amine = _pathway(model, "amine_alpha_oxidation")
    assert amine.computed is not None
    # Free group of two, the amine shifted: the thioether takes mu_total - ln(1 + e^delta),
    # the amine mu_total + delta - ln(1 + e^delta); both s_p with w = 1.
    delta = amine.computed.delta_ln_k
    mu, sd = _value(thioether, "ln_k_ref_per_s")
    assert mu == pytest.approx(total["mu"] - math.log1p(math.exp(delta)), rel=1e-15)
    assert (sd, thioether.parameters.shared_weight) == (total["sigma"], 1.0)
    assert thioether.free_share == FreeShare(delta_ln=0.0, method_sd_ln=0.0)
    assert thioether.computed is None
    mu, sd = _value(amine, "ln_k_ref_per_s")
    assert mu == pytest.approx(total["mu"] + delta - math.log1p(math.exp(delta)), rel=1e-15)
    assert (sd, amine.parameters.shared_weight) == (total["sigma"], 1.0)
    assert amine.free_share == FreeShare(delta_ln=delta, method_sd_ln=LN10)
    assert thioether.degradant is not None
    assert thioether.degradant.degradant_id == "promethazine_sulfoxide"


def test_solid_thermal_block_contents(bundle: ConfigBundle) -> None:
    pathway = _pathway(_model(bundle, "ibuprofen_400_tab", "ibuprofen"), "unclassified_thermal")
    assert pathway.parameters.names == ("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh")
    assert _value(pathway, "ea_j_per_mol") == (120000.0, 24500.0)
    assert _value(pathway, "b_per_percent_rh") == (0.04, 0.025)
    ea = pathway.parameters.estimates[1]
    assert (ea.family, ea.lower) == ("truncated_normal", 0.0)


def test_solution_blocks_frozen_multiplier_and_radiolysis(bundle: ConfigBundle) -> None:
    model = _model(bundle, "promethazine_25mg_ml_inj", "promethazine")
    thioether = _pathway(model, "thioether_s_oxidation")
    assert thioether.parameters.names == (
        "ln_k_ref_per_s",
        "ea_j_per_mol",
        "b_per_percent_rh",
        "ln_frozen_rate_multiplier",
    )
    assert _value(thioether, "b_per_percent_rh") == (0.0, 0.0)  # fixed prior, variance 0
    assert _value(thioether, "ln_frozen_rate_multiplier") == (0.0, 1.17)
    assert thioether.parameters.estimates[2].family == "fixed"
    radiolysis = _pathway(model, "radiolysis")
    assert radiolysis.parameters.names == ("ln_k_dose_per_gy", "ln_k_dose_frozen_per_gy")
    assert radiolysis.parameters.mean[0] == pytest.approx(-13.1594015694076276, abs=1e-12)
    assert radiolysis.parameters.mean[1] == pytest.approx(-17.3754735810530512, abs=1e-12)
    assert radiolysis.computed is not None
    assert radiolysis.computed.method == "g_value_indirect"
    assert model.freezing_point_k == 272.65
    assert par.pathway_layer(radiolysis) == "class_prior"


def test_solid_radiolysis_block(bundle: ConfigBundle) -> None:
    radiolysis = _pathway(_model(bundle, "acetaminophen_325_tab", "acetaminophen"), "radiolysis")
    assert radiolysis.parameters.names == ("ln_k_dose_per_gy",)
    assert radiolysis.parameters.mean[0] == pytest.approx(-18.0075164005008488, abs=1e-12)
    assert radiolysis.computed is not None
    assert radiolysis.computed.method == "g_value_direct"


def test_light_block(bundle: ConfigBundle) -> None:
    light = _pathway(_model(bundle, "promethazine_25_tab", "promethazine"), "photolysis_conjugated")
    assert light.parameters.names == ("ln_k_vis_per_lux_h", "ln_k_uv_per_w_h_per_m2")
    assert light.parameters.mean == (-16.968, -8.26851)
    assert light.parameters.covariance == ((2.25, 0.0), (0.0, 2.25))
    assert light.parameters.shared_weight == 0.0
    assert par.pathway_layer(light) == "class_prior"


def test_epinephrine_racemization_and_initial_fraction(bundle: ConfigBundle) -> None:
    model = _model(bundle, "epinephrine_0_1mg_ml_syringe", "epinephrine")
    assert model.initial_active_fraction == 0.995
    racemization = _pathway(model, "racemization_benzylic_alcohol")
    assert racemization.role == "racemization"
    assert _value(racemization, "ln_k_ref_per_s") == (-19.6845, 1.5)
    assert racemization.parameters.shared_weight == 0.0
    assert racemization.degradant is None
    catechol = _pathway(model, "catechol_autoxidation")
    assert catechol.degradant is not None
    assert catechol.degradant.degradant_id == "adrenochrome"
    # Three EP sites (two catechol oxygens, two amine alpha carbons) plus references: 6 keys.
    assert len(par.computed_cache_keys(model)) == 6


def _split_keys(bundle: ConfigBundle) -> list[tuple[str, str]]:
    return sorted(
        (p["product_id"], i["ingredient_id"]) for p in bundle.formulary["products"] for i in p["ingredients"]
    )


def _check_calibration(bundle: ConfigBundle) -> int:
    """Spec 7.5 calibration: the split shares sum to the total, anchored members keep the
    equal split, and the model's free members carry mu_p,i +- s_p with w = 1 and a free share
    exactly when the free group has two or more members. Returns the number of shifted
    members."""
    shifted = 0
    for product_id, ingredient_id in _split_keys(bundle):
        package = product(bundle, product_id)["default_package_id"]
        split = par.total_rate_split(bundle, product_id, ingredient_id, [package])
        assert split is not None
        mu_total = split.total["ln_k_ref_per_s"]["mu"]
        total = math.fsum(math.exp(m.mu_ln_k) for m in split.members)
        assert total == pytest.approx(math.exp(mu_total), rel=1e-12, abs=0.0)
        model = par.build_stability_model(bundle, product_id, ingredient_id, [package])
        assert {m.pathway_id for m in split.members} == {
            p.pathway_id for p in model.pathways if p.driver == "thermal" and p.role == "destructive"
        }
        s_p = split.sd_class_prior
        for member in split.members:
            pathway = next(p for p in model.pathways if p.pathway_id == member.pathway_id)
            mu, sd = _value(pathway, "ln_k_ref_per_s")
            assert pathway.free_share == member.free_share
            if member.anchored:
                assert par.pathway_layer(pathway) == "computed"
                assert (member.mu_ln_k, member.sd_ln_k) == par.class_prior_ln_k(
                    split.total, len(split.members)
                )
                assert member.shift is None
                assert member.free_share is None
                continue
            assert par.pathway_layer(pathway) == "class_prior"
            assert pathway.computed == member.shift
            assert (mu, sd) == (member.mu_ln_k, s_p)
            assert pathway.parameters.shared_weight == 1.0
            assert (member.free_share is not None) == (split.n_free >= 2)
            if member.shift is not None:
                shifted += 1
    return shifted


def test_split_calibration_with_shifts(bundle: ConfigBundle) -> None:
    assert _check_calibration(bundle) > 0


def test_split_calibration_without_shifts(mutate_bundle: BundleMutator) -> None:
    def drop(data: dict[str, Any]) -> None:
        data["evans_polanyi"] = []

    bundle = mutate_bundle({"lfer-constants": drop})
    assert _check_calibration(bundle) == 0
    for product_id, ingredient_id in _split_keys(bundle):
        package = product(bundle, product_id)["default_package_id"]
        split = par.total_rate_split(bundle, product_id, ingredient_id, [package])
        assert split is not None
        assert not split.shifted
        equal, s_p = par.class_prior_ln_k(split.total, len(split.members))
        for member in split.members:
            assert member.mu_ln_k == pytest.approx(equal, rel=1e-15)
            assert member.sd_ln_k == s_p


def test_total_rate_split_under_a_measured_model_is_none(
    mutate_bundle: BundleMutator, bundle: ConfigBundle
) -> None:
    edited = mutate_bundle({"stability-models": _measured("prod_apap", None)})
    assert par.total_rate_split(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"]) is None
    split = par.total_rate_split(bundle, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    assert split is not None
    assert not split.shifted
    assert [(m.pathway_id, m.anchored) for m in split.members] == [
        ("acetaminophen:amide_hydrolysis", True),
        ("acetaminophen:phenol_autoxidation", False),
    ]
    with pytest.raises(ModelDomainError, match="not in the total-rate split"):
        split.member("acetaminophen:radiolysis")


def test_evans_polanyi_class_outside_the_split_raises(bundle: ConfigBundle) -> None:
    """Spec 9.5: an Evans-Polanyi class without an anchor outside the split is a
    configuration error."""

    def add(data: dict[str, Any]) -> None:
        entry = copy.deepcopy(data["evans_polanyi"][0])
        entry["class_id"] = "racemization_benzylic_alcohol"
        data["evans_polanyi"].append(entry)

    edited = unvalidated(bundle, "lfer-constants", add)
    with pytest.raises(ModelDomainError, match="outside the total-rate split"):
        _model(edited, "epinephrine_0_1mg_ml_syringe", "epinephrine")


def test_package_list_is_sorted_and_unique(bundle: ConfigBundle) -> None:
    model = _model(
        bundle,
        "acetaminophen_325_tab",
        "acetaminophen",
        ["repackaged_bag", "original_bottle", "original_bottle"],
    )
    assert model.model_id == "acetaminophen_325_tab:acetaminophen:original_bottle+repackaged_bag"


def test_lot_package_histories_build(bundle: ConfigBundle) -> None:
    for lot in bundle.lots["lots"]:
        prod = product(bundle, lot["product_id"])
        ids = [entry["package_id"] for entry in lot["package_history"]]
        for ingredient in prod["ingredients"]:
            model = par.build_stability_model(bundle, prod["product_id"], ingredient["ingredient_id"], ids)
            assert {p.reaction_class_id for p in model.pathways} == {
                c for c, _ in EXPECTED[(prod["product_id"], ingredient["ingredient_id"])]
            }


def test_build_errors(bundle: ConfigBundle) -> None:
    with pytest.raises(ConfigReferenceError, match="unknown product"):
        par.build_stability_model(bundle, "nope", "aspirin", ["original_bottle"])
    with pytest.raises(ConfigReferenceError, match="no ingredient"):
        par.build_stability_model(bundle, "aspirin_325_tab", "nope", ["original_bottle"])
    with pytest.raises(ConfigReferenceError, match="unknown package"):
        par.build_stability_model(bundle, "aspirin_325_tab", "aspirin", ["nope"])
    with pytest.raises(InputError):
        par.build_stability_model(bundle, "aspirin_325_tab", "aspirin", [])


# --- config edge cases through schema-valid test bundles -------------------------------------------
def _measured(model_id: str, package_id: str | None, **changes: Any) -> Any:
    def mutate(doc: dict[str, Any]) -> None:
        base = copy.deepcopy(find(doc["models"], "model_id", "ref_acetaminophen_0_5m_hcl"))
        base.update(
            model_id=model_id,
            scope="product",
            product_id="acetaminophen_325_tab",
            ingredient_id="acetaminophen",
            package_id=package_id,
            study=None,
            max_study_duration_s=1.0e8,
        )
        base.update(changes)
        doc["models"].append(base)

    return mutate


def test_measured_model_replaces_thermal_pathways(mutate_bundle: BundleMutator) -> None:
    edited = mutate_bundle({"stability-models": _measured("prod_apap", None)})
    model = par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    assert model.measured_model_id == "prod_apap"
    assert model.max_study_duration_s == 1.0e8
    assert [p.pathway_id for p in model.pathways] == ["acetaminophen:prod_apap", "acetaminophen:radiolysis"]
    measured = model.pathways[0]
    assert par.pathway_layer(measured) == "measured_model"
    assert measured.parameters.names == ("ln_k_ref_per_s", "ea_j_per_mol")
    # Stored at 298.15 K without RH: the conversion to (T*, RH*) is the identity.
    assert measured.parameters.mean == (-13.040773194587574, 68118.84460007404)
    assert measured.parameters.covariance[0][1] == -1071.5483760089392
    assert measured.parameters.shared_weight == 0.0
    assert measured.parameters.ea_lower_j_per_mol == 0.0
    assert measured.validated_range.source_layer == "measured_model"
    assert tuple(s.site_atom for s in measured.sites) == (1, 10)
    assert measured.degradant is not None
    assert measured.degradant.degradant_id == "4_aminophenol"
    assert not measured.degradant_identification.identified
    assert "MDS95" in model.source_ids
    assert par.computed_cache_keys(model) == ()
    shares = par.nominal_shares(model, {p.pathway_id: 1.0 for p in model.pathways}, 0.5, 0.0, False)
    summary = par.basis_summary(model, shares, 0.01, assay_used=False)
    assert (summary.prior_basis, summary.estimate_basis, summary.computed_only) == (
        "measured_model",
        "measured_model",
        False,
    )


@pytest.mark.parametrize("evidence", [[], ["salicylic_acid"], ["4_aminophenol"]])
def test_anchor_degradant_evidence(mutate_bundle: BundleMutator, evidence: list[str]) -> None:
    def change(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "ester_hydrolysis")
        anchor = find(cls["anchors"], "anchor_id", "aspirin_tablet_iss_550d")
        anchor["measured_degradant_ids"] = evidence

    bundle = mutate_bundle({"reaction-classes": change})
    pathway = _pathway(_model(bundle, "aspirin_325_tab", "aspirin"), "ester_hydrolysis")
    identification = pathway.degradant_identification
    assert identification.identified == ("salicylic_acid" in evidence)
    if identification.identified:
        assert identification.evidence_kind == "anchor"
        assert identification.evidence_id == "aspirin_tablet_iss_550d"
        assert identification.source_ids == tuple(
            sorted(set(_aspirin_anchor(bundle)["ln_k_per_s"]["source_ids"]))
        )
    else:
        assert identification.evidence_id is None
        assert identification.evidence_kind is None
        assert identification.source_ids == ()


@pytest.mark.parametrize("evidence", [[], ["salicylic_acid"], ["4_aminophenol"]])
def test_measured_degradant_evidence(mutate_bundle: BundleMutator, evidence: list[str]) -> None:
    bundle = mutate_bundle(
        {"stability-models": _measured("prod_apap", None, measured_degradant_ids=evidence)}
    )
    model = _model(bundle, "acetaminophen_325_tab", "acetaminophen")
    pathway = model.pathways[0]
    identification = pathway.degradant_identification
    assert identification.identified == ("4_aminophenol" in evidence)
    if identification.identified:
        assert identification.evidence_kind == "measured_model"
        assert identification.evidence_id == "prod_apap"
        measured = par.select_measured_model(
            bundle, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"]
        )
        assert measured is not None
        assert identification.source_ids == tuple(sorted(set(measured["source_ids"])))
    else:
        assert identification.evidence_id is None


def test_current_formulary_degradants_are_unidentified(bundle: ConfigBundle) -> None:
    for key in sorted(EXPECTED):
        for pathway in _model(bundle, *key).pathways:
            assert not pathway.degradant_identification.identified


def test_omitted_degradant_evidence_is_unidentified(bundle: ConfigBundle) -> None:
    def change(doc: dict[str, Any]) -> None:
        for cls in doc["classes"]:
            for anchor in cls["anchors"]:
                anchor.pop("measured_degradant_ids", None)

    edited = unvalidated(bundle, "reaction-classes", change)
    pathway = _pathway(_model(edited, "aspirin_325_tab", "aspirin"), "ester_hydrolysis")
    assert not pathway.degradant_identification.identified


def test_measured_model_prefers_package_specific(mutate_bundle: BundleMutator) -> None:
    def both(doc: dict[str, Any]) -> None:
        _measured("prod_any", None)(doc)
        _measured("prod_bottle", "original_bottle")(doc)

    edited = mutate_bundle({"stability-models": both})
    bottle = par.select_measured_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    assert bottle is not None
    assert bottle["model_id"] == "prod_bottle"
    mixed = par.select_measured_model(
        edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle", "repackaged_bag"]
    )
    assert mixed is not None
    assert mixed["model_id"] == "prod_any"
    assert par.select_measured_model(edited, "aspirin_325_tab", "aspirin", ["original_bottle"]) is None


def test_measured_model_with_rh_conversion(mutate_bundle: BundleMutator) -> None:
    changes = {
        "reference_temperature_k": 313.15,
        "reference_rh_percent": 75.0,
        "parameters": {
            "names": ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
            "mean": [-14.0, 90000.0, 0.03],
            "covariance": [[0.04, -500.0, 0.0001], [-500.0, 1.0e7, 0.0], [0.0001, 0.0, 0.0004]],
        },
    }
    edited = mutate_bundle({"stability-models": _measured("prod_rh", None, **changes)})
    model = par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    measured = model.pathways[0]
    assert measured.parameters.mean[0] == pytest.approx(-16.7890511411748269, rel=1e-14)
    assert measured.parameters.covariance[0][2] == pytest.approx(-0.0139, rel=1e-12)


def test_measured_model_errors(mutate_bundle: BundleMutator) -> None:
    no_rh = {
        "parameters": {
            "names": ["ln_k_ref_per_s", "b_per_percent_rh"],
            "mean": [-14.0, 0.03],
            "covariance": [[0.04, 0.0], [0.0, 0.0004]],
        }
    }
    edited = mutate_bundle({"stability-models": _measured("prod_bad", None, **no_rh)})
    with pytest.raises(ConfigReferenceError, match="reference_rh_percent"):
        par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    light = {
        "parameters": {
            "names": ["ln_k_ref_per_s", "ln_k_vis_per_lux_h"],
            "mean": [-14.0, -16.0],
            "covariance": [[0.04, 0.0], [0.0, 1.0]],
        }
    }
    edited = mutate_bundle({"stability-models": _measured("prod_light", None, **light)})
    with pytest.raises(ModelDomainError, match="non-thermal"):
        par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    fn = {"rate_law_kind": "Fn", "kind_parameters": {"order_n": None, "alpha0": None, "avrami_n": None}}
    edited = mutate_bundle({"stability-models": _measured("prod_fn", None, **fn)})
    with pytest.raises(ModelDomainError, match="needs order_n"):
        par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    fn_ok = {"rate_law_kind": "Fn", "kind_parameters": {"order_n": 1.5, "alpha0": None, "avrami_n": None}}
    edited = mutate_bundle({"stability-models": _measured("prod_fn", None, **fn_ok)})
    model = par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])
    assert (model.rate_law_kind, model.order_n) == ("Fn", 1.5)


def test_measured_ea_needs_class_lower_bound(mutate_bundle: BundleMutator) -> None:
    def no_lower(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "amide_hydrolysis")
        find(cls["priors"], "dose_form_family", "solid")["ea_j_per_mol"]["lower"] = None

    # The schema rejects a truncated normal without a lower bound; the engine checks anyway.
    edited = unvalidated(
        mutate_bundle({"stability-models": _measured("prod_apap", None)}), "reaction-classes", no_lower
    )
    with pytest.raises(ModelDomainError, match="lower bound"):
        par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])


def test_missing_priors_raise(mutate_bundle: BundleMutator, bundle: ConfigBundle) -> None:
    def drop_family(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "unclassified_thermal")
        cls["priors"] = [p for p in cls["priors"] if p["dose_form_family"] != "solid"]

    with pytest.raises(ConfigReferenceError, match="no priors for family"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": drop_family}),
            "ibuprofen_400_tab",
            "ibuprofen",
            ["original_bottle"],
        )

    def no_ea(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "unclassified_thermal")
        find(cls["priors"], "dose_form_family", "solid")["ea_j_per_mol"] = None

    with pytest.raises(ConfigReferenceError, match="no Ea prior"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": no_ea}), "ibuprofen_400_tab", "ibuprofen", ["original_bottle"]
        )

    def normal_ea(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "unclassified_thermal")
        find(cls["priors"], "dose_form_family", "solid")["ea_j_per_mol"]["family"] = "normal"

    with pytest.raises(ModelDomainError, match="truncated normal"):  # schema-rejected input
        par.build_stability_model(
            unvalidated(bundle, "reaction-classes", normal_ea),
            "ibuprofen_400_tab",
            "ibuprofen",
            ["original_bottle"],
        )

    def no_total(doc: dict[str, Any]) -> None:
        doc["total_rate_priors"] = [t for t in doc["total_rate_priors"] if t["dose_form_family"] != "solid"]

    with pytest.raises(ConfigReferenceError, match="total_rate_priors"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": no_total}),
            "ibuprofen_400_tab",
            "ibuprofen",
            ["original_bottle"],
        )

    def no_racemization_prior(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "racemization_benzylic_alcohol")
        find(cls["priors"], "dose_form_family", "solution")["ln_k_ref_per_s"] = None

    with pytest.raises(ConfigReferenceError, match="normal ln k prior"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": no_racemization_prior}),
            "epinephrine_0_1mg_ml_syringe",
            "epinephrine",
            ["prefilled_syringe"],
        )

    def no_light(doc: dict[str, Any]) -> None:
        find(doc["classes"], "class_id", "photolysis_conjugated")["light_priors"] = None

    with pytest.raises(ConfigReferenceError, match="light_priors"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": no_light}),
            "promethazine_25_tab",
            "promethazine",
            ["original_bottle"],
        )

    def no_default(doc: dict[str, Any]) -> None:
        doc["rate_law_defaults"] = [d for d in doc["rate_law_defaults"] if d["dose_form_family"] != "solid"]

    with pytest.raises(ConfigReferenceError, match="rate_law_defaults"):
        par.build_stability_model(
            mutate_bundle({"engine-settings": no_default}),
            "ibuprofen_400_tab",
            "ibuprofen",
            ["original_bottle"],
        )


def test_unusable_multiplier_priors(mutate_bundle: BundleMutator, bundle: ConfigBundle) -> None:
    def fixed_zero(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "thioether_s_oxidation")
        prior = find(cls["priors"], "dose_form_family", "solution")["frozen_rate_multiplier"]
        prior.update(family="fixed", mu=0.0, sigma=0.0)

    with pytest.raises(ModelDomainError, match="must be positive"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": fixed_zero}),
            "promethazine_25mg_ml_inj",
            "promethazine",
            ["original_vial"],
        )

    def fixed_two(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "thioether_s_oxidation")
        prior = find(cls["priors"], "dose_form_family", "solution")["frozen_rate_multiplier"]
        prior.update(family="fixed", mu=2.0, sigma=0.0)

    model = par.build_stability_model(
        mutate_bundle({"reaction-classes": fixed_two}),
        "promethazine_25mg_ml_inj",
        "promethazine",
        ["original_vial"],
    )
    assert _value(_pathway(model, "thioether_s_oxidation"), "ln_frozen_rate_multiplier") == (
        math.log(2.0),
        0.0,
    )

    def normal_multiplier(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "thioether_s_oxidation")
        find(cls["priors"], "dose_form_family", "solution")["frozen_rate_multiplier"]["family"] = "normal"

    with pytest.raises(ModelDomainError, match="lognormal or fixed"):
        par.build_stability_model(
            mutate_bundle({"reaction-classes": normal_multiplier}),
            "promethazine_25mg_ml_inj",
            "promethazine",
            ["original_vial"],
        )

    def lognormal_b(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "thioether_s_oxidation")
        find(cls["priors"], "dose_form_family", "solution")["b_per_percent_rh"]["family"] = "lognormal"

    with pytest.raises(ModelDomainError, match="normal or fixed"):  # schema-rejected input
        par.build_stability_model(
            unvalidated(bundle, "reaction-classes", lognormal_b),
            "promethazine_25mg_ml_inj",
            "promethazine",
            ["original_vial"],
        )


def test_anchor_family_fallback(bundle: ConfigBundle) -> None:
    cls = reaction_class(bundle, "ester_hydrolysis")
    solid = par.select_anchor(cls, "solid")
    assert solid is not None
    assert solid["anchor_id"] == "aspirin_tablet_iss_550d"
    semisolid = par.select_anchor(cls, "semisolid")
    assert semisolid is not None
    assert semisolid["anchor_id"] == "aspirin_tablet_iss_550d"
    suspension = par.select_anchor(cls, "suspension")
    assert suspension is not None
    assert suspension["anchor_id"] == "aspirin_pbs_ph7_4"
    assert par.select_anchor(reaction_class(bundle, "phenol_autoxidation"), "solid") is None
    only_solution: Any = copy.deepcopy(cls)
    only_solution["anchors"] = [a for a in cls["anchors"] if a["dose_form_family"] == "solution"]
    assert par.select_anchor(only_solution, "solid") is None


def test_family_priors_lookup(bundle: ConfigBundle) -> None:
    assert (
        par.family_priors(reaction_class(bundle, "ester_hydrolysis"), "solid")["dose_form_family"] == "solid"
    )
    with pytest.raises(ConfigReferenceError):
        par.family_priors(reaction_class(bundle, "racemization_benzylic_alcohol"), "solid")


def test_class_generic_degradant(mutate_bundle: BundleMutator, bundle: ConfigBundle) -> None:
    pathway = _pathway(_model(bundle, "levofloxacin_500_tab", "levofloxacin"), "amine_alpha_oxidation")
    assert pathway.degradant is not None
    assert (pathway.degradant.degradant_id, pathway.degradant.product_specific) == (
        "n_dealkylation_n_oxide",
        False,
    )
    assert pathway.degradant.limit_percent is None
    assert pathway.degradant.molar_mass_g_per_mol is None

    def with_mass(doc: dict[str, Any]) -> None:
        find(doc["classes"], "class_id", "amine_alpha_oxidation")["degradant"]["molar_mass_g_per_mol"] = (
            sourced(300.0, "g/mol")
        )

    edited = mutate_bundle({"reaction-classes": with_mass})
    pathway = _pathway(
        par.build_stability_model(edited, "levofloxacin_500_tab", "levofloxacin", ["original_blister"]),
        "amine_alpha_oxidation",
    )
    assert pathway.degradant is not None
    assert pathway.degradant.molar_mass_g_per_mol == 300.0


# --- layer helpers and the estimate basis ----------------------------------------------------------
def test_layer_helpers(bundle: ConfigBundle) -> None:
    model = _model(bundle, "acetaminophen_325_tab", "acetaminophen")
    amide = _pathway(model, "amide_hydrolysis")
    layers = par.parameter_layers(amide)
    assert [layer.name for layer in layers] == ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"]
    assert [layer.layer for layer in layers] == ["computed", "class_prior", "class_prior"]
    assert layers[0].method == "computed:anchor_only"
    assert par.weakest_layer(["measured_model", "computed"]) == "computed"
    assert par.weakest_layer(["assay_updated", "measured_model"]) == "assay_updated"
    with pytest.raises(ModelDomainError):
        par.weakest_layer([])
    empty = dataclass_replace_estimates(amide)
    with pytest.raises(ModelDomainError, match="no ln_k_ref_per_s"):
        par.pathway_layer(empty)


def dataclass_replace_estimates(pathway: Pathway) -> Pathway:
    block = dataclasses.replace(pathway.parameters, estimates=pathway.parameters.estimates[1:])
    return dataclasses.replace(pathway, parameters=block)


def test_nominal_shares_and_basis(bundle: ConfigBundle) -> None:
    model = _model(bundle, "acetaminophen_325_tab", "acetaminophen")
    theta = {
        "acetaminophen:amide_hydrolysis": 3.0,
        "acetaminophen:phenol_autoxidation": 1.0,
        "acetaminophen:radiolysis": 4.0,
    }
    shares = par.nominal_shares(model, theta, 0.5, 0.0, include_radiation=False)
    assert shares == {
        "acetaminophen:amide_hydrolysis": 0.75,
        "acetaminophen:phenol_autoxidation": 0.25,
        "acetaminophen:radiolysis": 0.0,
    }
    with_rad = par.nominal_shares(model, theta, 0.5, 0.0, include_radiation=True)
    assert with_rad["acetaminophen:radiolysis"] == 0.5
    summary = par.basis_summary(model, shares, 0.01, assay_used=False)
    assert summary.contributing == ("acetaminophen:amide_hydrolysis", "acetaminophen:phenol_autoxidation")
    assert (summary.prior_basis, summary.estimate_basis, summary.computed_only) == (
        "class_prior",
        "class_prior",
        True,
    )
    only_amide = par.basis_summary(model, shares, 0.5, assay_used=True)
    assert only_amide.contributing == ("acetaminophen:amide_hydrolysis",)
    assert (only_amide.prior_basis, only_amide.estimate_basis, only_amide.computed_only) == (
        "computed",
        "computed",
        False,
    )
    nothing = par.basis_summary(model, dict.fromkeys(theta, 0.0), 0.01, assay_used=False)
    assert len(nothing.contributing) == 3
    zero = par.nominal_shares(model, dict.fromkeys(theta, 0.0), 0.5, 0.0, include_radiation=True)
    assert set(zero.values()) == {0.0}
    with pytest.raises(ModelDomainError, match="missing or invalid"):
        par.nominal_shares(model, {"acetaminophen:amide_hydrolysis": 1.0}, 0.5, 0.0, False)
    with pytest.raises(ModelDomainError):
        par.nominal_shares(model, {**theta, "acetaminophen:radiolysis": -1.0}, 0.5, 0.0, False)
    with pytest.raises(ModelDomainError):
        par.nominal_shares(model, theta, math.nan, 0.0, False)
    with pytest.raises(ModelDomainError):
        par.basis_summary(model, shares, math.nan, assay_used=False)


def test_nominal_shares_racemization_hand_value(bundle: ConfigBundle) -> None:
    """s_r = (1 - rho/f0)/(1 - (1 - alpha) rho/f0) with f0 0.995, rho 0.95, alpha 0.5:
    0.045/0.52 = 0.0865384615384615."""
    model = _model(bundle, "epinephrine_0_1mg_ml_syringe", "epinephrine")
    theta = {p.pathway_id: 1.0 for p in model.pathways}
    shares = par.nominal_shares(model, theta, 0.5, 0.95, include_radiation=False)
    s_r = 0.0865384615384615385
    assert shares["epinephrine:racemization_benzylic_alcohol"] == pytest.approx(s_r, rel=1e-14)
    assert shares["epinephrine:catechol_autoxidation"] == pytest.approx((1 - s_r) / 2, rel=1e-14)
    assert shares["epinephrine:amine_alpha_oxidation"] == pytest.approx((1 - s_r) / 2, rel=1e-14)
    assert shares["epinephrine:radiolysis"] == 0.0
    # rho = f0: nothing racemized.
    assert (
        par.nominal_shares(model, theta, 0.5, 0.995, False)["epinephrine:racemization_benzylic_alcohol"]
        == 0.0
    )
    no_theta = {**theta, "epinephrine:racemization_benzylic_alcohol": 0.0}
    assert (
        par.nominal_shares(model, no_theta, 0.5, 0.95, False)["epinephrine:racemization_benzylic_alcohol"]
        == 0.0
    )


def test_nominal_shares_rejects_non_positive_f0(bundle: ConfigBundle) -> None:
    model = dataclasses.replace(
        _model(bundle, "epinephrine_0_1mg_ml_syringe", "epinephrine"), initial_active_fraction=0.0
    )
    with pytest.raises(ModelDomainError, match="initial active fraction"):
        par.nominal_shares(model, {p.pathway_id: 1.0 for p in model.pathways}, 0.5, 0.5, False)


def test_computed_cache_keys_point_at_committed_entries(bundle: ConfigBundle) -> None:
    keys: set[str] = set()
    for product_id, ingredient_id in EXPECTED:
        keys.update(par.computed_cache_keys(_model(bundle, product_id, ingredient_id)))
    assert keys
    for key in keys:
        assert (DEFAULT_CACHE_DIR / f"{key}.json").is_file()
    # The cache also holds sites no model consults today (free groups of one: acetaminophen,
    # levofloxacin, and the phenol reference), as spec 7.4 commits every formulary site.
    assert keys < {path.stem for path in DEFAULT_CACHE_DIR.glob("*.json")}


def test_block_rejects_bad_variance_and_weight(bundle: ConfigBundle) -> None:
    pathway = _pathway(_model(bundle, "ibuprofen_400_tab", "ibuprofen"), "unclassified_thermal")
    estimate = pathway.parameters.estimates[0]
    good = par._Component("ln_k_ref_per_s", -20.0, 0.25, estimate)
    with pytest.raises(ModelDomainError, match="variance"):
        par._block([par._Component("ln_k_ref_per_s", -20.0, -0.25, estimate)], 0.0, 1.0)
    with pytest.raises(ModelDomainError, match="mean"):
        par._block([par._Component("ln_k_ref_per_s", math.inf, 0.25, estimate)], 0.0, 1.0)
    with pytest.raises(ModelDomainError, match="shared weight"):
        par._block([good], 0.0, 1.5)


def test_thermal_block_without_humidity_term(mutate_bundle: BundleMutator) -> None:
    def no_b(doc: dict[str, Any]) -> None:
        cls = find(doc["classes"], "class_id", "unclassified_thermal")
        find(cls["priors"], "dose_form_family", "solid")["b_per_percent_rh"] = None

    model = par.build_stability_model(
        mutate_bundle({"reaction-classes": no_b}), "ibuprofen_400_tab", "ibuprofen", ["original_bottle"]
    )
    assert _pathway(model, "unclassified_thermal").parameters.names == ("ln_k_ref_per_s", "ea_j_per_mol")


def test_measured_model_without_ea_and_with_negative_variance(mutate_bundle: BundleMutator) -> None:
    only_k = {"parameters": {"names": ["ln_k_ref_per_s"], "mean": [-14.0], "covariance": [[0.04]]}}
    edited = mutate_bundle({"stability-models": _measured("prod_k", None, **only_k)})
    measured = par.build_stability_model(
        edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"]
    ).pathways[0]
    assert measured.parameters.names == ("ln_k_ref_per_s",)
    assert measured.parameters.ea_lower_j_per_mol == par.NO_EA_LOWER_BOUND
    negative = {"parameters": {"names": ["ln_k_ref_per_s"], "mean": [-14.0], "covariance": [[-0.04]]}}
    edited = mutate_bundle({"stability-models": _measured("prod_neg", None, **negative)})
    with pytest.raises(ModelDomainError, match="negative variance"):
        par.build_stability_model(edited, "acetaminophen_325_tab", "acetaminophen", ["original_bottle"])


@pytest.mark.parametrize("temperature", [0.1, 1e-300])
def test_reference_conversion_rejects_overflow(temperature: float) -> None:
    with pytest.raises(ModelDomainError):
        par.convert_reference(
            ("ln_k_ref_per_s", "ea_j_per_mol"),
            (1e308, 1e308),
            ((1.0, 0.0), (0.0, 1.0)),
            temperature,
            40.0,
            298.15,
            40.0,
        )
