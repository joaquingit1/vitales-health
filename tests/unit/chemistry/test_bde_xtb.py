"""xTB tests of pharmacy.chemistry.bde (marker ``xtb``, spec 7.4 and 7.5): determinism,
cache equality (recomputing every committed entry reproduces it bit for bit), physically
sensible ordering of BDEs, the spec's phenol range and the phenol minus methanol
difference, and symmetry-equivalent sites sharing one key.

Experimental gas-phase D298 values for orientation (Blanksby and Ellison, Acc. Chem. Res.
36 (2003) 255, kJ/mol): methane 439.3, ethane 423.0, propene allylic 371.5, toluene
benzylic 375.3, methanol O-H 440.2, phenol O-H 362.8. The engine's BDEs are electronic
GFN2-xTB energies with spin polarization (lowest over the conformers, no zero-point or
thermal correction); only differences enter Evans-Polanyi."""

import copy
import os
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rdkit import Chem

from pharmacy.chemistry import bde
from pharmacy.chemistry.lfer import evans_polanyi_entry
from pharmacy.chemistry.pathways import identify_pathways
from pharmacy.chemistry.structure import match_reaction_class, mol_from_smiles
from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.errors import ConvergenceError, StructureError, XtbError
from pharmacy.types import ComputedLayerSettings, JSONValue, to_json
from tests.unit.chemistry.support import reaction_class

pytestmark = pytest.mark.xtb


