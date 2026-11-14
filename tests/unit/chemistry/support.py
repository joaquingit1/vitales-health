"""Shared helpers of the chemistry unit tests (B2): type aliases, small builders and the
expected-pathway table of the formulary."""

import copy
import dataclasses
import math
from collections.abc import Callable, Mapping
from typing import Any

from scipy.integrate import quad

from pharmacy import microclimate
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, SECONDS_PER_DAY
from pharmacy.types import Anchor, DoseFormFamily, ProductDoc, ReactionClassDoc

type Mutation = Callable[[dict[str, Any]], None]
type BundleMutator = Callable[[Mapping[str, Mutation]], ConfigBundle]


def anchor_quadrature(
    bundle: ConfigBundle, anchor: Anchor, ea: float, ea_sd: float, b: float, b_sd: float
) -> tuple[float, float]:
    """Independent mean-rate quadrature and spec variance for a configured anchor.

    Share package time constants with the engine, but integrate the instantaneous
    exponential directly instead of using its humidity integral or conversion helper.
    """
    temperature = anchor["temperature_k"]["value"]
    outside = anchor["rh_percent"]["value"]
    reference = bundle.settings["reference_conditions"]
    t_ref = reference["temperature_k"]["value"]
    rh_ref = reference["relative_humidity_percent"]["value"]
    duration = 1.0
    start = outside
    tau = 0.0
    if anchor["package_id"] is not None:
        package = next(p for p in bundle.packages["packages"] if p["package_id"] == anchor["package_id"])
        assert anchor["duration_days"] is not None
        duration = anchor["duration_days"]["value"] * SECONDS_PER_DAY
        tau = microclimate.exchange_time_constant_s(package, temperature)
        moisture = package["moisture_model"]
        initial = moisture["initial_internal_rh_percent"]
        sealed = moisture["sealed_rh_percent"]
        start = outside if initial is None else initial["value"]
        if moisture["kind"] == "sealed_constant":
            assert sealed is not None
            start = sealed["value"]

    def rh(fraction: float) -> float:
        if tau == 0.0:
            return outside
        if math.isinf(tau):
            return start
        return outside + (start - outside) * math.exp(-fraction * duration / tau)

    humidity, _ = quad(lambda f: math.exp(b * (rh(f) - rh_ref)), 0.0, 1.0, epsabs=1e-12, epsrel=1e-12)
    d_rh = (
        quad(
            lambda f: (rh_ref - rh(f)) * math.exp(b * (rh(f) - rh_ref)),
            0.0,
            1.0,
            epsabs=1e-12,
            epsrel=1e-12,
        )[0]
        / humidity
    )
    zone = next(z for z in bundle.zones["zones"] if z["zone_id"] == anchor["storage_zone_id"])
    multiplier = zone["rate_multiplier"]
    zone_mean = math.log(multiplier["mu"]) if multiplier["family"] == "fixed" else multiplier["mu"]
    zone_sd = 0.0 if multiplier["family"] == "fixed" else multiplier["sigma"]
    x = (1.0 / t_ref - 1.0 / temperature) / GAS_CONSTANT_J_PER_MOL_K
    mean = anchor["ln_k_per_s"]["mu"] - ea * x - math.log(humidity) - zone_mean
    sd = math.hypot(
        anchor["ln_k_per_s"]["sigma"],
        x * ea_sd,
        d_rh * b_sd,
        ea * anchor["temperature_sd_k"]["value"] / (GAS_CONSTANT_J_PER_MOL_K * temperature**2),
        zone_sd,
    )
    return mean, sd


def anchor_posterior(
    bundle: ConfigBundle, class_id: str, family: DoseFormFamily, count: int
) -> tuple[float, float, float]:
    """Independent same-molecule anchor posterior and class shared weight."""
    cls = reaction_class(bundle, class_id)
    anchor = next(a for a in cls["anchors"] if a["dose_form_family"] == family)
    priors = next(p for p in cls["priors"] if p["dose_form_family"] == family)
    ea, b = priors["ea_j_per_mol"], priors["b_per_percent_rh"]
    assert ea is not None
    assert b is not None
    mu_a, sd_a = anchor_quadrature(bundle, anchor, ea["mu"], ea["sigma"], b["mu"], b["sigma"])
    total = next(p for p in bundle.reaction_classes["total_rate_priors"] if p["dose_form_family"] == family)
    mu_p = total["ln_k_ref_per_s"]["mu"] - math.log(count)
    var_p = total["ln_k_ref_per_s"]["sigma"] ** 2
    method_sd = bundle.settings["computed_layer"]["same_molecule_sd_log10"]["value"] * math.log(10.0)
    var_c = sd_a**2 + method_sd**2
    variance = 1.0 / (1.0 / var_p + 1.0 / var_c)
    return variance * (mu_p / var_p + mu_a / var_c), math.sqrt(variance), variance / var_p


def product(bundle: ConfigBundle, product_id: str) -> ProductDoc:
    """The formulary product with this id."""
    return next(p for p in bundle.formulary["products"] if p["product_id"] == product_id)


