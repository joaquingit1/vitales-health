"""Unit tests of pharmacy.chemistry.structure (spec 7.1, 7.2, 7.3.2)."""

import copy
import random

import pytest
from rdkit import Chem

from pharmacy.chemistry import structure as s
from pharmacy.config import ConfigBundle
from pharmacy.errors import StructureError
from pharmacy.types import ReactionClassDoc
from tests.unit.chemistry.support import reaction_class

# PubChem InChIKeys of the formulary ingredients (MDX01; identity check of the SMILES).
PUBCHEM_INCHIKEYS = {
    "acetaminophen": "RZVAJINKPMORJF-UHFFFAOYSA-N",
    "aspirin": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
    "ibuprofen": "HEFNNWSXXWATRW-UHFFFAOYSA-N",
    "amoxicillin": "LSQZJLSUYDQPKJ-NJBDSQKTSA-N",
    "clavulanic_acid": "HZZVJAQRINQKSD-PBFISZAISA-N",
    "promethazine": "PWWVAXIEGOYWEE-UHFFFAOYSA-N",
    "epinephrine": "UCTWMZQNUQWSLP-VIFPVBQESA-N",
    "ascorbic_acid": "CIWBSHSKHKDKBQ-JLAZNSOCSA-N",
    "levofloxacin": "GSDSWSVVBLHKDQ-JTQLQIEISA-N",
}
HILL_FORMULAS = {
    "acetaminophen": "C8H9NO2",
    "aspirin": "C9H8O4",
    "ibuprofen": "C13H18O2",
    "amoxicillin": "C16H19N3O5S",
    "clavulanic_acid": "C8H9NO5",
    "promethazine": "C17H20N2S",
    "epinephrine": "C9H13NO3",
    "ascorbic_acid": "C6H8O6",
    "levofloxacin": "C18H20FN3O4",
}
EPINEPHRINE = "CNC[C@@H](C1=CC(=C(C=C1)O)O)O"


# --- parsing and identity -----------------------------------------------------------------------
def test_mol_from_smiles_parses_and_sanitizes() -> None:
    mol = s.mol_from_smiles("c1ccccc1O")
    assert mol.GetNumAtoms() == 7
    assert mol.GetAtomWithIdx(0).GetIsAromatic()


@pytest.mark.parametrize("bad", ["C1CC", "c1cccc1", "C(C", "not a smiles", "C[N](C)(C)(C)C"])
def test_mol_from_smiles_rejects_invalid(bad: str) -> None:
    with pytest.raises(StructureError):
        s.mol_from_smiles(bad)


def test_mol_from_smiles_rejects_empty() -> None:
    with pytest.raises(StructureError, match="no atoms"):
        s.mol_from_smiles("")


def test_canonical_smiles_and_same_molecule() -> None:
    assert s.canonical_smiles("OC(=O)c1ccccc1OC(C)=O") == s.canonical_smiles("CC(=O)OC1=CC=CC=C1C(=O)O")
    assert s.same_molecule("CC(=O)Oc1ccccc1C(=O)O", "OC(=O)c1ccccc1OC(C)=O")
    assert not s.same_molecule("Oc1ccccc1", "Oc1ccccc1O")


def test_same_molecule_respects_stereochemistry() -> None:
    assert not s.same_molecule(EPINEPHRINE, "CNC[C@H](C1=CC(=C(C=C1)O)O)O")
    assert not s.same_molecule(EPINEPHRINE, "CNCC(C1=CC(=C(C=C1)O)O)O")


def test_molecule_info_formulary_identity(bundle: ConfigBundle) -> None:
    seen = 0
    for product in bundle.formulary["products"]:
        for ingredient in product["ingredients"]:
            info = s.molecule_info(ingredient["smiles"])
            iid = ingredient["ingredient_id"]
            assert info.inchikey == PUBCHEM_INCHIKEYS[iid]
            assert info.formula == HILL_FORMULAS[iid]
            assert info.smiles_input == ingredient["smiles"]
            assert info.molar_mass_g_per_mol == pytest.approx(
                ingredient["molar_mass_g_per_mol"]["value"], abs=0.05
            )
            assert info.heavy_atom_count == s.mol_from_smiles(ingredient["smiles"]).GetNumHeavyAtoms()
            assert s.same_molecule(info.canonical_smiles, ingredient["smiles"])
            seen += 1
    assert seen == 11


def test_molecule_info_stereocenters() -> None:
    assert s.molecule_info(EPINEPHRINE).defined_stereocenters == (3,)
    assert s.molecule_info("CNCC(C1=CC(=C(C=C1)O)O)O").defined_stereocenters == ()
    amoxicillin = "CC1([C@@H](N2[C@H](S1)[C@@H](C2=O)NC(=O)[C@@H](C3=CC=C(C=C3)O)N)C(=O)O)C"
    assert s.molecule_info(amoxicillin).defined_stereocenters == (2, 4, 6, 12)