@pytest.fixture(scope="module")
def scratch_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A private cache for molecules that are not part of the committed cache."""
    return tmp_path_factory.mktemp("xtb-cache")


def _settings(bundle: ConfigBundle) -> ComputedLayerSettings:
    return bundle.settings["computed_layer"]


def _bde(bundle: ConfigBundle, cache: Path, smiles: str, atom: int, bond: str) -> float:
    return bde.bond_dissociation_energy(smiles, atom, bond, _settings(bundle), cache).bde_kj_per_mol


def _committed_sites(bundle: ConfigBundle) -> dict[str, tuple[str, int, str]]:
    """Every BDE the formulary needs (spec 7.4): each Evans-Polanyi site of every formulary
    ingredient in its default package and each Evans-Polanyi reference site, by cache key."""
    settings = _settings(bundle)
    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    out: dict[str, tuple[str, int, str]] = {}
    for product in bundle.formulary["products"]:
        for ingredient in product["ingredients"]:
            matches = identify_pathways(
                product, ingredient, bundle.reaction_classes, (packages[product["default_package_id"]],)
            )
            for match in matches:
                entry = evans_polanyi_entry(bundle.lfer, match.reaction_class["class_id"])
                if entry is None:
                    continue
                for site in match.sites:
                    key = bde.bde_cache_key(ingredient["smiles"], site.site_atom, entry["bond"], settings)
                    out[key] = (ingredient["smiles"], site.site_atom, entry["bond"])
                reference = match_reaction_class(entry["reference_smiles"], match.reaction_class)[0]
                key = bde.bde_cache_key(
                    entry["reference_smiles"], reference.site_atom, entry["bond"], settings
                )
                out[key] = (entry["reference_smiles"], reference.site_atom, entry["bond"])
    return out


def test_committed_cache_is_exactly_the_needed_set(bundle: ConfigBundle) -> None:
    needed = _committed_sites(bundle)
    committed = {path.stem for path in bde.DEFAULT_CACHE_DIR.glob("*.json")}
    assert set(needed) == committed


def _recompute(item: tuple[str, str, int, str]) -> JSONValue:
    """Worker: recompute one committed entry from scratch (no cache read)."""
    key, smiles, atom, bond = item
    site = bde._validate_site(smiles, atom, bond)
    return to_json(bde._compute(site, bond, load_bundle().settings["computed_layer"], key))


def test_recomputing_every_committed_entry_reproduces_it(bundle: ConfigBundle) -> None:
    """Spec 7.4: recomputing an entry on the same platform and versions gives an identical
    value. Every field is compared exactly (energies, BDE and the method label). Entries
    run in worker processes (tblite single-threaded in each) to bound the wall time."""
    items = [(key, *site) for key, site in sorted(_committed_sites(bundle).items())]
    workers = max(1, min(4, os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fresh = list(pool.map(_recompute, items))
    for (key, smiles, _atom, _bond), result in zip(items, fresh, strict=True):
        cached = bde._read_cache(bde.DEFAULT_CACHE_DIR / f"{key}.json", key)
        assert result == to_json(cached), smiles


def test_two_runs_are_identical(bundle: ConfigBundle, tmp_path: Path) -> None:
    first = bde.bond_dissociation_energy("CC", 0, "C-H", _settings(bundle), tmp_path / "a")
    second = bde.bond_dissociation_energy("CC", 1, "C-H", _settings(bundle), tmp_path / "b")
    assert first == second  # symmetry-equivalent carbons: one key, one value
    assert (tmp_path / "a" / f"{first.cache_key}.json").read_bytes() == (
        tmp_path / "b" / f"{second.cache_key}.json"
    ).read_bytes()


def test_alkyl_ch_ordering(bundle: ConfigBundle, scratch_cache: Path) -> None:
    """Primary alkyl C-H is stronger in methane than in ethane, and benzylic and allylic
    C-H are much weaker than either (radical delocalization)."""
    methane = _bde(bundle, scratch_cache, "C", 0, "C-H")
    ethane = _bde(bundle, scratch_cache, "CC", 0, "C-H")
    allylic = _bde(bundle, scratch_cache, "C=CC", 2, "C-H")
    benzylic = _bde(bundle, scratch_cache, "Cc1ccccc1", 0, "C-H")
    assert methane > ethane
    assert benzylic < ethane - 40.0
    assert allylic < ethane - 40.0
    assert abs(benzylic - allylic) < 15.0  # experiment: 3.8 kJ/mol apart


def test_phenol_minus_methanol_oh(bundle: ConfigBundle, scratch_cache: Path) -> None:
    """Spec 7.5: the phenoxyl radical is resonance stabilized, phenol O-H is 77.4 kJ/mol
    weaker than methanol O-H in experiment (362.8 - 440.2). The difference is the quantity
    Evans-Polanyi uses, so it must lie within 10 kJ/mol of experiment."""
    phenol = bde.bond_dissociation_energy("Oc1ccccc1", 0, "O-H", _settings(bundle)).bde_kj_per_mol
    methanol = _bde(bundle, scratch_cache, "CO", 1, "O-H")
    assert phenol < methanol
    assert abs((phenol - methanol) - (-77.4)) < 10.0


def test_catechol_oh_below_phenol_oh(bundle: ConfigBundle) -> None:
    """Spec 7.5. Both from the committed cache."""
    phenol = bde.bond_dissociation_energy("Oc1ccccc1", 0, "O-H", _settings(bundle))
    catechol = bde.bond_dissociation_energy("Oc1ccccc1O", 0, "O-H", _settings(bundle))
    assert catechol.bde_kj_per_mol < phenol.bde_kj_per_mol


def test_phenol_oh_in_spec_range(bundle: ConfigBundle) -> None:
    phenol = bde.bond_dissociation_energy("Oc1ccccc1", 0, "O-H", _settings(bundle))
    assert 300.0 <= phenol.bde_kj_per_mol <= 420.0


def test_hydrogen_atom_reference_energy(bundle: ConfigBundle) -> None:
    """With spin polarization (scale 1.0) the GFN2-xTB H atom is -0.4292952639 Eh; scale 0
    leaves the interaction out (-0.393483 Eh). Closed shells never get it."""
    energy, gradient = bde.xtb_singlepoint((1,), ((0.0, 0.0, 0.0),), 0, 1, _settings(bundle))
    assert energy == pytest.approx(-0.4292952639, abs=1e-9)
    assert gradient == ((0.0, 0.0, 0.0),)
    off: Any = copy.deepcopy(_settings(bundle))
    off["xtb_spin_polarization_scale"]["value"] = 0.0
    energy_off, _ = bde.xtb_singlepoint((1,), ((0.0, 0.0, 0.0),), 0, 1, off)
    assert energy_off == pytest.approx(-0.393483, abs=1e-6)
    h2 = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.4))
    assert bde.xtb_singlepoint((1, 1), h2, 0, 0, _settings(bundle)) == bde.xtb_singlepoint(
        (1, 1), h2, 0, 0, off
    )


def test_symmetry_equivalent_sites_share_one_key_under_permutations(
    bundle: ConfigBundle, tmp_path: Path
) -> None:
    """Spec 7.5: both methyls of dimethyl ether, under shuffled atom orders, hit one cache
    entry computed once."""
    settings = _settings(bundle)
    mol = mol_from_smiles("COC")
    first = bde.bond_dissociation_energy("COC", 0, "C-H", settings, tmp_path)
    rng = random.Random(11)
    for _ in range(4):
        order = list(range(mol.GetNumAtoms()))
        rng.shuffle(order)
        text = str(Chem.MolToSmiles(Chem.RenumberAtoms(mol, order), canonical=False))
        carbons = [a.GetIdx() for a in mol_from_smiles(text).GetAtoms() if a.GetAtomicNum() == 6]
        for carbon in carbons:
            assert bde.bond_dissociation_energy(text, carbon, "C-H", settings, tmp_path) == first
    assert [p.stem for p in tmp_path.glob("*.json")] == [first.cache_key]


def test_unconverged_conformers_are_left_out_and_counted(
    bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 7.4: a conformer whose parent or radical does not converge leaves that minimum
    and is counted; ConvergenceError when fewer than half of either converged. The
    optimizer is a scripted stand-in (energies in hartree, None for a failure); the H atom
    single point is real."""
    settings = _settings(bundle)
    site = bde._validate_site("CCO", 0, "C-H")

    def scripted(parents: list[float | None], radicals: list[float | None]) -> Any:
        parent_iter = iter(parents)
        radical_iter = iter(radicals)

        def optimize(numbers: tuple[int, ...], positions: Any, charge: int, uhf: int, _s: Any) -> Any:
            energy = next(parent_iter) if uhf == 0 else next(radical_iter)
            if energy is None:
                raise ConvergenceError("stand-in")
            result = bde.XtbResult(
                energy_hartree=energy, gradient_norm_hartree_per_bohr=0.0, converged=True, n_steps=1
            )
            return result, np.asarray(positions, dtype=np.float64).reshape(-1, 3)

        return optimize

    n = 10
    parents: list[float | None] = [-10.0 - 0.01 * i for i in range(n)]
    parents[9] = None  # the lowest parent would be conformer 9: it does not converge
    radicals: list[float | None] = [-9.4 - 0.01 * i for i in range(n - 1)]
    radicals[3] = None
    monkeypatch.setattr(bde, "_optimize", scripted(parents, radicals))
    result = bde._compute(site, "C-H", settings, "k")
    assert result.parent_energy_hartree == -10.08
    assert result.radical_energy_hartree == -9.48
    assert (result.conformers_embedded, result.parents_converged, result.radicals_converged) == (10, 9, 8)

    # Exactly half converges: accepted.
    monkeypatch.setattr(bde, "_optimize", scripted([-10.0, None] * 5, [-9.4, -9.4, -9.4, None, None]))
    result = bde._compute(site, "C-H", settings, "k")
    assert (result.parents_converged, result.radicals_converged) == (5, 3)

    monkeypatch.setattr(bde, "_optimize", scripted([-10.0] * 4 + [None] * 6, [-9.4] * 4))
    with pytest.raises(ConvergenceError, match="4 of 10 parent"):
        bde._compute(site, "C-H", settings, "k")
    monkeypatch.setattr(bde, "_optimize", scripted([-10.0] * n, [-9.4] * 4 + [None] * 6))
    with pytest.raises(ConvergenceError, match="4 of 10 radical"):
        bde._compute(site, "C-H", settings, "k")