def reaction_class(bundle: ConfigBundle, class_id: str) -> ReactionClassDoc:
    """The reaction class with this id."""
    return next(c for c in bundle.reaction_classes["classes"] if c["class_id"] == class_id)


def find(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    """The first JSON object in ``items`` whose ``key`` equals ``value`` (for mutations)."""
    return next(item for item in items if item[key] == value)


def unvalidated(bundle: ConfigBundle, member: str, mutation: Mutation) -> ConfigBundle:
    """A bundle whose ``member`` document is edited WITHOUT schema validation. Only for
    reaching the engine's defensive checks against data the schema already rejects."""
    data = copy.deepcopy(bundle.documents[member].data)
    mutation(data)
    documents = dict(bundle.documents)
    documents[member] = dataclasses.replace(bundle.documents[member], data=data)
    return ConfigBundle(documents=documents)


def sourced(value: float, unit: str = "1") -> dict[str, Any]:
    """A schema-valid team-default SourcedValue for test bundles."""
    return {
        "value": value,
        "unit": unit,
        "basis": "team_default",
        "source_ids": [],
        "to_be_set_by": "test",
        "note": "",
    }


type Table = tuple[tuple[str, tuple[int, ...]], ...]

# Expected pathways per (product, ingredient), in reaction-class order, with site atoms
# (input SMILES atom indices). Verified by hand against each structure: see the comments.
EXPECTED: dict[tuple[str, str], Table] = {
    # Anilide C(=O)N (atom 1) and the para phenol O (atom 10).
    ("acetaminophen_325_tab", "acetaminophen"): (
        ("amide_hydrolysis", (1,)),
        ("phenol_autoxidation", (10,)),
        ("radiolysis", ()),
    ),
    # Aryl acetate ester carbonyl (atom 1); the carboxylic acid is not an ester.
    ("aspirin_325_tab", "aspirin"): (("ester_hydrolysis", (1,)), ("radiolysis", ())),
    # No class matches: the fallback thermal class keeps a destructive thermal pathway.
    ("ibuprofen_400_tab", "ibuprofen"): (("radiolysis", ()), ("unclassified_thermal", ())),
    # Side-chain amide (10), beta-lactam (7), phenol (19), alpha C-H of the primary amine
    # (12), thiazolidine sulfur (5).
    ("amoxicillin_clavulanate_875_125_tab", "amoxicillin"): (
        ("amide_hydrolysis", (10,)),
        ("beta_lactam_hydrolysis", (7,)),
        ("phenol_autoxidation", (19,)),
        ("amine_alpha_oxidation", (12,)),
        ("thioether_s_oxidation", (5,)),
        ("radiolysis", ()),
    ),
    # The beta-lactam (3) only: the four-membered ring is excluded from amide_hydrolysis
    # and the free carboxylic acid is not an ester.
    ("amoxicillin_clavulanate_875_125_tab", "clavulanic_acid"): (
        ("beta_lactam_hydrolysis", (3,)),
        ("radiolysis", ()),
    ),
    # Alpha C-H of the tertiary amine (1, 18, 19), phenothiazine sulfur (10) and the
    # conjugated ring-fusion atoms (4, 9, 11, 16); the bottle transmits light.
    ("promethazine_25_tab", "promethazine"): (
        ("amine_alpha_oxidation", (1, 18, 19)),
        ("thioether_s_oxidation", (10,)),
        ("photolysis_conjugated", (4, 9, 11, 16)),
        ("radiolysis", ()),
    ),
    # Same molecule in an opaque vial: no photolysis.
    ("promethazine_25mg_ml_inj", "promethazine"): (
        ("amine_alpha_oxidation", (1, 18, 19)),
        ("thioether_s_oxidation", (10,)),
        ("radiolysis", ()),
    ),
    # Both catechol oxygens (10, 11) go to catechol_autoxidation, not phenol_autoxidation
    # (larger pattern wins); N-methyl and N-CH2 alpha C-H (0, 2); the defined benzylic
    # stereocenter (3) racemizes in solution.
    ("epinephrine_0_3mg_autoinjector", "epinephrine"): (
        ("catechol_autoxidation", (10, 11)),
        ("amine_alpha_oxidation", (0, 2)),
        ("racemization_benzylic_alcohol", (3,)),
        ("radiolysis", ()),
    ),
    ("epinephrine_0_1mg_ml_syringe", "epinephrine"): (
        ("catechol_autoxidation", (10, 11)),
        ("amine_alpha_oxidation", (0, 2)),
        ("racemization_benzylic_alcohol", (3,)),
        ("radiolysis", ()),
    ),
    # The gamma-lactone carbonyl (5) and the enediol oxygens (8, 9).
    ("ascorbic_acid_500_tab", "ascorbic_acid"): (
        ("ester_hydrolysis", (5,)),
        ("enediol_oxidation", (8, 9)),
        ("radiolysis", ()),
    ),
    # Alpha C-H of the piperazine N-methyl amine (17, 19, 21); the aryl N is excluded. The
    # quinolone matches photolysis, but the blister transmits no light.
    ("levofloxacin_500_tab", "levofloxacin"): (
        ("amine_alpha_oxidation", (17, 19, 21)),
        ("radiolysis", ()),
    ),
}
