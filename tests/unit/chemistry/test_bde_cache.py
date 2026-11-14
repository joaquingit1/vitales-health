"""Fast tests of the BDE cache and the pure parts of pharmacy.chemistry.bde (spec 7.4).
No test here runs xTB: the autouse guard in conftest.py fails any cache miss."""

import copy
import dataclasses
import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rdkit import Chem

from pharmacy.chemistry import bde
from pharmacy.chemistry.structure import mol_from_smiles
from pharmacy.config import ConfigBundle, canonical_json_bytes, canonical_sha256
from pharmacy.constants import HARTREE_TO_J_PER_MOL, J_PER_KJ
from pharmacy.errors import ConvergenceError, StructureError, XtbError
from pharmacy.types import ComputedLayerSettings, to_json

PROMETHAZINE = "CC(CN1C2=CC=CC=C2SC3=CC=CC=C31)N(C)C"


def _settings(bundle: ConfigBundle) -> ComputedLayerSettings:
    return bundle.settings["computed_layer"]


def _entries() -> list[Path]:
    return sorted(bde.DEFAULT_CACHE_DIR.glob("*.json"))


def test_committed_cache_entries_are_canonical_and_consistent(bundle: ConfigBundle) -> None:
    entries = _entries()
    assert len(entries) == 14
    settings = _settings(bundle)
    label = bde._method_label(settings)
    for path in entries:
        result = bde._read_cache(path, path.stem)
        assert path.read_bytes() == canonical_json_bytes(to_json(result))
        assert result.method == label
        assert result.bond in bde.BOND_ELEMENTS
        assert math.isfinite(result.bde_kj_per_mol)
        # GFN2-xTB H atom with spin polarization (scale 1.0); -0.393483 without it.
        assert result.hydrogen_energy_hartree == pytest.approx(-0.4292952639, abs=1e-9)
        # The stored BDE is the stored energies through the spec formula.
        recomputed = (
            (result.radical_energy_hartree + result.hydrogen_energy_hartree - result.parent_energy_hartree)
            * HARTREE_TO_J_PER_MOL
            / J_PER_KJ
        )
        assert result.bde_kj_per_mol == recomputed
        material = bde.cache_key_material(result.inchikey, result.site_atom, result.bond, settings)
        assert canonical_sha256(material) == result.cache_key
        # Spec 7.4: every conformer embedded, at least half of each optimization converged.
        assert result.conformers_embedded == 10
        assert 2 * result.parents_converged >= result.conformers_embedded
        assert 2 * result.radicals_converged >= result.parents_converged


def test_cache_hit_values_for_references(bundle: ConfigBundle) -> None:
    """The three Evans-Polanyi reference molecules as committed (kJ/mol, electronic)."""
    settings = _settings(bundle)
    phenol = bde.bond_dissociation_energy("Oc1ccccc1", 0, "O-H", settings)
    catechol = bde.bond_dissociation_energy("Oc1ccccc1O", 0, "O-H", settings)
    tma = bde.bond_dissociation_energy("CN(C)C", 0, "C-H", settings)
    assert phenol.bde_kj_per_mol == pytest.approx(365.207, abs=5e-4)
    assert catechol.bde_kj_per_mol == pytest.approx(330.559, abs=5e-4)
    assert tma.bde_kj_per_mol == pytest.approx(378.752, abs=5e-4)


def test_key_does_not_depend_on_smiles_spelling_or_atom_order(bundle: ConfigBundle) -> None:
    settings = _settings(bundle)
    mol = mol_from_smiles(PROMETHAZINE)
    reference = {atom: bde.bde_cache_key(PROMETHAZINE, atom, "C-H", settings) for atom in (1, 18, 19)}
    rng = random.Random(7)
    for _ in range(5):
        order = list(range(mol.GetNumAtoms()))
        rng.shuffle(order)
        permuted = Chem.RenumberAtoms(mol, order)
        text = str(Chem.MolToSmiles(permuted, canonical=False))
        reparsed = mol_from_smiles(text)
        # Map each original site to its index in the reparsed permuted SMILES.
        out_order = [
            int(i) for i in permuted.GetProp("_smilesAtomOutputOrder").strip("[], ").split(",") if i.strip()
        ]
        for atom, key in reference.items():
            new_index = out_order.index(order.index(atom))
            assert reparsed.GetAtomWithIdx(new_index).GetAtomicNum() == 6
            assert bde.bde_cache_key(text, new_index, "C-H", settings) == key
            # A cache hit, never a computation (the guard would fail the test).
            assert bde.bond_dissociation_energy(text, new_index, "C-H", settings).cache_key == key


