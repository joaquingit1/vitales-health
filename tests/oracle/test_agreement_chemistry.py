# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Radiation constants, parameter algebra and LFER functions against the oracle (spec 6,
7.3, 9.3 to 9.5, 15.2) at 1e-9 relative (1e-12 absolute near zero)."""

import dataclasses
import math
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pharmacy.config import ConfigBundle
from tests.oracle import chemistry as oc
from tests.oracle.compare import assert_agrees

HYPOTHESIS = settings(derandomize=True, max_examples=200, deadline=None)
POSITIVE = st.floats(1e-12, 1e3)


# --- Radiation (spec 6) ----------------------------------------------------------------------------
@HYPOTHESIS
@given(g=st.floats(1e-12, 1e-4), m=st.floats(10.0, 2000.0))
def test_k_dose_direct(g: float, m: float) -> None:
    from pharmacy.chemistry import radiolysis

    assert_agrees(
        "radiolysis.k_dose_direct", locals(), radiolysis.k_dose_direct(g, m), oc.k_dose_direct(g, m), "6.2"
    )


@HYPOTHESIS
@given(g=st.floats(1e-12, 1e-4), c=st.floats(0.0, 5.0), c_half=st.floats(1e-6, 1.0))
def test_k_dose_indirect(g: float, c: float, c_half: float) -> None:
    from pharmacy.chemistry import radiolysis

    engine = radiolysis.k_dose_indirect(g, c, c_half)
    assert_agrees("radiolysis.k_dose_indirect", locals(), engine, oc.k_dose_indirect(g, c, c_half), "6.3")
    if c > 0:
        assert_agrees(
            "radiolysis.k_dose_indirect", locals(), engine, oc.k_dose_indirect_from_g_eff(g, c, c_half), "6.3"
        )


@HYPOTHESIS
@given(k=st.floats(0.0, 0.1), f=st.floats(1.0, 100.0), dose=st.floats(0.0, 1e4))
def test_upper_bound_loss_fraction(k: float, f: float, dose: float) -> None:
    from pharmacy.chemistry import radiolysis

    engine = radiolysis.upper_bound_loss_fraction(k, f, dose)
    assert_agrees(
        "radiolysis.upper_bound_loss_fraction",
        locals(),
        engine,
        oc.upper_bound_loss_fraction(k, f, dose),
        "6.4",
    )


def test_upper_bound_with_configured_constants(bundle: ConfigBundle) -> None:
    from pharmacy.chemistry import radiolysis

    f = bundle.settings["radiation"]["dose_rate_safety_factor"]["value"]
    for family in ("solid", "semisolid", "solution", "suspension"):
        k = radiolysis.upper_bound_k_per_gy(bundle.radiation, family)
        for dose in (0.0, 0.45, 0.9, 1.0, 10.0):
            assert_agrees(
                "radiolysis.upper_bound_loss_fraction",
                {"family": family, "dose": dose, "k": k, "F": f},
                radiolysis.upper_bound_loss_fraction(k, f, dose),
                oc.upper_bound_loss_fraction(k, f, dose),
                "6.4",
            )


# --- Parameters (spec 9.3 to 9.5) -----------------------------------------------------------------
CONVERSION_CASES: list[tuple[list[str], list[float], list[list[float]]]] = [
    (
        ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
        [-18.0, 90000.0, 0.03],
        [[0.25, 300.0, 0.001], [300.0, 4.0e6, 0.0], [0.001, 0.0, 1e-4]],
    ),
    (["ea_j_per_mol", "ln_k_ref_per_s"], [65000.0, -12.0], [[1.9e8, 120.0], [120.0, 0.8]]),
    (["ln_k_ref_per_s"], [-14.0], [[0.3]]),
    (
        ["ln_k_ref_per_s", "b_per_percent_rh", "ln_k_dose_per_gy"],
        [-17.0, 0.05, -12.0],
        [[0.4, 0.002, 0.0], [0.002, 4e-4, 0.0], [0.0, 0.0, 1.3]],
    ),
]
CONDITIONS = [
    (313.15, 75.0, 298.15, 40.0),
    (298.15, 40.0, 298.15, 40.0),
    (277.15, 60.0, 298.15, 40.0),
    (343.15, 10.0, 298.15, 40.0),
]


@pytest.mark.parametrize("case", CONVERSION_CASES, ids=lambda c: "+".join(c[0]))
@pytest.mark.parametrize("conditions", CONDITIONS, ids=str)
def test_convert_reference(
    case: tuple[list[str], list[float], list[list[float]]], conditions: tuple[float, ...]
) -> None:
    from pharmacy.chemistry import parameters

    names, mean, cov = case
    engine_mean, engine_cov = parameters.convert_reference(cast(Any, names), mean, cov, *conditions)
    oracle_mean, oracle_cov = oc.convert_reference(names, mean, cov, *conditions)
    inputs = {"names": names, "mean": mean, "conditions": conditions}
    for i in range(len(names)):
        assert_agrees(
            "parameters.convert_reference", inputs | {"mean": i}, engine_mean[i], oracle_mean[i], "9.3"
        )
        for j in range(len(names)):
            assert_agrees(
                "parameters.convert_reference",
                inputs | {"cov": (i, j)},
                engine_cov[i][j],
                oracle_cov[i][j],
                "9.3",
            )


@HYPOTHESIS
@given(
    mu_p=st.floats(-40.0, 0.0),
    sd_p=st.floats(1e-3, 5.0),
    mu_c=st.floats(-40.0, 0.0),
    sd_c=st.floats(1e-3, 5.0),
)
def test_precision_weighted(mu_p: float, sd_p: float, mu_c: float, sd_c: float) -> None:
    from pharmacy.chemistry import parameters

    engine = parameters.precision_weighted(mu_p, sd_p, mu_c, sd_c)
    oracle = oc.precision_weighted(mu_p, sd_p, mu_c, sd_c)
    assert_agrees("parameters.precision_weighted", locals() | {"part": "mu"}, engine[0], oracle[0], "9.4")
    assert_agrees("parameters.precision_weighted", locals() | {"part": "sd"}, engine[1], oracle[1], "9.4")


@pytest.mark.parametrize("n", [1, 2, 3, 5, 8])
def test_class_prior_ln_k(bundle: ConfigBundle, n: int) -> None:
    from pharmacy.chemistry import parameters

    for total in bundle.reaction_classes["total_rate_priors"]:
        prior = total["ln_k_ref_per_s"]
        engine = parameters.class_prior_ln_k(total, n)
        oracle = oc.class_prior_ln_k(prior["mu"], prior["sigma"], n)
        inputs = {"family": total["dose_form_family"], "N": n}
        assert_agrees("parameters.class_prior_ln_k", inputs | {"part": "mu"}, engine[0], oracle[0], "9.5")
        assert_agrees("parameters.class_prior_ln_k", inputs | {"part": "sd"}, engine[1], oracle[1], "9.5")


# --- LFER (spec 7.3) ----------------------------------------------------------------------------------
@HYPOTHESIS
@given(rho=st.floats(-5.0, 5.0), s_site=st.floats(-3.0, 3.0), s_anchor=st.floats(-3.0, 3.0))
def test_hammett(rho: float, s_site: float, s_anchor: float) -> None:
    from pharmacy.chemistry import lfer

    engine = lfer.hammett_delta_ln_k(rho, s_site, s_anchor)
    assert_agrees(
        "lfer.hammett_delta_ln_k", locals(), engine, oc.hammett_delta_ln_k(rho, s_site, s_anchor), "7.3.2"
    )


@HYPOTHESIS
@given(
    rho=st.floats(-5.0, 5.0),
    delta=st.floats(-3.0, 3.0),
    d_sigma=st.floats(-3.0, 3.0),
    d_es=st.floats(-3.0, 3.0),
)
def test_taft(rho: float, delta: float, d_sigma: float, d_es: float) -> None:
    from pharmacy.chemistry import lfer

    engine = lfer.taft_delta_ln_k(rho, delta, d_sigma, d_es)
    assert_agrees(
        "lfer.taft_delta_ln_k", locals(), engine, oc.taft_delta_ln_k(rho, delta, d_sigma, d_es), "7.3.3"
    )


@HYPOTHESIS
@given(
    alpha=st.floats(0.0, 1.0),
    bde=st.floats(250.0, 500.0),
    ref=st.floats(250.0, 500.0),
    t=st.floats(250.0, 350.0),
)
def test_evans_polanyi(alpha: float, bde: float, ref: float, t: float) -> None:
    from pharmacy.chemistry import lfer

    engine = lfer.evans_polanyi_delta_ln_k(alpha, bde, ref, t)
    assert_agrees(
        "lfer.evans_polanyi_delta_ln_k",
        locals(),
        engine,
        oc.evans_polanyi_delta_ln_k(alpha, bde, ref, t),
        "7.3.4",
    )


@HYPOTHESIS
@given(deltas=st.lists(st.floats(-30.0, 30.0), min_size=1, max_size=6), n_ref=st.integers(1, 6))
def test_combine_sites(deltas: list[float], n_ref: int) -> None:
    from pharmacy.chemistry import lfer

    engine = lfer.combine_sites(tuple(deltas), n_ref)
    assert_agrees("lfer.combine_sites", locals(), engine, oc.combine_sites(deltas, n_ref), "7.3.5")


def test_combine_sites_large_values_do_not_overflow() -> None:
    from pharmacy.chemistry import lfer

    deltas = (800.0, 799.0)
    assert_agrees(
        "lfer.combine_sites",
        {"deltas": deltas},
        lfer.combine_sites(deltas, 2),
        oc.combine_sites(deltas, 2),
        "7.3.5",
    )
    assert math.isfinite(lfer.combine_sites(deltas, 2))


# --- Computed-layer rules added to the spec mid-build (7.3.4, 7.3.5, 9.5) ---------------------------
@pytest.mark.parametrize(
    ("site", "reference"), [(380.5, 350.0), (310.0, 350.0), (379.0, 350.0), (321.0, 350.0)]
)
def test_evans_polanyi_applicability_domain(bundle: ConfigBundle, site: float, reference: float) -> None:
    """Spec 7.3.4: delta_s = 0 and the site flagged outside the applicability domain when
    |BDE_site - BDE_ref| exceeds max_abs_delta_bde_kj_per_mol (the configured limit; the
    engine applies it in ``lfer.evans_polanyi_site_delta``)."""
    from pharmacy.chemistry import lfer

    entries = bundle.lfer["evans_polanyi"]
    limit = entries[0]["max_abs_delta_bde_kj_per_mol"]["value"]
    engine, inside = lfer.evans_polanyi_site_delta(0.5, site, reference, 298.15, limit)
    oracle = oc.evans_polanyi_delta_ln_k(0.5, site, reference, 298.15, limit)
    inputs = {"site": site, "ref": reference, "limit": limit}
    assert_agrees("lfer.evans_polanyi_site_delta", inputs, engine, oracle, "7.3.4")
    assert inside is not oc.outside_applicability_domain(site, reference, limit), inputs


@pytest.mark.parametrize(
    ("deltas", "hydrogens", "reference"),
    [
        ((0.0, 0.0, 0.0), (3, 3, 3), 9),
        ((1.0,), (2,), 9),
        ((0.3, -2.0), (1, 1), 2),
        ((-1.5,), (1,), 1),
        ((4.0, 0.0), (2, 3), 9),
    ],
)
def test_combine_sites_weighted_by_hydrogens(
    deltas: tuple[float, ...], hydrogens: tuple[int, ...], reference: int
) -> None:
    """Spec 7.3.5: Evans-Polanyi sites weighted by abstractable hydrogens
    (``lfer.combine_weighted_sites``)."""
    from pharmacy.chemistry import lfer

    engine = lfer.combine_weighted_sites(deltas, hydrogens, reference)
    oracle = oc.combine_sites_weighted(deltas, hydrogens, reference)
    inputs = {"deltas": deltas, "n_H": hydrogens, "sum n_H,r": reference}
    assert_agrees("lfer.combine_weighted_sites", inputs, engine, oracle, "7.3.5")


_MODELS: dict[int, list[tuple[str, str, Any]]] = {}


def _split_members(bundle: ConfigBundle) -> list[tuple[str, str, Any]]:
    """Every formulary ingredient's model (BDE cache: the isolated copy), built once."""
    from tests.scenarios.bundles import stability_model

    if id(bundle) not in _MODELS:
        out = []
        for product in bundle.formulary["products"]:
            for ingredient in product["ingredients"]:
                model = stability_model(
                    bundle,
                    product["product_id"],
                    ingredient["ingredient_id"],
                    [product["default_package_id"]],
                )
                out.append((product["product_id"], ingredient["ingredient_id"], model))
        _MODELS[id(bundle)] = out
    return _MODELS[id(bundle)]


