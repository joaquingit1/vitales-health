"""Unit tests of pharmacy.chemistry.pathways (spec 7.2), with the expected-pathway table of
every formulary product."""

import copy
from typing import Any

import pytest

from pharmacy.chemistry import pathways as pw
from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError
from pharmacy.types import ActiveIngredientDoc, PackageDoc, ProductDoc, ReactionClassesDoc
from tests.unit.chemistry.support import EXPECTED, BundleMutator, Table, product


def _packages(bundle: ConfigBundle, ids: list[str]) -> tuple[PackageDoc, ...]:
    by_id = {p["package_id"]: p for p in bundle.packages["packages"]}
    return tuple(by_id[i] for i in ids)


def _table(matches: tuple[pw.PathwayMatch, ...]) -> Table:
    return tuple((m.reaction_class["class_id"], tuple(s.site_atom for s in m.sites)) for m in matches)


def _ingredient(prod: ProductDoc, ingredient_id: str) -> ActiveIngredientDoc:
    return next(i for i in prod["ingredients"] if i["ingredient_id"] == ingredient_id)


def _lot_packages(bundle: ConfigBundle, product_id: str) -> list[str]:
    ids = {
        entry["package_id"]
        for lot in bundle.lots["lots"]
        if lot["product_id"] == product_id
        for entry in lot["package_history"]
    }
    return sorted(ids)


def test_table_covers_every_formulary_ingredient(bundle: ConfigBundle) -> None:
    keys = {
        (p["product_id"], i["ingredient_id"]) for p in bundle.formulary["products"] for i in p["ingredients"]
    }
    assert keys == set(EXPECTED)
    assert len({k[0] for k in keys}) == 10


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_expected_pathways_default_package(bundle: ConfigBundle, key: tuple[str, str]) -> None:
    prod = product(bundle, key[0])
    matches = pw.identify_pathways(
        prod,
        _ingredient(prod, key[1]),
        bundle.reaction_classes,
        _packages(bundle, [prod["default_package_id"]]),
    )
    assert _table(matches) == EXPECTED[key]
    for match in matches:
        assert match.pathway_id == f"{key[1]}:{match.reaction_class['class_id']}"
        assert match.ingredient_id == key[1]
        assert all(site.class_id == match.reaction_class["class_id"] for site in match.sites)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_expected_pathways_lot_package_history(bundle: ConfigBundle, key: tuple[str, str]) -> None:
    """Every package in the configured lot histories (the repackaged bag transmits light, but
    no product besides promethazine tablets has a photolysis site)."""
    prod = product(bundle, key[0])
    ids = _lot_packages(bundle, key[0])
    assert ids
    matches = pw.identify_pathways(
        prod, _ingredient(prod, key[1]), bundle.reaction_classes, _packages(bundle, ids)
    )
    assert _table(matches) == EXPECTED[key]


def test_levofloxacin_gets_photolysis_in_a_transmitting_package(bundle: ConfigBundle) -> None:
    prod = product(bundle, "levofloxacin_500_tab")
    matches = pw.identify_pathways(
        prod,
        _ingredient(prod, "levofloxacin"),
        bundle.reaction_classes,
        _packages(bundle, ["repackaged_bag"]),
    )
    assert ("photolysis_conjugated", (4, 6, 11)) in _table(matches)


def test_photostable_product_has_no_photolysis(bundle: ConfigBundle) -> None:
    prod: ProductDoc = copy.deepcopy(product(bundle, "promethazine_25_tab"))
    prod["photostable_per_q1b"] = True
    matches = pw.identify_pathways(
        prod,
        _ingredient(prod, "promethazine"),
        bundle.reaction_classes,
        _packages(bundle, ["repackaged_bag"]),
    )
    assert "photolysis_conjugated" not in {m.reaction_class["class_id"] for m in matches}
    prod["photostable_per_q1b"] = False
    matches = pw.identify_pathways(
        prod,
        _ingredient(prod, "promethazine"),
        bundle.reaction_classes,
        _packages(bundle, ["repackaged_bag"]),
    )
    assert "photolysis_conjugated" in {m.reaction_class["class_id"] for m in matches}


def test_no_packages_means_no_photolysis(bundle: ConfigBundle) -> None:
    prod = product(bundle, "promethazine_25_tab")
    matches = pw.identify_pathways(prod, _ingredient(prod, "promethazine"), bundle.reaction_classes, ())
    assert "photolysis_conjugated" not in {m.reaction_class["class_id"] for m in matches}


def test_package_transmits_light(bundle: ConfigBundle) -> None:
    by_id = {p["package_id"]: pw.package_transmits_light(p) for p in bundle.packages["packages"]}
    assert by_id == {
        "original_blister": False,
        "original_bottle": True,
        "repackaged_bag": True,
        "original_vial": False,
        "auto_injector": True,
        "prefilled_syringe": False,
    }
    uv_only: Any = copy.deepcopy(bundle.packages["packages"][0])
    uv_only["light_transmission_uv"]["value"] = 0.1
    assert pw.package_transmits_light(uv_only)