def test_symmetry_equivalent_sites_share_one_entry(bundle: ConfigBundle) -> None:
    """The two N-methyls of promethazine are symmetry equivalent: one key, one value (spec 7.4)."""
    settings = _settings(bundle)
    a = bde.bond_dissociation_energy(PROMETHAZINE, 18, "C-H", settings)
    b = bde.bond_dissociation_energy(PROMETHAZINE, 19, "C-H", settings)
    assert a == b
    assert bde._validate_site(PROMETHAZINE, 18, "C-H") == bde._validate_site(PROMETHAZINE, 19, "C-H")
    assert bde._validate_site(PROMETHAZINE, 1, "C-H") != bde._validate_site(PROMETHAZINE, 18, "C-H")


def _fake(key: str, inchikey: str, rank: int, settings: ComputedLayerSettings | None = None) -> bde.BdeResult:
    return bde.BdeResult(
        inchikey=inchikey,
        site_atom=rank,
        bond="C-H",
        bde_kj_per_mol=(-6.44 - 0.393483 + 7.0) * HARTREE_TO_J_PER_MOL / J_PER_KJ,
        parent_energy_hartree=-7.0,
        radical_energy_hartree=-6.44,
        hydrogen_energy_hartree=-0.393483,
        conformers_embedded=10,
        parents_converged=9,
        radicals_converged=8,
        method="test" if settings is None else bde._method_label(settings),
        cache_key=key,
    )


def test_cache_round_trip(bundle: ConfigBundle, tmp_path: Path) -> None:
    settings = _settings(bundle)
    key = bde.bde_cache_key("CCO", 0, "C-H", settings)
    site = bde._validate_site("CCO", 0, "C-H")
    fake = _fake(key, site.inchikey, site.canonical_rank, settings)
    bde._write_cache(tmp_path / f"{key}.json", fake)
    assert not list(tmp_path.glob(".*.tmp"))
    assert bde.bond_dissociation_energy("CCO", 0, "C-H", settings, tmp_path) == fake
    assert bde.bond_dissociation_energy("OCC", 2, "C-H", settings, tmp_path) == fake  # same atom
    # ETKDG may embed fewer conformers than requested; the result records the actual count.
    partial = dataclasses.replace(fake, conformers_embedded=9)
    bde._write_cache(tmp_path / f"{key}.json", partial)
    assert bde.bond_dissociation_energy("CCO", 0, "C-H", settings, tmp_path) == partial
    # The CH2 of ethanol is another site: a miss, which the fast-test guard refuses.
    with pytest.raises(AssertionError, match="reached xTB"):
        bde.bond_dissociation_energy("CCO", 1, "C-H", settings, tmp_path)


