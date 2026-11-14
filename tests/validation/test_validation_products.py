"""Fast checks of the validation inputs built in tests (no engine pipeline): the PubChem data
file, the products, lots and declared zone, the test bundle, and the pass-rule arithmetic."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from rdkit import Chem
from rdkit.Chem import Descriptors

from pharmacy.config import load_bundle
from tests.scenarios.bundles import write_test_bundle
from tests.validation import rules
from tests.validation.products import (
    DECLARED_STATION_ZONE_ID,
    PUBCHEM_PATH,
    V1_FLIGHT_ZONE_ID,
    declared_station_zone,
    dose_form_of,
    ground_zone,
    package_for,
    pubchem_entries,
    v1_cases,
    v6_cases,
    validation_zones,
)


def test_pubchem_file_records_cid_date_and_row() -> None:
    data = json.loads(PUBCHEM_PATH.read_text(encoding="utf-8"))
    assert data["retrieved_utc"].endswith("Z")
    assert data["source_ids"] == ["MDX01"]
    for entry in data["entries"]:
        assert isinstance(entry["pubchem_cid"], int)
        assert entry["pubchem_cid"] > 0
        assert isinstance(entry["row_number"], int)
        assert entry["request_url"].startswith("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/")


@pytest.mark.parametrize("key", sorted(pubchem_entries()), ids=lambda k: f"{k[0]}-row-{k[1]}")
def test_pubchem_smiles_parse_and_match_the_molecular_weight(key: tuple[str, int]) -> None:
    entry = pubchem_entries()[key]
    mol = Chem.MolFromSmiles(entry["smiles"])
    assert mol is not None, entry["smiles"]
    weight = float(Descriptors.MolWt(mol))  # type: ignore[attr-defined]
    assert abs(weight - entry["molecular_weight_g_per_mol"]) < 0.1  # PubChem rounds to 0.1 or 0.01


def test_every_validation_row_has_a_structure() -> None:
    entries = pubchem_entries()
    assert {n for (d, n) in entries if d == "V1"} == set(range(1, 37))
    assert {n for (d, n) in entries if d == "V6"} == set(range(2, 10))


@pytest.mark.parametrize(
    ("label", "family"),
    [
        ("Acyclovir tablet", "solid"),
        ("Triamcinolone cream", "semisolid"),
        ("Mupirocin ointment", "semisolid"),
        ("Lidocaine injection", "solution"),
        ("Ciprofloxacin ophthalmic solution", "solution"),
        ("Promethazine suppository", "semisolid"),
        ("Cilastatin (imipenem/cilastatin powder)", "solid"),
        ("Ethinyl estradiol (oral contraceptive)", "solid"),
        ("Temazepam capsule", "solid"),
    ],
)
def test_dose_form_family_from_the_row(label: str, family: str) -> None:
    assert dose_form_of(label)[1] == family


def test_package_rule() -> None:
    assert package_for(True, "solid") == "repackaged_bag"
    assert package_for(False, "solid") == "original_bottle"
    assert package_for(None, "solid") == "original_bottle"
    assert package_for(False, "semisolid") == "original_vial"
    assert package_for(False, "solution") == "original_vial"


def test_v1_selection_and_exclusions() -> None:
    validated = [c for c in v1_cases("control") if c.excluded_reason is None]
    assert [c.row_number for c in validated] == [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 26, 28, 32, 34, 36]
    every = v1_cases("flight", all_rows=True)
    assert len(every) == 36
    assert {c.row_number for c in every if c.excluded_reason and "solution" in c.excluded_reason} == {22, 30}
    clavulanate = next(c for c in validated if c.row_number == 12)
    assert clavulanate.lot["initial_assay"][0]["mean_percent"]["value"] == 93.3
    assert clavulanate.lot["package_history"][0]["package_id"] == "repackaged_bag"
    assert clavulanate.measured_percent[-1] == 3.3
    assert all(c.lot["custody"][0]["zone_id"] == V1_FLIGHT_ZONE_ID for c in every)


def test_v6_selection() -> None:
    cases = v6_cases()
    assert [c.label for c in cases] == [
        "Acetaminophen",
        "Ibuprofen",
        "Loratadine",
        "Loperamide",
        "Pseudoephedrine ER",
        "Melatonin",
        "Zolpidem",
        "Modafinil",
    ]
    assert all(c.lot["initial_assay"][0]["mean_percent"]["value"] == 100.0 for c in cases)
    assert all(c.measurement_days == (550,) for c in cases)


def test_declared_station_zone_uses_the_profile_setpoints() -> None:
    bundle = load_bundle()
    zone = declared_station_zone(bundle)
    declared = zone["declared_conditions"]
    assert declared is not None
    assert zone["kind"] == "declared"
    assert zone["sensors"] == []
    t_prior, rh_prior = declared["temperature_k"], declared["relative_humidity_percent"]
    assert t_prior is not None
    assert rh_prior is not None
    profile = next(p for p in bundle.environment_profiles["profiles"] if p["profile_id"] == "station")
    assert t_prior["mu"] == profile["temperature"]["setpoint_k"]["value"]
    assert rh_prior["mu"] == profile["relative_humidity"]["setpoint_percent"]["value"]


def test_the_validation_bundle_loads(tmp_path: Path) -> None:
    control = v1_cases("control", all_rows=True)
    zones = validation_zones(load_bundle())
    flight = v1_cases("flight", all_rows=True)
    v6 = v6_cases()
    products = {c.product["product_id"]: c.product for c in [*control, *v6]}
    bundle = write_test_bundle(
        tmp_path,
        products=list(products.values()),
        extra_lots=[c.lot for c in [*control, *flight, *v6]],
        zones=zones,
        zone_overrides=[declared_station_zone()],
    )
    ids = {p["product_id"] for p in bundle.formulary["products"]}
    assert set(products) <= ids
    assert DECLARED_STATION_ZONE_ID in {z["zone_id"] for z in bundle.zones["zones"]}


@pytest.mark.parametrize(("n", "k_min"), [(16, 12), (8, 6), (1, 0)])
def test_binomial_rule(n: int, k_min: int) -> None:
    assert rules.minimum_covered(n) == k_min


def test_binomial_rule_needs_rows() -> None:
    with pytest.raises(ValueError, match="positive"):
        rules.minimum_covered(0)


def test_v6_every_row_repackaged() -> None:
    for case in v6_cases():
        assert case.product["default_package_id"] == "repackaged_bag"
        assert case.lot["package_history"][0]["package_id"] == "repackaged_bag"


def test_ground_zone_matches_each_prior_at_test_time() -> None:
    for prior in load_bundle().reaction_classes["total_rate_priors"]:
        declared = ground_zone(prior)["declared_conditions"]
        assert declared is not None
        for name, source, sd in (
            ("temperature_k", "temperature_k", "temperature_sd_k"),
            ("relative_humidity_percent", "rh_percent", "rh_sd_percent"),
        ):
            condition = cast(Any, declared)[name]
            assert condition["mu"] == cast(Any, prior)[source]["value"]
            assert condition["sigma"] == cast(Any, prior)[sd]["value"]


def test_v2_own_usp_limits_and_one_count_per_formulation() -> None:
    failed = rules.failed_formulations({1: 97.0, 2: 97.0, 3: 89.0}, {1: 95.0, 2: 98.0, 3: 90.0}, {2: 1})
    assert failed == {1: True, 3: True}
    assert sum(failed.values()) == 2
    assert rules.failed_formulations({1: 95.0}, {1: 95.0}, {}) == {1: False}
    with pytest.raises(KeyError):
        rules.failed_formulations({1: 97.0}, {}, {})