def test_racemization_needs_block_stereocenter_and_family(bundle: ConfigBundle) -> None:
    prod: ProductDoc = copy.deepcopy(product(bundle, "epinephrine_0_1mg_ml_syringe"))
    ingredient = copy.deepcopy(_ingredient(prod, "epinephrine"))
    packages = _packages(bundle, ["prefilled_syringe"])

    def classes() -> set[str]:
        return {
            m.reaction_class["class_id"]
            for m in pw.identify_pathways(prod, ingredient, bundle.reaction_classes, packages)
        }

    assert "racemization_benzylic_alcohol" in classes()
    ingredient["racemization"] = None
    assert "racemization_benzylic_alcohol" not in classes()
    ingredient = copy.deepcopy(_ingredient(prod, "epinephrine"))
    ingredient["smiles"] = "CNCC(C1=CC(=C(C=C1)O)O)O"  # racemic: no defined stereocenter
    assert "racemization_benzylic_alcohol" not in classes()
    ingredient = copy.deepcopy(_ingredient(prod, "epinephrine"))
    prod["dose_form_family"] = "solid"  # racemization applies to solutions and suspensions
    assert "racemization_benzylic_alcohol" not in classes()


def test_family_filter_happens_before_site_collapse(bundle: ConfigBundle) -> None:
    """A class that does not apply to the product never takes a site from one that does."""
    classes: ReactionClassesDoc = copy.deepcopy(bundle.reaction_classes)
    for cls in classes["classes"]:
        if cls["class_id"] == "catechol_autoxidation":
            cls["applies_to_families"] = ["solid"]
    prod = product(bundle, "epinephrine_0_1mg_ml_syringe")
    table = _table(
        pw.identify_pathways(
            prod, _ingredient(prod, "epinephrine"), classes, _packages(bundle, ["prefilled_syringe"])
        )
    )
    assert ("phenol_autoxidation", (10, 11)) in table
    assert "catechol_autoxidation" not in {c for c, _ in table}


def test_shared_site_tie_goes_to_config_order(bundle: ConfigBundle) -> None:
    classes: ReactionClassesDoc = copy.deepcopy(bundle.reaction_classes)
    duplicate = copy.deepcopy(next(c for c in classes["classes"] if c["class_id"] == "phenol_autoxidation"))
    duplicate["class_id"] = "phenol_autoxidation_copy"
    classes["classes"].append(duplicate)
    prod = product(bundle, "acetaminophen_325_tab")
    table = _table(pw.identify_pathways(prod, _ingredient(prod, "acetaminophen"), classes, ()))
    assert ("phenol_autoxidation", (10,)) in table
    assert "phenol_autoxidation_copy" not in {c for c, _ in table}


def test_different_mechanisms_do_not_share_sites(bundle: ConfigBundle) -> None:
    """Promethazine C-H next to N and the sulfur are different mechanisms; both stay."""
    prod = product(bundle, "promethazine_25mg_ml_inj")
    table = dict(
        _table(pw.identify_pathways(prod, _ingredient(prod, "promethazine"), bundle.reaction_classes, ()))
    )
    assert table["amine_alpha_oxidation"] == (1, 18, 19)
    assert table["thioether_s_oxidation"] == (10,)


def test_missing_fallback_class_raises(bundle: ConfigBundle) -> None:
    classes: ReactionClassesDoc = copy.deepcopy(bundle.reaction_classes)
    classes["classes"] = [c for c in classes["classes"] if c["class_id"] != "unclassified_thermal"]
    prod = product(bundle, "ibuprofen_400_tab")
    with pytest.raises(ConfigReferenceError, match="fallback"):
        pw.identify_pathways(prod, _ingredient(prod, "ibuprofen"), classes, ())
    # Not needed when a thermal destructive class matches.
    prod_a = product(bundle, "aspirin_325_tab")
    assert pw.identify_pathways(prod_a, _ingredient(prod_a, "aspirin"), classes, ())


def test_mutated_bundle_validates(mutate_bundle: BundleMutator) -> None:
    def no_light(doc: dict[str, Any]) -> None:
        for package in doc["packages"]:
            package["light_transmission_visible"]["value"] = 0.0
            package["light_transmission_uv"]["value"] = 0.0

    edited = mutate_bundle({"packages": no_light})
    prod = product(edited, "promethazine_25_tab")
    matches = pw.identify_pathways(
        prod,
        _ingredient(prod, "promethazine"),
        edited.reaction_classes,
        _packages(edited, ["original_bottle"]),
    )
    assert "photolysis_conjugated" not in {m.reaction_class["class_id"] for m in matches}


def test_pathway_id() -> None:
    assert pw.pathway_id("aspirin", "ester_hydrolysis") == "aspirin:ester_hydrolysis"