def _split(model: Any) -> list[tuple[Any, oc.SplitMember]]:
    """The 9.5 split as the public model shows it: the class-based destructive thermal
    pathways (layers computed and class_prior); anchored when the rate layer is computed
    (7.3.1); a free member's shift and method error from ``Pathway.free_share`` (none for a
    free group of one, whose shift is not computed)."""
    from pharmacy.chemistry import parameters

    out = []
    for p in model.pathways:
        layer = parameters.pathway_layer(p)
        if p.role != "destructive" or p.driver != "thermal" or layer not in ("computed", "class_prior"):
            continue
        if layer == "computed":
            out.append((p, oc.SplitMember(True)))
        elif p.free_share is not None:
            out.append((p, oc.SplitMember(False, p.free_share.delta_ln, p.free_share.method_sd_ln)))
        else:
            out.append((p, oc.SplitMember(False)))
    return out


def test_evans_polanyi_shifts_redistribute_the_class_total(bundle: ConfigBundle) -> None:
    """Spec 9.5 (final form) on every formulary ingredient. N counts anchored and free
    members. A free member's block is mu_total + ln(N_F/N) + delta_i - ln(sum_F exp(delta_j))
    with sd s_p and w = 1; it carries a free share exactly when the free group has two or
    more members. Anchored members are combined by 9.4 and are not checked here."""
    from pharmacy.chemistry import parameters

    checked = 0
    for product_id, ingredient_id, model in _split_members(bundle):
        split = _split(model)
        if not split:
            continue
        total = parameters.total_rate_prior(bundle, model.dose_form_family)["ln_k_ref_per_s"]
        members = [m for _, m in split]
        priors = oc.split_class_prior(total["mu"], total["sigma"], members)
        for (p, member), prior in zip(split, priors, strict=True):
            if member.anchored:
                continue
            block = p.parameters
            i = block.names.index("ln_k_ref_per_s")
            inputs = {
                "product": product_id,
                "ingredient": ingredient_id,
                "pathway": p.pathway_id,
                "members": [dataclasses.asdict(m) for m in members],
            }
            assert_agrees("parameters.build_stability_model (mu_p,i)", inputs, block.mean[i], prior.mu, "9.5")
            assert_agrees(
                "parameters.build_stability_model (sd)",
                inputs,
                math.sqrt(block.covariance[i][i]),
                prior.sd,
                "9.5",
            )
            assert_agrees(
                "parameters.build_stability_model (w)",
                inputs,
                block.shared_weight,
                prior.shared_weight,
                "9.5",
            )
            assert (p.free_share is not None) is (prior.free_share is not None), (
                f"{inputs}: free_share {p.free_share} where spec 9.5 gives "
                f"{'one' if prior.free_share is not None else 'none'}"
            )
            if p.free_share is not None:
                assert p.free_share.method_sd_ln >= 0, inputs
            checked += 1
    assert checked > 0


