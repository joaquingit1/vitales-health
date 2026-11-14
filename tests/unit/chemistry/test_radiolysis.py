"""Unit tests of pharmacy.chemistry.radiolysis (spec 6). Hand values are from mpmath at
30 digits (scratch computation, inputs as written here)."""

import copy
import math
from typing import Any

import pytest

from pharmacy.chemistry import radiolysis as rad
from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError, ModelDomainError
from pharmacy.types import RadiationChemistryDoc


def test_k_dose_direct_hand_value() -> None:
    # G = 1e-7 mol/J, M = 151.16 g/mol: k_D = 1e-7 * 151.16 / 1000 = 1.5116e-8 per Gy.
    assert rad.k_dose_direct(1e-7, 151.16) == pytest.approx(1.5116e-8, rel=1e-15)


def test_k_dose_indirect_hand_value() -> None:
    # G_max = 1.7e-7, c = 0.0879044, c_half = 3e-4: 1.7e-7 / 0.0882044.
    assert rad.k_dose_indirect(1.7e-7, 0.0879044, 0.0003) == pytest.approx(1.92734149316814127e-6, rel=1e-14)
    # c = 0 is the limit G_max / c_half.
    assert rad.k_dose_indirect(1.7e-7, 0.0, 0.0003) == pytest.approx(5.66666666666666667e-4, rel=1e-14)


def test_k_dose_indirect_equals_g_eff_over_c() -> None:
    g_max, c, c_half = 1.7e-7, 0.002, 0.0003
    g_eff = g_max * c / (c + c_half)
    assert rad.k_dose_indirect(g_max, c, c_half) == pytest.approx(g_eff / c, rel=1e-14)


def test_upper_and_central_loss_hand_values() -> None:
    # 1 - exp(-1.5e-6 * 10 * 0.9 Gy)
    assert rad.upper_bound_loss_fraction(1.5e-6, 10.0, 0.9) == pytest.approx(
        1.34999088754100611e-5, rel=1e-13
    )
    # 1 - exp(-0.0046 * 10 * 1 Gy)
    assert rad.upper_bound_loss_fraction(0.0046, 10.0, 1.0) == pytest.approx(
        4.49580378092853647e-2, rel=1e-13
    )
    # 1 - exp(-1.5116e-8 * 0.45 Gy)
    assert rad.central_loss_fraction(1.5116e-8, 0.45) == pytest.approx(6.80219997686503763e-9, rel=1e-13)
    assert rad.central_loss_fraction(0.0, 5.0) == 0.0
    assert rad.upper_bound_loss_fraction(1.5e-6, 10.0, 0.0) == 0.0


@pytest.mark.parametrize(
    ("call", "args"),
    [
        (rad.k_dose_direct, (0.0, 151.16)),
        (rad.k_dose_direct, (-1e-7, 151.16)),
        (rad.k_dose_direct, (1e-7, 0.0)),
        (rad.k_dose_direct, (math.nan, 151.16)),
        (rad.k_dose_direct, (1e-7, math.inf)),
        (rad.k_dose_indirect, (0.0, 0.01, 0.0003)),
        (rad.k_dose_indirect, (1.7e-7, -0.01, 0.0003)),
        (rad.k_dose_indirect, (1.7e-7, 0.01, 0.0)),
        (rad.k_dose_indirect, (1.7e-7, math.nan, 0.0003)),
        (rad.upper_bound_loss_fraction, (-1.0, 10.0, 1.0)),
        (rad.upper_bound_loss_fraction, (1e-6, -10.0, 1.0)),
        (rad.upper_bound_loss_fraction, (1e-6, 10.0, -1.0)),
        (rad.upper_bound_loss_fraction, (1e-6, 10.0, math.inf)),
        (rad.central_loss_fraction, (-1e-6, 1.0)),
        (rad.central_loss_fraction, (1e-6, math.nan)),
        (rad.radiosensitive_dose_exceeded, (True, -1.0, 0.9)),
        (rad.radiosensitive_dose_exceeded, (True, 1.0, math.nan)),
    ],
)
def test_domain_errors(call: Any, args: tuple[Any, ...]) -> None:
    with pytest.raises(ModelDomainError):
        call(*args)