def test_optimize_h2(bundle: ConfigBundle) -> None:
    result = bde.xtb_optimize((1, 1), ((0.0, 0.0, 0.0), (0.0, 0.0, 1.6)), 0, 0, _settings(bundle))
    assert result.converged
    assert result.gradient_norm_hartree_per_bohr <= _settings(bundle)["optimizer_gtol_eh_per_bohr"]["value"]
    assert result.n_steps >= 1
    assert result.energy_hartree < -0.98


def test_optimizer_step_limit_raises(bundle: ConfigBundle) -> None:
    settings: Any = copy.deepcopy(_settings(bundle))
    settings["optimizer_max_steps"]["value"] = 1
    with pytest.raises(ConvergenceError, match="within 1 steps"):
        bde.xtb_optimize((1, 1), ((0.0, 0.0, 0.0), (0.0, 0.0, 2.5)), 0, 0, settings)
    settings["optimizer_max_steps"]["value"] = 0
    with pytest.raises(ConvergenceError):
        bde.xtb_optimize((1, 1), ((0.0, 0.0, 0.0), (0.0, 0.0, 2.5)), 0, 0, settings)


def test_tblite_failures_raise_xtb_error(bundle: ConfigBundle) -> None:
    settings: Any = copy.deepcopy(_settings(bundle))
    with pytest.raises(XtbError, match="Too close"):
        bde.xtb_singlepoint((1, 1), ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), 0, 0, settings)
    settings["xtb_method"] = "GFN9-xTB"
    with pytest.raises(XtbError, match="not available"):
        bde.xtb_singlepoint((1,), ((0.0, 0.0, 0.0),), 0, 1, settings)