DRAW_CHECK_N = 20000


def test_free_group_draws_keep_the_class_total(bundle: ConfigBundle) -> None:
    """Spec 9.5 draw rule on every formulary ingredient with a free group of two or more:
    G = ln(sum_F k_i) - mu_total - ln(N_F/N) equals s_p z_cls in every draw, so over
    20000 draws of ``uncertainty.draw_parameters`` its mean is within 5 standard errors of 0
    and its sd within 5 standard errors of s_p (a linearized share error would widen it)."""
    import numpy as np

    from pharmacy import uncertainty
    from pharmacy.chemistry import parameters

    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    checked = []
    for product_id, ingredient_id, model in _split_members(bundle):
        split = _split(model)
        free = [model.pathways.index(p) for p, m in split if not m.anchored]
        if len(free) < 2:
            continue
        total = parameters.total_rate_prior(bundle, model.dose_form_family)["ln_k_ref_per_s"]
        rng = np.random.Generator(np.random.PCG64(20261114))
        block = uncertainty.draw_parameters(model, 100.0, 1.5, zones, ["ground_storage"], rng, DRAW_CHECK_N)
        ln_k = np.asarray(block.ln_k_ref_per_s)[:, free]
        top = ln_k.max(axis=1)
        g = top + np.log(np.exp(ln_k - top[:, None]).sum(axis=1))
        g -= total["mu"] + math.log(len(free) / len(split))
        s_p = total["sigma"]
        mean, sd = float(g.mean()), float(g.std(ddof=1))
        inputs = {"product": product_id, "ingredient": ingredient_id, "N_F": len(free), "N": len(split)}
        assert abs(mean) <= 5 * s_p / math.sqrt(DRAW_CHECK_N), (inputs, mean, s_p)
        assert abs(sd / s_p - 1) <= 5 / math.sqrt(2 * DRAW_CHECK_N), (inputs, sd, s_p)
        checked.append(inputs)
    if not checked:
        pytest.skip("no formulary ingredient has a free group of two or more members")