def test_tv_rad_2_solids_negligible_at_one_gray(bundle: ConfigBundle, vector: Any) -> None:
    """TV-RAD-2: 1 - exp(-k_D * 1 Gy) < 1e-4 for every solid product, central (median G)
    and upper-bound (k_upper F) terms. The 99.9th percentile of G is checked too."""
    tv = vector("TV-RAD-2")
    limit = tv["expected"]["max_loss_fraction_at_1_gy"]
    assert limit == 1e-4
    radiation = bundle.radiation
    prior = radiation["direct_effect_g_loss_mol_per_j"]
    g_median = math.exp(prior["mu"])
    g_high = math.exp(prior["mu"] + 3.090232306167813 * prior["sigma"])  # z of 0.999
    safety = bundle.settings["radiation"]["dose_rate_safety_factor"]["value"]
    k_upper = rad.upper_bound_k_per_gy(radiation, "solid")
    solids = [p for p in bundle.formulary["products"] if p["dose_form_family"] == "solid"]
    assert len(solids) == 7
    assert sum(len(p["ingredients"]) for p in solids) == 8
    for product in solids:
        assert rad.upper_bound_loss_fraction(k_upper, safety, 1.0) < limit
        for ingredient in product["ingredients"]:
            mass = ingredient["molar_mass_g_per_mol"]["value"]
            assert rad.central_loss_fraction(rad.k_dose_direct(g_median, mass), 1.0) < limit
            assert rad.central_loss_fraction(rad.k_dose_direct(g_high, mass), 1.0) < limit
            # The vector doses.
            for dose in tv["inputs"]["doses_gy"]:
                assert rad.central_loss_fraction(rad.k_dose_direct(g_median, mass), dose) < limit


def test_upper_bound_k_per_family(bundle: ConfigBundle) -> None:
    radiation = bundle.radiation
    assert rad.upper_bound_k_per_gy(radiation, "solid") == 1.5e-6
    assert rad.upper_bound_k_per_gy(radiation, "semisolid") == 1.5e-6
    assert rad.upper_bound_k_per_gy(radiation, "solution") == 0.0046
    assert rad.upper_bound_k_per_gy(radiation, "suspension") == 0.0046


def test_upper_bound_suspension_falls_back_to_solution(bundle: ConfigBundle) -> None:
    radiation: RadiationChemistryDoc = copy.deepcopy(bundle.radiation)
    radiation["upper_bounds"] = [
        u for u in radiation["upper_bounds"] if u["dose_form_family"] != "suspension"
    ]
    assert rad.upper_bound_k_per_gy(radiation, "suspension") == 0.0046
    radiation["upper_bounds"] = [u for u in radiation["upper_bounds"] if u["dose_form_family"] != "solution"]
    with pytest.raises(ConfigReferenceError):
        rad.upper_bound_k_per_gy(radiation, "suspension")
    with pytest.raises(ConfigReferenceError):
        rad.upper_bound_k_per_gy(radiation, "solution")


def test_radiosensitive_flag(bundle: ConfigBundle) -> None:
    threshold = bundle.settings["radiation"]["radiosensitive_dose_threshold_gy"]["value"]
    assert threshold == 0.9
    assert rad.radiosensitive_dose_exceeded(True, 0.91, threshold)
    assert not rad.radiosensitive_dose_exceeded(True, 0.9, threshold)  # strictly above
    assert not rad.radiosensitive_dose_exceeded(False, 5.0, threshold)


def test_weakest_basis() -> None:
    assert rad.weakest_basis("sourced", "derived") == "derived"
    assert rad.weakest_basis("sourced", "team_default", "derived") == "team_default"
    assert rad.weakest_basis("sourced") == "sourced"
    with pytest.raises(ModelDomainError):
        rad.weakest_basis()


def test_direct_ln_k_dose_hand_value(bundle: ConfigBundle) -> None:
    ln = rad.direct_ln_k_dose(bundle.radiation, 151.16)
    # -16.1181 + ln(0.15116)
    assert ln.mu == pytest.approx(-18.0075164005008488, abs=1e-12)
    assert ln.sigma == 1.15
    assert ln.source_ids == ("MDS63", "MDS73")
    assert ln.basis == "derived"


def test_indirect_ln_k_dose_hand_value(bundle: ConfigBundle) -> None:
    ln = rad.indirect_ln_k_dose(bundle.radiation, 0.0879044)
    # -15.5875 - ln(0.0879044 + 0.0003)
    assert ln.mu == pytest.approx(-13.1594015694076276, abs=1e-12)
    assert ln.sigma == 0.5
    assert ln.source_ids == ("MDS68",)
    assert ln.basis == "team_default"  # c_half is a team default
    # Consistency with the linear-scale function at the prior median.
    assert math.exp(ln.mu) == pytest.approx(
        rad.k_dose_indirect(math.exp(-15.5875), 0.0879044, 0.0003), rel=1e-12
    )


