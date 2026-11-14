"""Radiolysis: G-value conversion to first-order-in-dose constants and the upper bound.

Owner: B2. Contract: docs/ENGINE-SPEC.md section 6.

Absorbed dose D in Gy (J/kg). A radiolytic yield G in mol/J destroys G D mol of drug per
kg of material; divided by the drug content (mol/kg) it is the fraction lost per Gy:

* solid (direct effect): drug content 1000/M mol per kg of drug, so k_D = G M / 1000;
* dilute aqueous solution (indirect effect): content c mol/kg and G_eff = G_max c/(c + c_half),
  so k_D = G_max / (c + c_half).

All functions are deterministic and raise ``ModelDomainError`` for inputs outside their
domain instead of returning NaN or infinity.
"""

import dataclasses
import math
from typing import Literal

from pharmacy.constants import GRAMS_PER_KILOGRAM
from pharmacy.errors import ConfigReferenceError, ModelDomainError
from pharmacy.types import Basis, DoseFormFamily, Prior, RadiationChemistryDoc

RadiolysisMethod = Literal["g_value_direct", "g_value_indirect"]


def _require_positive_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ModelDomainError(f"{name} must be positive and finite, got {value!r}")


def _require_non_negative_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ModelDomainError(f"{name} must be non-negative and finite, got {value!r}")


def k_dose_direct(g_value_mol_per_j: float, molar_mass_g_per_mol: float) -> float:
    """Direct effect in a solid (or frozen solution): k_D = G M / 1000 per Gy (fraction of
    drug lost per Gy), G in mol/J, M in g/mol, 1 Gy = 1 J/kg (spec 6.2). Vector TV-RAD-2."""
    _require_positive_finite("G", g_value_mol_per_j)
    _require_positive_finite("molar mass", molar_mass_g_per_mol)
    result = g_value_mol_per_j * molar_mass_g_per_mol / GRAMS_PER_KILOGRAM
    _require_positive_finite("direct sensitivity", result)
    return result


def k_dose_indirect(
    g_max_mol_per_j: float, concentration_mol_per_kg: float, half_scavenging_mol_per_kg: float
) -> float:
    """Indirect effect in dilute aqueous solution: G_eff = G_max c / (c + c_half),
    k_D = G_eff / c per Gy (spec 6.3).

    Evaluated as G_max / (c + c_half), which equals G_eff / c for c > 0 and is its limit at
    c = 0."""
    _require_positive_finite("G_max", g_max_mol_per_j)
    _require_non_negative_finite("concentration", concentration_mol_per_kg)
    _require_positive_finite("half-scavenging concentration", half_scavenging_mol_per_kg)
    denominator = concentration_mol_per_kg + half_scavenging_mol_per_kg
    _require_positive_finite("total scavenging concentration", denominator)
    result = g_max_mol_per_j / denominator
    _require_positive_finite("indirect sensitivity", result)
    return result


def upper_bound_loss_fraction(k_upper_per_gy: float, dose_rate_safety_factor: float, dose_gy: float) -> float:
    """1 - exp(-k_upper F D) (spec 6.4), evaluated as -expm1(-k_upper F D)."""
    _require_non_negative_finite("k_upper", k_upper_per_gy)
    _require_non_negative_finite("dose-rate safety factor", dose_rate_safety_factor)
    _require_non_negative_finite("dose", dose_gy)
    if k_upper_per_gy == 0.0 or dose_rate_safety_factor == 0.0 or dose_gy == 0.0:
        return 0.0
    return -math.expm1(-k_upper_per_gy * dose_rate_safety_factor * dose_gy)


def central_loss_fraction(k_dose_per_gy: float, dose_gy: float) -> float:
    """Loss at one constant k_D: 1 - exp(-k_D D) (spec 6.3), as -expm1(-k_D D)."""
    _require_non_negative_finite("k_D", k_dose_per_gy)
    _require_non_negative_finite("dose", dose_gy)
    return -math.expm1(-k_dose_per_gy * dose_gy)


def upper_bound_k_per_gy(radiation: RadiationChemistryDoc, family: DoseFormFamily) -> float:
    """The configured upper-bound sensitivity for a dose-form family (suspension uses the
    solution value when it has none of its own). Raises ``ConfigReferenceError`` if
    missing."""
    by_family = {entry["dose_form_family"]: entry["k_per_gy"]["value"] for entry in radiation["upper_bounds"]}
    if family in by_family:
        return by_family[family]
    if family == "suspension" and "solution" in by_family:
        return by_family["solution"]
    raise ConfigReferenceError("radiation-chemistry", [f"upper_bounds has no entry for {family!r}"])


def radiosensitive_dose_exceeded(
    radiosensitive: bool, cumulative_dose_gy: float, threshold_gy: float
) -> bool:
    """Flag ``radiosensitive_dose_exceeded`` (spec 6.4): a product marked radiosensitive whose
    cumulative absorbed dose is above the threshold (strictly)."""
    _require_non_negative_finite("cumulative dose", cumulative_dose_gy)
    _require_non_negative_finite("threshold", threshold_gy)
    return radiosensitive and cumulative_dose_gy > threshold_gy


# =========================================================================================
# ln k_dose priors for the radiolysis pathway (spec 6.2, 6.3 and 9.1)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class LnNormal:
    """A normal distribution of a natural logarithm (mean and standard deviation in ln
    units) with the provenance of the prior it came from."""

    mu: float
    sigma: float
    source_ids: tuple[str, ...]
    basis: Basis
    note: str