def test_molecule_info_values_for_acetaminophen() -> None:
    info = s.molecule_info("CC(=O)NC1=CC=C(C=C1)O")
    assert info.canonical_smiles == "CC(=O)Nc1ccc(O)cc1"
    # Average molar mass from IUPAC standard atomic weights as RDKit tabulates them.
    assert info.molar_mass_g_per_mol == pytest.approx(151.165, abs=1e-3)
    assert info.heavy_atom_count == 11


def test_molecule_info_is_deterministic_under_atom_permutation() -> None:
    rng = random.Random(20261114)
    mol = s.mol_from_smiles(EPINEPHRINE)
    reference = s.molecule_info(EPINEPHRINE)
    for _ in range(5):
        order = list(range(mol.GetNumAtoms()))
        rng.shuffle(order)
        permuted = str(Chem.MolToSmiles(Chem.RenumberAtoms(mol, order), canonical=False))
        info = s.molecule_info(permuted)
        assert info.canonical_smiles == reference.canonical_smiles
        assert info.inchikey == reference.inchikey


# --- reaction-class matching ----------------------------------------------------------------------
def test_match_reaction_class_sites_and_mapped_atoms(bundle: ConfigBundle) -> None:
    sites = s.match_reaction_class("CC(=O)NC1=CC=C(C=C1)O", reaction_class(bundle, "amide_hydrolysis"))
    assert len(sites) == 1
    assert sites[0].class_id == "amide_hydrolysis"
    assert sites[0].site_atom == 1
    assert sites[0].mapped_atoms == ((1, 1), (2, 3), (3, 0))


def test_catechol_sites_keep_both_oxygens(bundle: ConfigBundle) -> None:
    """RDKit uniquify would drop the reverse match; both catechol oxygens are sites (spec 7.2)."""
    sites = s.match_reaction_class(EPINEPHRINE, reaction_class(bundle, "catechol_autoxidation"))
    assert [site.site_atom for site in sites] == [10, 11]
    sizes = s.site_matches(s.mol_from_smiles(EPINEPHRINE), reaction_class(bundle, "catechol_autoxidation"))
    assert {match.pattern_atoms for match in sizes} == {4}


def test_stereocenter_requirement(bundle: ConfigBundle) -> None:
    racemization = reaction_class(bundle, "racemization_benzylic_alcohol")
    assert [x.site_atom for x in s.match_reaction_class(EPINEPHRINE, racemization)] == [3]
    assert s.match_reaction_class("CNCC(C1=CC(=C(C=C1)O)O)O", racemization) == ()


def test_site_collapse_first_pattern_wins(bundle: ConfigBundle) -> None:
    phenol = copy.deepcopy(reaction_class(bundle, "phenol_autoxidation"))
    phenol["smarts"] = ["[OX2H1:1][c:2]", "[OX2H1:1][c:2][c]"]
    matches = s.site_matches(s.mol_from_smiles("Oc1ccccc1"), phenol)
    assert len(matches) == 1
    assert matches[0].pattern_atoms == 2
    assert matches[0].site.mapped_atoms == ((1, 0), (2, 1))


def test_classes_without_smarts_match_nothing(bundle: ConfigBundle) -> None:
    assert s.match_reaction_class("CCO", reaction_class(bundle, "radiolysis")) == ()
    assert s.match_reaction_class("CCO", reaction_class(bundle, "unclassified_thermal")) == ()


def _with_smarts(base: ReactionClassDoc, smarts: list[str], site_map: int | None = 1) -> ReactionClassDoc:
    out = copy.deepcopy(base)
    out["smarts"] = smarts
    out["site_atom_map"] = site_map
    return out


def test_invalid_smarts_and_maps_raise(bundle: ConfigBundle) -> None:
    base = reaction_class(bundle, "phenol_autoxidation")
    with pytest.raises(StructureError, match="invalid SMARTS"):
        s.match_reaction_class("Oc1ccccc1", _with_smarts(base, ["[OX2H1:1"]))
    with pytest.raises(StructureError, match="exactly once"):
        s.match_reaction_class("Oc1ccccc1", _with_smarts(base, ["[OX2H1:1][c:1]"]))
    with pytest.raises(StructureError, match="exactly once"):
        s.match_reaction_class("Oc1ccccc1", _with_smarts(base, ["[OX2H1:2][c:3]"]))
    with pytest.raises(StructureError, match="no site_atom_map"):
        s.match_reaction_class("Oc1ccccc1", _with_smarts(base, ["[OX2H1:1][c:2]"], None))


def test_match_limit_is_an_error(bundle: ConfigBundle) -> None:
    # A disconnected two-atom pattern on 50 carbons has 50 * 49 ordered matches.
    greedy = _with_smarts(reaction_class(bundle, "phenol_autoxidation"), ["[#6:1].[#6]"])
    with pytest.raises(StructureError, match="match limit"):
        s.match_reaction_class("C" * 50, greedy)