def test_cache_directory_precedence(
    bundle: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(bundle)
    site = bde._validate_site("CCO", 0, "C-H")
    key = bde.bde_cache_key("CCO", 0, "C-H", settings)
    result = _fake(key, site.inchikey, site.canonical_rank, settings)
    default, environment, explicit = (tmp_path / name for name in ("default", "environment", "explicit"))
    for directory in (default, environment, explicit):
        bde._write_cache(directory / f"{key}.json", result)
    monkeypatch.setattr(bde, "DEFAULT_CACHE_DIR", default)
    monkeypatch.setenv("PHARMACY_XTB_CACHE_DIR", str(environment))
    (environment / f"{key}.json").write_text("broken", encoding="utf-8")
    assert bde.bond_dissociation_energy("CCO", 0, "C-H", settings, explicit) == result
    with pytest.raises(XtbError, match="not valid JSON"):
        bde.bond_dissociation_energy("CCO", 0, "C-H", settings)
    monkeypatch.delenv("PHARMACY_XTB_CACHE_DIR")
    assert bde.bond_dissociation_energy("CCO", 0, "C-H", settings) == result


def test_cache_miss_writes_to_environment_directory(
    bundle: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(bundle)
    key = bde.bde_cache_key("CCO", 0, "C-H", settings)
    site = bde._validate_site("CCO", 0, "C-H")
    result = _fake(key, site.inchikey, site.canonical_rank, settings)
    monkeypatch.setenv("PHARMACY_XTB_CACHE_DIR", str(tmp_path / "environment"))
    monkeypatch.setattr(bde, "_compute", lambda *_args: result)
    assert bde.bond_dissociation_energy("CCO", 0, "C-H", settings) == result
    assert (tmp_path / "environment" / f"{key}.json").read_bytes() == canonical_json_bytes(to_json(result))


def test_atomic_cache_write_and_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = "a" * 64
    destination = tmp_path / f"{key}.json"
    result = _fake(key, "X", 0)
    old = b"old complete entry"
    destination.write_bytes(old)
    replace = os.replace

    def inspect_and_replace(source: Path, target: Path) -> None:
        assert target.read_bytes() == old
        assert source.parent == target.parent
        assert source.read_bytes() == canonical_json_bytes(to_json(result))
        replace(source, target)

    monkeypatch.setattr(os, "replace", inspect_and_replace)
    bde._write_cache(destination, result)
    assert destination.read_bytes() == canonical_json_bytes(to_json(result))

    def fail(_source: Path, _target: Path) -> None:
        raise OSError("test replace failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(XtbError, match="cannot write"):
        bde._write_cache(destination, result)
    assert list(tmp_path.iterdir()) == [destination]
    assert destination.read_bytes() == canonical_json_bytes(to_json(result))


def test_concurrent_cache_writers_use_separate_temporary_files(tmp_path: Path) -> None:
    result = _fake("a" * 64, "X", 0)
    destination = tmp_path / f"{result.cache_key}.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(bde._write_cache, destination, result) for _ in range(8)]
        for future in futures:
            future.result()
    assert bde._read_cache(destination, result.cache_key) == result
    assert list(tmp_path.iterdir()) == [destination]


def test_cache_temporary_cleanup_failure_is_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path, *, missing_ok: bool = False) -> None:
        raise OSError("test cleanup failure")

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(XtbError, match="cannot remove temporary"):
        bde._write_cache(tmp_path / "k.json", _fake("k", "X", 0))


def _write_raw(directory: Path, key: str, payload: object) -> None:
    (directory / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_corrupt_cache_entries_raise(bundle: ConfigBundle, tmp_path: Path) -> None:
    settings = _settings(bundle)
    key = bde.bde_cache_key("CC", 0, "C-H", settings)
    good = to_json(_fake(key, "OTMSDBZUPAUEDD-UHFFFAOYSA-N", 0, settings))
    assert isinstance(good, dict)
    path = tmp_path / f"{key}.json"

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(XtbError, match="not valid JSON"):
        bde.bond_dissociation_energy("CC", 0, "C-H", settings, tmp_path)

    for broken in (
        {k: v for k, v in good.items() if k != "method"},
        [1, 2],
        {**good, "bde_kj_per_mol": "420"},
        {**good, "site_atom": True},
        {**good, "site_atom": 1.5},
        {**good, "inchikey": 3},
        {**good, "cache_key": "0" * 64},
        {**good, "parents_converged": 4},  # fewer than half of 10
        {**good, "radicals_converged": 4},  # fewer than half of 9
        {**good, "radicals_converged": 10},  # more radicals than parents
        {**good, "conformers_embedded": 0, "parents_converged": 0, "radicals_converged": 0},
        {**good, "parents_converged": -1},
        {**good, "parents_converged": 1.0},
        {**good, "bde_kj_per_mol": 999.0},
        {**good, "inchikey": "wrong"},
        {**good, "site_atom": 99},
        {**good, "bond": "O-H"},
        {**good, "method": "wrong"},
        {**good, "conformers_embedded": 11},
    ):
        _write_raw(tmp_path, key, broken)
        with pytest.raises(XtbError):
            bde.bond_dissociation_energy("CC", 0, "C-H", settings, tmp_path)

    path.write_bytes(b"\xff")
    with pytest.raises(XtbError, match="cannot read"):
        bde.bond_dissociation_energy("CC", 0, "C-H", settings, tmp_path)

    path.unlink()
    path.mkdir()
    with pytest.raises(XtbError, match="cannot read"):
        bde.bond_dissociation_energy("CC", 0, "C-H", settings, tmp_path)


def test_enough_converged() -> None:
    """Spec 7.4: half of the parent optimizations (one per embedded conformer) and half of
    the radical optimizations (one per converged parent) must converge."""
    assert bde._enough_converged(10, 10, 10)
    assert bde._enough_converged(10, 5, 3)
    assert bde._enough_converged(1, 1, 1)
    assert not bde._enough_converged(10, 4, 4)
    assert not bde._enough_converged(10, 6, 2)
    assert not bde._enough_converged(10, 10, 4)
    assert not bde._enough_converged(0, 0, 0)
    assert not bde._enough_converged(10, 11, 5)
    assert not bde._enough_converged(10, 6, 7)


def test_read_cache_checks_file_name(tmp_path: Path) -> None:
    key = "a" * 64
    entry = tmp_path / ("b" * 64 + ".json")
    entry.write_bytes(canonical_json_bytes(to_json(_fake(key, "X", 0))))
    with pytest.raises(XtbError, match="names key"):
        bde._read_cache(entry, key)


def test_write_cache_failure_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(XtbError, match="cannot write"):
        bde._write_cache(blocker / "k.json", _fake("k", "X", 0))


@pytest.mark.parametrize(
    ("smiles", "atom", "bond", "message"),
    [
        ("CC", 0, "S-H", "bond must be one of"),
        ("CC", 5, "C-H", "is not an atom"),
        ("CC", -1, "C-H", "is not an atom"),
        ("CO", 1, "C-H", "not the C-H atom"),
        ("CC(C)(C)C", 1, "C-H", "carries no hydrogen"),
        ("C1CC", 0, "C-H", "cannot parse"),
    ],
)
def test_validate_site_errors(smiles: str, atom: int, bond: str, message: str) -> None:
    with pytest.raises(StructureError, match=message):
        bde._validate_site(smiles, atom, bond)


def test_validate_site_maps_to_canonical_index() -> None:
    site = bde._validate_site("OCC", 0, "O-H")
    assert site.canonical_smiles == "CCO"
    assert site.canonical_index == 2
    assert mol_from_smiles(site.canonical_smiles).GetAtomWithIdx(site.canonical_index).GetAtomicNum() == 8


def test_cache_key_material(bundle: ConfigBundle) -> None:
    settings = _settings(bundle)
    material = bde.cache_key_material("OTMSDBZUPAUEDD-UHFFFAOYSA-N", 0, "C-H", settings)
    assert material == {
        "inchikey": "OTMSDBZUPAUEDD-UHFFFAOYSA-N",
        "site_canonical_rank": 0,
        "bond": "C-H",
        "method": "GFN2-xTB",
        "accuracy": 1.0,
        "electronic_temperature_k": 300.0,
        "seed": 20261114,
        "n_conformers": 10,
        "spin_polarization_scale": 1.0,
        "optimizer_gtol_eh_per_bohr": 1.0e-4,
        "optimizer_max_steps": 500,
        "tblite_version": bde.tblite_version(),
        "rdkit_version": bde.rdkit_version(),
    }
    assert bde.tblite_version() == "0.8.0"
    assert bde.rdkit_version() == "2026.09.2"
    for field, value, message in (
        ("rdkit_embed_seed", 1.5, "rdkit_embed_seed must be an integer"),
        ("rdkit_embed_seed", -1.0, "rdkit_embed_seed must be an integer >= 0"),
        ("xtb_n_conformers", 0.0, "xtb_n_conformers must be an integer >= 1"),
        ("xtb_n_conformers", math.inf, "xtb_n_conformers must be an integer"),
        ("optimizer_max_steps", 2.5, "optimizer_max_steps must be an integer"),
        ("xtb_spin_polarization_scale", -0.1, "xtb_spin_polarization_scale must be finite"),
        ("xtb_spin_polarization_scale", math.nan, "xtb_spin_polarization_scale must be finite"),
    ):
        broken: Any = copy.deepcopy(settings)
        broken[field]["value"] = value
        with pytest.raises(XtbError, match=message):
            bde.cache_key_material("X", 0, "C-H", broken)


def test_every_keyed_setting_changes_the_key(bundle: ConfigBundle) -> None:
    """Spec 7.4: conformer count, spin-polarization scale, gradient tolerance and step limit
    are part of the key, so changing any of them misses the committed cache."""
    settings = _settings(bundle)
    base = bde.bde_cache_key("CCO", 0, "C-H", settings)
    for field, value in (
        ("xtb_n_conformers", 3.0),
        ("xtb_spin_polarization_scale", 0.0),
        ("optimizer_gtol_eh_per_bohr", 5e-4),
        ("optimizer_max_steps", 1000.0),
        ("rdkit_embed_seed", 1.0),
        ("xtb_accuracy", 0.5),
    ):
        changed: Any = copy.deepcopy(settings)
        changed[field]["value"] = value
        assert bde.bde_cache_key("CCO", 0, "C-H", changed) != base, field


def test_electronic_temperature_in_hartree(bundle: ConfigBundle) -> None:
    # 300 K x 1.380649e-23 J/K / 4.3597447222060e-18 J
    assert bde._electronic_temperature_hartree(_settings(bundle)) == pytest.approx(9.50044e-4, rel=1e-5)


@pytest.mark.parametrize(
    ("numbers", "charge", "uhf"),
    [((1,), 0, 0), ((6, 1, 1, 1, 1), 0, 1), ((1,), 0, -1), ((1,), 0, 3), ((8,), 1, 0)],
)
def test_check_spin_rejects_impossible_multiplicity(numbers: tuple[int, ...], charge: int, uhf: int) -> None:
    with pytest.raises(StructureError):
        bde._check_spin(numbers, charge, uhf)


def test_check_spin_accepts_consistent_multiplicity() -> None:
    bde._check_spin((1,), 0, 1)
    bde._check_spin((6, 1, 1, 1, 1), 0, 0)
    bde._check_spin((6, 1, 1, 1), 0, 1)


def test_embed_conformers_is_deterministic_and_checks_mmff() -> None:
    first, first_ids = bde.embed_conformers(mol_from_smiles("CCCO"), 20261114, 10)
    second, second_ids = bde.embed_conformers(mol_from_smiles("CCCO"), 20261114, 10)
    assert first_ids == second_ids == tuple(range(10))
    for conformer_id in first_ids:
        a = first.GetConformer(conformer_id).GetPositions()
        b = second.GetConformer(conformer_id).GetPositions()
        assert (a == b).all()
        assert a.shape == (12, 3)
    _single, single_ids = bde.embed_conformers(mol_from_smiles("CCO"), 20261114, 1)
    assert single_ids == (0,)
    with pytest.raises(StructureError, match="MMFF94"):
        bde.embed_conformers(mol_from_smiles("CB(C)C"), 20261114, 2)
    with pytest.raises(StructureError, match="at least 1"):
        bde.embed_conformers(mol_from_smiles("CCO"), 20261114, 0)


def test_positions_are_validated() -> None:
    with pytest.raises(XtbError, match="positions"):
        bde._as_array(((0.0, 0.0),), 1)  # type: ignore[arg-type]
    with pytest.raises(XtbError, match="positions"):
        bde._as_array(((math.nan, 0.0, 0.0),), 1)


def test_bde_result_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _fake("k", "X", 0).bde_kj_per_mol = 1.0  # type: ignore[misc]


# --- the optimizer and error paths with a stand-in calculator (no tblite) --------------------------
class _FakeCalculator:
    """Stands in for tblite's Calculator: ``update`` stores positions, ``singlepoint`` returns
    a mapping with energy and gradient from ``surface``."""

    def __init__(self, surface: Any, fail_update: bool = False) -> None:
        self.surface = surface
        self.fail_update = fail_update
        self.positions = np.zeros((1, 3))

    def update(self, positions: Any) -> None:
        if self.fail_update:
            raise RuntimeError("bad geometry")
        self.positions = np.asarray(positions)

    def singlepoint(self) -> dict[str, Any]:
        energy, gradient = self.surface(self.positions)
        return {"energy": energy, "gradient": gradient}


def _use(monkeypatch: pytest.MonkeyPatch, calc: _FakeCalculator) -> None:
    monkeypatch.setattr(bde, "_calculator", lambda *_args: calc)


def _bowl(x: Any) -> tuple[float, Any]:
    return float(np.sum((x - 1.0) ** 2)), 2.0 * (x - 1.0)


def test_optimizer_converges_on_a_bowl(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _FakeCalculator(_bowl))
    result, positions = bde._optimize((1, 1), ((0.0, 0.0, 0.0), (0.5, 0.5, 0.5)), 0, 0, _settings(bundle))
    assert result.converged
    assert result.gradient_norm_hartree_per_bohr <= 1e-4
    assert np.allclose(positions, 1.0, atol=1e-4)


def test_optimizer_gives_up_on_inconsistent_gradient(
    bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gradient that never reaches gtol and does not match the energy makes line searches
    fail: the optimizer restarts while steps remain, then raises ConvergenceError."""

    def tilted(x: Any) -> tuple[float, Any]:
        return float(np.sum(x**2)), 2.0 * x + 1e-2

    _use(monkeypatch, _FakeCalculator(tilted))
    with pytest.raises(ConvergenceError):
        bde._optimize((1,), ((0.3, -0.2, 0.1),), 0, 0, _settings(bundle))


def test_evaluate_and_update_failures(bundle: ConfigBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_x: Any) -> tuple[float, Any]:
        raise RuntimeError("SCF did not converge")

    with pytest.raises(XtbError, match="single point failed"):
        bde._evaluate(_FakeCalculator(broken))
    with pytest.raises(XtbError, match="non-finite"):
        bde._evaluate(_FakeCalculator(lambda x: (math.nan, x)))
    with pytest.raises(XtbError, match="non-finite"):
        bde._evaluate(_FakeCalculator(lambda x: (0.0, x + math.inf)))
    _use(monkeypatch, _FakeCalculator(_bowl, fail_update=True))
    with pytest.raises(XtbError, match="geometry update"):
        bde._optimize((1,), ((0.0, 0.0, 0.0),), 0, 0, _settings(bundle))


def test_failed_embedding_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bde.AllChem, "EmbedMultipleConfs", lambda *_args: ())
    with pytest.raises(StructureError, match="could not embed"):
        bde.embed_conformers(mol_from_smiles("CCO"), 20261114, 1)


def test_optional_thread_runtime_symbols(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "tblite"
    libraries = package / ".dylibs"
    libraries.mkdir(parents=True)
    for name in ("README", "libomp_missing.so", "libopenblas_missing.so", "libunrelated.so"):
        (libraries / name).touch()
    monkeypatch.setattr("tblite.interface.__file__", str(package / "interface.py"))
    monkeypatch.setattr("ctypes.CDLL", lambda _name: object())
    assert bde._pin_tblite_threads.__wrapped__() == ()