def test_ln_k_dose_requires_lognormal_priors(bundle: ConfigBundle) -> None:
    radiation: Any = copy.deepcopy(bundle.radiation)
    radiation["direct_effect_g_loss_mol_per_j"]["family"] = "normal"
    with pytest.raises(ModelDomainError, match="lognormal"):
        rad.direct_ln_k_dose(radiation, 151.16)
    radiation = copy.deepcopy(bundle.radiation)
    radiation["indirect_effect"]["g_max_mol_per_j"]["family"] = "fixed"
    with pytest.raises(ModelDomainError, match="lognormal"):
        rad.indirect_ln_k_dose(radiation, 0.01)
    radiation = copy.deepcopy(bundle.radiation)
    radiation["indirect_effect"]["half_scavenging_concentration_mol_per_kg"]["value"] = 0.0
    with pytest.raises(ModelDomainError):
        rad.indirect_ln_k_dose(radiation, 0.01)
    with pytest.raises(ModelDomainError):
        rad.direct_ln_k_dose(bundle.radiation, -1.0)
    with pytest.raises(ModelDomainError):
        rad.indirect_ln_k_dose(bundle.radiation, -1.0)


def test_radiolysis_prior_by_family(bundle: ConfigBundle) -> None:
    radiation = bundle.radiation
    solid = rad.radiolysis_prior(radiation, "solid", 151.16, None)
    assert solid.method == "g_value_direct"
    assert solid.ln_k_dose_frozen_per_gy is None
    assert solid.concentration_mol_per_kg is None
    assert solid.half_scavenging_mol_per_kg is None
    assert solid.ln_k_dose_per_gy.mu == pytest.approx(-18.0075164005008488, abs=1e-12)
    # A concentration on a solid is ignored (direct effect).
    assert rad.radiolysis_prior(radiation, "semisolid", 151.16, 0.1).method == "g_value_direct"

    solution = rad.radiolysis_prior(radiation, "solution", 284.4, 0.0879044)
    assert solution.method == "g_value_indirect"
    assert solution.ln_k_dose_per_gy.mu == pytest.approx(-13.1594015694076276, abs=1e-12)
    assert solution.ln_k_dose_frozen_per_gy is not None
    assert solution.ln_k_dose_frozen_per_gy.mu == pytest.approx(-17.3754735810530512, abs=1e-12)
    assert solution.concentration_mol_per_kg == 0.0879044
    assert solution.half_scavenging_mol_per_kg == 0.0003

    suspension = rad.radiolysis_prior(radiation, "suspension", 183.2, 0.000545852)
    assert suspension.method == "g_value_indirect"
    assert suspension.ln_k_dose_per_gy.mu == pytest.approx(-8.51233384543966018, abs=1e-12)

    # A solution without a concentration uses the direct effect throughout (spec 6.3).
    no_c = rad.radiolysis_prior(radiation, "solution", 284.4, None)
    assert no_c.method == "g_value_direct"
    assert no_c.ln_k_dose_frozen_per_gy is None


def test_radiolysis_rejects_nonrepresentable_rates() -> None:
    for g, mass in ((1e308, 1e308), (1e-300, 1e-300)):
        with pytest.raises(ModelDomainError, match="finite"):
            rad.k_dose_direct(g, mass)
    for g, c, half in ((1e308, 0.0, 1e-308), (1.0, 1e308, 1e308), (1e-308, 1e308, 1.0)):
        with pytest.raises(ModelDomainError, match="finite"):
            rad.k_dose_indirect(g, c, half)
    assert rad.upper_bound_loss_fraction(1e308, 1e308, 0.0) == 0.0
    assert rad.upper_bound_loss_fraction(0.0, 1e308, 1e308) == 0.0
    assert rad.upper_bound_loss_fraction(1e308, 0.0, 1e308) == 0.0


def test_log_sensitivity_rejects_nonrepresentable_inputs(bundle: ConfigBundle) -> None:
    radiation = copy.deepcopy(bundle.radiation)
    with pytest.raises(ModelDomainError, match="positive"):
        rad.direct_ln_k_dose(radiation, 5e-324)
    radiation["indirect_effect"]["half_scavenging_concentration_mol_per_kg"]["value"] = 1e308
    with pytest.raises(ModelDomainError, match="finite"):
        rad.indirect_ln_k_dose(radiation, 1e308)
    radiation["direct_effect_g_loss_mol_per_j"]["mu"] = math.inf
    with pytest.raises(ModelDomainError, match="finite"):
        rad.direct_ln_k_dose(radiation, 100.0)
    radiation["direct_effect_g_loss_mol_per_j"]["mu"] = -10.0
    radiation["direct_effect_g_loss_mol_per_j"]["sigma"] = 0.0
    with pytest.raises(ModelDomainError, match="positive"):
        rad.direct_ln_k_dose(radiation, 100.0)