@dataclasses.dataclass(frozen=True, slots=True)
class RadiolysisPrior:
    """ln k_dose of one ingredient: the in-use constant and, for a solution or suspension
    with a concentration, the direct-effect constant used in frozen segments (spec 6.3)."""

    method: RadiolysisMethod
    ln_k_dose_per_gy: LnNormal
    ln_k_dose_frozen_per_gy: LnNormal | None
    molar_mass_g_per_mol: float
    concentration_mol_per_kg: float | None
    half_scavenging_mol_per_kg: float | None


def _lognormal(prior: Prior, label: str) -> tuple[float, float]:
    if prior["family"] != "lognormal":
        raise ModelDomainError(f"{label} must be a lognormal prior, got {prior['family']!r}")
    if not math.isfinite(prior["mu"]):
        raise ModelDomainError(f"{label} mean must be finite")
    _require_positive_finite(f"{label} sigma", prior["sigma"])
    return prior["mu"], prior["sigma"]


def _merge_sources(*groups: list[str]) -> tuple[str, ...]:
    return tuple(sorted({sid for group in groups for sid in group}))


_BASIS_STRENGTH: dict[Basis, int] = {"sourced": 3, "derived": 2, "team_default": 1}


def weakest_basis(*bases: Basis) -> Basis:
    """The weakest provenance basis among ``bases`` (sourced > derived > team_default)."""
    if not bases:
        raise ModelDomainError("weakest_basis needs at least one basis")
    return min(bases, key=lambda basis: _BASIS_STRENGTH[basis])


def direct_ln_k_dose(radiation: RadiationChemistryDoc, molar_mass_g_per_mol: float) -> LnNormal:
    """ln k_D = ln G + ln(M / 1000) with G lognormal (spec 6.2): normal with mean
    mu_G + ln(M/1000) and the prior's sigma."""
    _require_positive_finite("molar mass", molar_mass_g_per_mol)
    prior = radiation["direct_effect_g_loss_mol_per_j"]
    mu, sigma = _lognormal(prior, "direct_effect_g_loss_mol_per_j")
    mass_kg = molar_mass_g_per_mol / GRAMS_PER_KILOGRAM
    _require_positive_finite("molar mass in kg/mol", mass_kg)
    return LnNormal(
        mu=mu + math.log(mass_kg),
        sigma=sigma,
        source_ids=_merge_sources(prior["source_ids"]),
        basis=prior["basis"],
        note=f"direct effect: ln G + ln(M/1000), M = {molar_mass_g_per_mol} g/mol",
    )


def indirect_ln_k_dose(radiation: RadiationChemistryDoc, concentration_mol_per_kg: float) -> LnNormal:
    """ln k_D = ln G_max - ln(c + c_half) with G_max lognormal (spec 6.3)."""
    _require_non_negative_finite("concentration", concentration_mol_per_kg)
    indirect = radiation["indirect_effect"]
    prior = indirect["g_max_mol_per_j"]
    mu, sigma = _lognormal(prior, "indirect_effect.g_max_mol_per_j")
    half = indirect["half_scavenging_concentration_mol_per_kg"]
    _require_positive_finite("half-scavenging concentration", half["value"])
    denominator = concentration_mol_per_kg + half["value"]
    _require_positive_finite("total scavenging concentration", denominator)
    return LnNormal(
        mu=mu - math.log(denominator),
        sigma=sigma,
        source_ids=_merge_sources(prior["source_ids"], half["source_ids"]),
        basis=weakest_basis(prior["basis"], half["basis"]),
        note=f"indirect effect: ln G_max - ln(c + c_half), c = {concentration_mol_per_kg} mol/kg",
    )


def radiolysis_prior(
    radiation: RadiationChemistryDoc,
    family: DoseFormFamily,
    molar_mass_g_per_mol: float,
    concentration_mol_per_kg: float | None,
) -> RadiolysisPrior:
    """The radiolysis parameters of one ingredient (spec 6.2, 6.3):

    * solid and semisolid: direct effect, no frozen variant;
    * solution and suspension with a concentration: indirect effect in use and the direct
      effect in frozen segments (``frozen_solution_uses_direct_effect``);
    * solution and suspension without a concentration: direct effect throughout."""
    direct = direct_ln_k_dose(radiation, molar_mass_g_per_mol)
    if family in ("solution", "suspension") and concentration_mol_per_kg is not None:
        indirect = indirect_ln_k_dose(radiation, concentration_mol_per_kg)
        return RadiolysisPrior(
            method="g_value_indirect",
            ln_k_dose_per_gy=indirect,
            ln_k_dose_frozen_per_gy=direct,
            molar_mass_g_per_mol=molar_mass_g_per_mol,
            concentration_mol_per_kg=concentration_mol_per_kg,
            half_scavenging_mol_per_kg=radiation["indirect_effect"][
                "half_scavenging_concentration_mol_per_kg"
            ]["value"],
        )
    return RadiolysisPrior(
        method="g_value_direct",
        ln_k_dose_per_gy=direct,
        ln_k_dose_frozen_per_gy=None,
        molar_mass_g_per_mol=molar_mass_g_per_mol,
        concentration_mol_per_kg=None,
        half_scavenging_mol_per_kg=None,
    )