def test_threads_are_pinned() -> None:
    pinned = bde._pin_tblite_threads()
    assert any(name.startswith(("libgomp", "libomp", "libiomp")) for name in pinned)


def test_cache_miss_writes_entry(bundle: ConfigBundle, tmp_path: Path) -> None:
    result = bde.bond_dissociation_energy("CO", 1, "O-H", _settings(bundle), tmp_path)
    path = tmp_path / f"{result.cache_key}.json"
    assert path.is_file()
    assert bde._read_cache(path, result.cache_key) == result
    assert result.site_atom == bde._validate_site("CO", 1, "O-H").canonical_rank


def test_reference_sites_used_by_lfer(bundle: ConfigBundle) -> None:
    """The reference atom of each Evans-Polanyi entry is the first site of its class."""
    for class_id, expected in (
        ("phenol_autoxidation", 0),
        ("catechol_autoxidation", 0),
        ("amine_alpha_oxidation", 0),
    ):
        entry = evans_polanyi_entry(bundle.lfer, class_id)
        assert entry is not None
        sites = match_reaction_class(entry["reference_smiles"], reaction_class(bundle, class_id))
        assert sites[0].site_atom == expected


def test_bde_rejects_missing_hydrogens(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    site = bde._validate_site("CCO", 0, "C-H")
    monkeypatch.setattr(bde, "embed_conformers", lambda mol, *_args: (mol, (0,)))
    with pytest.raises(StructureError, match="no hydrogen after AddHs"):
        bde._compute(site, "C-H", _settings(bundle), "test")


def test_bde_rejects_nonfinite_energy(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    site = bde._validate_site("CCO", 0, "C-H")

    def optimize(_numbers: Any, positions: Any, _charge: int, _uhf: int, _settings: Any) -> Any:
        return bde.XtbResult(float("nan"), 0.0, True, 1), np.asarray(positions, dtype=np.float64)

    monkeypatch.setattr(bde, "_optimize", optimize)
    monkeypatch.setattr(bde, "xtb_singlepoint", lambda *_args: (0.0, ()))
    with pytest.raises(XtbError, match="non-finite BDE"):
        bde._compute(site, "C-H", _settings(bundle), "test")