def test_match_all_concatenates_in_class_order(bundle: ConfigBundle) -> None:
    classes = [reaction_class(bundle, "phenol_autoxidation"), reaction_class(bundle, "amide_hydrolysis")]
    sites = s.match_all("CC(=O)NC1=CC=C(C=C1)O", classes)
    assert [(x.class_id, x.site_atom) for x in sites] == [
        ("phenol_autoxidation", 10),
        ("amide_hydrolysis", 1),
    ]


# --- rings and substituents -------------------------------------------------------------------------
def test_aromatic_ring_and_benzene_check() -> None:
    mol = s.mol_from_smiles("CCOC(=O)c1ccncc1")
    ring = s.aromatic_ring_of(mol, 5)
    assert ring is not None
    assert sorted(ring) == [5, 6, 7, 8, 9, 10]
    assert not s.is_benzene_ring(mol, ring)
    assert s.aromatic_ring_of(mol, 0) is None
    benzene = s.mol_from_smiles("Oc1ccccc1")
    ring_b = s.aromatic_ring_of(benzene, 1)
    assert ring_b is not None
    assert s.is_benzene_ring(benzene, ring_b)


def test_aromatic_ring_prefers_smallest_ring() -> None:
    indole = s.mol_from_smiles("c1ccc2[nH]ccc2c1")
    ring = s.aromatic_ring_of(indole, 3)
    assert ring is not None
    assert len(ring) == 5


@pytest.mark.parametrize(
    ("smiles", "ring_atom", "exclude", "expected"),
    [
        ("CCOC(=O)c1ccc([N+](=O)[O-])cc1", 5, [3, 4], (("*[N+](=O)[O-]", 4),)),
        ("CCOC(=O)c1cccc(Cl)c1", 5, [3, 4], (("*Cl", 3),)),
        ("CCOC(=O)c1ccc(N(C)C)c(Cl)c1", 5, [3, 4], (("*Cl", 3), ("*N(C)C", 4))),
        ("CC(=O)Oc1ccccc1C(=O)O", 4, [3], (("*C(=O)O", 2),)),
        ("Oc1ccc(C(F)(F)F)cc1OC", 1, [0], (("*OC", 2), ("*C(F)(F)F", 4))),
        ("CCOC(=O)c1ccccc1", 5, [3, 4], ()),
        ("CCOC(=O)c1ccncc1", 5, [3, 4], ()),
    ],
)
def test_substituent_fragments(
    smiles: str, ring_atom: int, exclude: list[int], expected: tuple[tuple[str, int], ...]
) -> None:
    assert s.substituent_fragments(smiles, ring_atom, exclude) == expected


def test_substituent_fragments_fused_ring_is_one_unmatched_fragment() -> None:
    fragments = s.substituent_fragments("CCOC(=O)c1ccc2ccccc2c1", 5, [3, 4])
    assert len(fragments) == 1
    fragment, position = fragments[0]
    assert fragment.count("*") == 2
    assert position == 3


def test_substituent_fragments_errors() -> None:
    with pytest.raises(StructureError, match="not in an aromatic ring"):
        s.substituent_fragments("CCO", 0, [])
    with pytest.raises(StructureError, match="is not in"):
        s.substituent_fragments("c1ccccc1", 9, [])


def test_canonical_fragment_matches_table_spelling() -> None:
    assert s.canonical_fragment("*N(C)C") == s.canonical_fragment("CN(C)*")
    assert (
        s.canonical_fragment("*C(=O)OCC")
        == s.substituent_fragments("CCOC(=O)c1ccc(C(=O)OCC)cc1", 5, [3, 4])[0][0]
    )


def test_attached_fragment() -> None:
    # Ethyl propanoate: the acyl substituent at the carbonyl carbon (atom 1) is ethyl.
    assert s.attached_fragment("CCC(=O)OCC", 1, [2, 4]) == s.canonical_fragment("*CC")
    # A lactone substituent closes the ring through the ester oxygen: two dummies.
    lactone = s.attached_fragment("O=C1CCCO1", 2, [1, 5])
    assert lactone.count("*") == 2
    with pytest.raises(StructureError, match="not bonded"):
        s.attached_fragment("CCC(=O)OCC", 0, [])
    with pytest.raises(StructureError, match="is not in"):
        s.attached_fragment("CC", 7, [0])


def test_defined_stereocenters_helper() -> None:
    assert s.defined_stereocenters(s.mol_from_smiles(EPINEPHRINE)) == frozenset({3})


def test_molecule_info_without_inchikey() -> None:
    with pytest.raises(StructureError, match="InChIKey"):
        s.molecule_info("*C")
