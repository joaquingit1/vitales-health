"""Bond dissociation energies with GFN2-xTB (tblite) for the Evans-Polanyi computed layer.

Owner: B2. Contract: docs/ENGINE-SPEC.md section 7.4. Excluded from the default coverage
run (tests marked ``xtb``). Results are cached by (InChIKey, site, bond, method version),
so estimates never depend on whether xTB ran in this process.

Procedure (spec 7.4): the molecule is rebuilt from its RDKit canonical isomeric SMILES so
the computation depends only on the molecule and the site (up to symmetry), never on how the
input SMILES was written. RDKit adds hydrogens, embeds ``xtb_n_conformers`` conformers with
ETKDGv3 (fixed seed) and pre-optimizes each with MMFF94; GFN2-xTB then optimizes every
conformer of the closed-shell parent (uhf 0) with scipy L-BFGS-B on the analytic energy and
gradient, and the parent energy is the lowest. A radical (uhf 1) is built from every optimized
parent conformer by removing the lowest-index hydrogen on the site atom and re-optimized; the
radical energy is the lowest. The hydrogen atom is a doublet single point. Open-shell species
(radical and H atom) carry tblite's spin-polarization interaction at
``xtb_spin_polarization_scale`` (0 leaves it out). Electronic BDE:

    BDE = (E_radical + E_H - E_parent) x HARTREE_TO_J_PER_MOL / 1000      [kJ/mol]

Determinism: tblite parallelizes its gradient with OpenMP and the reduction order of
threaded sums varies between runs (the energy is reproducible, the gradient differs in the
last bits), which would make the optimizer path, and so the BDE, vary. The tblite OpenMP
and OpenBLAS runtimes are therefore pinned to one thread before any calculation
(``_pin_tblite_threads``); recomputing an entry then reproduces it bit for bit.
"""

import ctypes
import dataclasses
import importlib.metadata
import math
import os
from functools import cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import numpy as np
from numpy.typing import NDArray
from rdkit import Chem
from rdkit import __version__ as rdkit_installed_version
from rdkit.Chem import AllChem as _AllChem
from scipy.optimize import minimize

from pharmacy.chemistry.structure import mol_from_smiles, molecule_info
from pharmacy.config import REPO_ROOT, canonical_json_bytes, canonical_sha256, parse_json_text
from pharmacy.constants import (
    ANGSTROM_TO_BOHR,
    BOLTZMANN_J_PER_K,
    HARTREE_ENERGY_J,
    HARTREE_TO_J_PER_MOL,
    J_PER_KJ,
)
from pharmacy.errors import ConvergenceError, StructureError, XtbError
from pharmacy.types import ComputedLayerSettings, JSONValue, to_json

DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "pharmacy" / "xtb-cache"
"""Committed BDE cache (spec 7.4)."""

BOND_ELEMENTS = {"C-H": 6, "N-H": 7, "O-H": 8}
"""Bonds the Evans-Polanyi entries may name (lfer-constants schema) and the atomic number of
the heavy atom each one breaks away from hydrogen."""

HYDROGEN_ATOMIC_NUMBER = 1

AllChem: Any = _AllChem
"""RDKit AllChem typed as ``Any``: its stubs omit the embedding and MMFF functions."""

type Positions = tuple[tuple[float, float, float], ...]
type FloatArray = NDArray[np.float64]


@dataclasses.dataclass(frozen=True, slots=True)
class XtbResult:
    energy_hartree: float
    gradient_norm_hartree_per_bohr: float
    converged: bool
    n_steps: int


# =========================================================================================
# Library versions and thread pinning
# =========================================================================================


def tblite_version() -> str:
    """Installed tblite version (``Pins.tblite_version``, cache key)."""
    return importlib.metadata.version("tblite")


def rdkit_version() -> str:
    """Installed RDKit version (``Pins.rdkit_version``, cache key)."""
    return str(rdkit_installed_version)


_OPENMP_LIBRARY_PREFIXES = ("libgomp", "libomp", "libiomp")
_OPENBLAS_LIBRARY_PREFIX = "libopenblas"


@cache
def _pin_tblite_threads() -> tuple[str, ...]:
    """Set the OpenMP runtimes and OpenBLAS bundled with tblite to one thread (spec 7.4) and
    return the library files that were pinned. A tblite build without bundled runtimes has nothing
    to pin; reproducibility is then checked by the ``xtb`` tests."""
    import tblite.interface  # noqa: PLC0415  (loads the bundled runtimes first)

    package_dir = Path(tblite.interface.__file__).resolve().parent
    candidates = [package_dir / ".dylibs", package_dir / ".libs", package_dir.parent / "tblite.libs"]
    pinned: list[str] = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            name = path.name
            if not (name.endswith((".dylib", ".dll")) or ".so" in name):
                continue
            if name.startswith(_OPENMP_LIBRARY_PREFIXES):
                library = ctypes.CDLL(str(path))
                if hasattr(library, "omp_set_num_threads"):
                    library.omp_set_num_threads(ctypes.c_int(1))
                    pinned.append(name)
            elif name.startswith(_OPENBLAS_LIBRARY_PREFIX):
                library = ctypes.CDLL(str(path))
                if hasattr(library, "openblas_set_num_threads"):
                    library.openblas_set_num_threads(ctypes.c_int(1))
                    pinned.append(name)
    return tuple(pinned)


# =========================================================================================
# Geometry and xTB calls
# =========================================================================================


def embed_conformers(mol: Any, seed: int, n_conformers: int) -> tuple[Any, tuple[int, ...]]:
    """Add hydrogens, embed ``n_conformers`` conformers with ETKDGv3 (``EmbedMultipleConfs``,
    randomSeed = seed), optimize each with MMFF94 and return the molecule with its conformer
    ids in embedding order. Raises ``StructureError`` if no conformer embeds, MMFF94 cannot
    type the molecule or ``n_conformers`` is below 1.

    MMFF94 is only the starting geometry (the xTB optimization follows), so an MMFF run that
    stops at RDKit's default iteration limit is accepted."""
    if n_conformers < 1:
        raise StructureError(f"the number of conformers must be at least 1, got {n_conformers}")
    with_h = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    ids = tuple(int(i) for i in AllChem.EmbedMultipleConfs(with_h, int(n_conformers), params))
    if not ids:
        raise StructureError(f"ETKDGv3 could not embed {Chem.MolToSmiles(mol)!r} with seed {seed}")
    if not AllChem.MMFFHasAllMoleculeParams(with_h):
        raise StructureError(f"MMFF94 has no parameters for {Chem.MolToSmiles(mol)!r}")
    AllChem.MMFFOptimizeMoleculeConfs(with_h, mmffVariant="MMFF94")
    return with_h, ids


def _integer_setting(name: str, value: float, minimum: int) -> int:
    if not math.isfinite(value) or float(value) != math.floor(value) or value < minimum:
        raise XtbError(f"{name} must be an integer >= {minimum}, got {value!r}")
    return int(value)


def _seed(settings: ComputedLayerSettings) -> int:
    return _integer_setting("rdkit_embed_seed", settings["rdkit_embed_seed"]["value"], 0)


def _n_conformers(settings: ComputedLayerSettings) -> int:
    return _integer_setting("xtb_n_conformers", settings["xtb_n_conformers"]["value"], 1)


def _max_steps(settings: ComputedLayerSettings) -> int:
    return _integer_setting("optimizer_max_steps", settings["optimizer_max_steps"]["value"], 0)


def _spin_polarization_scale(settings: ComputedLayerSettings) -> float:
    value = float(settings["xtb_spin_polarization_scale"]["value"])
    if not math.isfinite(value) or value < 0.0:
        raise XtbError(f"xtb_spin_polarization_scale must be finite and >= 0, got {value!r}")
    return value


def _electronic_temperature_hartree(settings: ComputedLayerSettings) -> float:
    """tblite expects the electronic temperature in hartree: T x k_B / E_h."""
    return settings["xtb_electronic_temperature_k"]["value"] * BOLTZMANN_J_PER_K / HARTREE_ENERGY_J


def _check_spin(numbers: tuple[int, ...], charge: int, uhf: int) -> None:
    electrons = sum(numbers) - charge
    if uhf < 0 or electrons < uhf or (electrons - uhf) % 2 != 0:
        raise StructureError(f"{electrons} electrons cannot have {uhf} unpaired electron(s)")


def _calculator(
    numbers: tuple[int, ...], positions: FloatArray, charge: int, uhf: int, settings: ComputedLayerSettings
) -> Any:
    _pin_tblite_threads()
    from tblite.interface import Calculator  # noqa: PLC0415  (heavy native import, xTB paths only)

    _check_spin(numbers, charge, uhf)
    scale = _spin_polarization_scale(settings)
    try:
        calc = Calculator(
            settings["xtb_method"], np.asarray(numbers, dtype=np.int64), positions, charge=charge, uhf=uhf
        )
        calc.set("verbosity", 0)
        calc.set("accuracy", float(settings["xtb_accuracy"]["value"]))
        calc.set("temperature", _electronic_temperature_hartree(settings))
        if uhf > 0 and scale > 0.0:
            calc.add("spin-polarization", scale)
    except Exception as exc:  # tblite raises TBLiteValueError and RuntimeError subclasses
        raise XtbError(f"tblite could not set up {settings['xtb_method']}: {exc}") from exc
    return calc


def _evaluate(calc: Any) -> tuple[float, FloatArray]:
    try:
        result = calc.singlepoint()
        energy = float(np.asarray(result.get("energy"), dtype=np.float64).ravel()[0])
        gradient = np.asarray(result.get("gradient"), dtype=np.float64)
    except Exception as exc:  # TBLiteRuntimeError: SCF failure and similar
        raise XtbError(f"tblite single point failed: {exc}") from exc
    if not math.isfinite(energy) or not np.all(np.isfinite(gradient)):
        raise XtbError("tblite returned a non-finite energy or gradient")
    return energy, gradient


def _as_array(positions_bohr: Positions | FloatArray, n_atoms: int) -> FloatArray:
    array = np.asarray(positions_bohr, dtype=np.float64)
    if array.shape != (n_atoms, 3) or not np.all(np.isfinite(array)):
        raise XtbError(f"positions must be a finite ({n_atoms}, 3) array, got shape {array.shape}")
    return np.ascontiguousarray(array)


def xtb_singlepoint(
    numbers: tuple[int, ...],
    positions_bohr: tuple[tuple[float, float, float], ...],
    charge: int,
    uhf: int,
    settings: ComputedLayerSettings,
) -> tuple[float, tuple[tuple[float, float, float], ...]]:
    """Energy (Eh) and gradient (Eh/bohr) from tblite
    ``Calculator("GFN2-xTB", numbers, positions_bohr, charge=charge, uhf=uhf)`` with the
    configured accuracy and electronic temperature, plus the spin-polarization interaction
    when ``uhf`` > 0 and the configured scale is positive. Raises ``XtbError``."""
    positions = _as_array(positions_bohr, len(numbers))
    energy, gradient = _evaluate(_calculator(numbers, positions, charge, uhf, settings))
    rows = tuple((float(g[0]), float(g[1]), float(g[2])) for g in gradient)
    return energy, rows


def _optimize(
    numbers: tuple[int, ...],
    positions_bohr: Positions | FloatArray,
    charge: int,
    uhf: int,
    settings: ComputedLayerSettings,
) -> tuple[XtbResult, FloatArray]:
    """L-BFGS-B on the xTB energy; returns the result and the optimized positions (bohr).

    scipy's L-BFGS-B stops when the largest projected gradient component is <= ``gtol``
    (no bounds, so the plain gradient) or when the relative energy change falls below
    ``ftol``; ``ftol`` is 0 so only the gradient criterion of spec 7.4 ends a run. A run
    that stops early on a failed line search (energy noise near the minimum) restarts from
    its last point while steps remain; the step count spans the restarts."""
    start = _as_array(positions_bohr, len(numbers))
    calc = _calculator(numbers, start, charge, uhf, settings)
    gtol = float(settings["optimizer_gtol_eh_per_bohr"]["value"])
    max_steps = _max_steps(settings)

    def energy_and_gradient(x: FloatArray) -> tuple[float, FloatArray]:
        try:
            calc.update(np.ascontiguousarray(x.reshape(-1, 3)))
        except Exception as exc:
            raise XtbError(f"tblite rejected a geometry update: {exc}") from exc
        energy, gradient = _evaluate(calc)
        return energy, gradient.ravel()

    x = start.ravel().copy()
    steps = 0
    while True:
        remaining = max_steps - steps
        if remaining <= 0:
            break
        run = minimize(
            energy_and_gradient,
            x,
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": remaining, "gtol": gtol, "ftol": 0.0},
        )
        steps += int(run.nit)
        x = np.asarray(run.x, dtype=np.float64)
        largest = float(np.max(np.abs(np.asarray(run.jac, dtype=np.float64))))
        if largest <= gtol:
            result = XtbResult(
                energy_hartree=float(run.fun),
                gradient_norm_hartree_per_bohr=largest,
                converged=True,
                n_steps=steps,
            )
            return result, x.reshape(-1, 3)
        if int(run.nit) == 0:
            break
    raise ConvergenceError(
        f"xTB optimization did not reach max |gradient| <= {gtol} Eh/bohr within {max_steps} steps"
    )


def xtb_optimize(
    numbers: tuple[int, ...],
    positions_bohr: tuple[tuple[float, float, float], ...],
    charge: int,
    uhf: int,
    settings: ComputedLayerSettings,
) -> XtbResult:
    """Geometry optimization with scipy L-BFGS-B on the xTB energy and gradient until the
    max gradient component <= ``optimizer_gtol_eh_per_bohr`` or ``optimizer_max_steps``
    (spec 7.4). Unconverged raises ``ConvergenceError``.

    ``gradient_norm_hartree_per_bohr`` is the largest absolute gradient component (the
    infinity norm, the quantity the convergence test uses)."""
    result, _positions = _optimize(numbers, positions_bohr, charge, uhf, settings)
    return result


# =========================================================================================
# Bond dissociation energy and its cache
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class BdeResult:
    """One cached BDE (spec 7.4). ``conformers_embedded`` parent optimizations ran, of which
    ``parents_converged`` converged; one radical optimization ran from each converged parent,
    of which ``radicals_converged`` converged. The energies are the lowest converged ones."""

    inchikey: str
    site_atom: int
    bond: str
    bde_kj_per_mol: float
    parent_energy_hartree: float
    radical_energy_hartree: float
    hydrogen_energy_hartree: float
    conformers_embedded: int
    parents_converged: int
    radicals_converged: int
    method: str
    cache_key: str


@dataclasses.dataclass(frozen=True, slots=True)
class _Site:
    """A validated site: InChIKey, the symmetry-class canonical rank of the site atom, the
    canonical SMILES and the index (in that SMILES) of the atom xTB uses for the class
    (spec 7.4)."""

    inchikey: str
    canonical_rank: int
    canonical_smiles: str
    canonical_index: int


def _validate_site(smiles: str, site_atom: int, bond: str) -> _Site:
    mol = mol_from_smiles(smiles)
    if bond not in BOND_ELEMENTS:
        raise StructureError(f"bond must be one of {sorted(BOND_ELEMENTS)}, got {bond!r}")
    if not 0 <= site_atom < mol.GetNumAtoms():
        raise StructureError(f"site atom {site_atom} is not an atom of {smiles!r}")
    atom = mol.GetAtomWithIdx(site_atom)
    if atom.GetAtomicNum() != BOND_ELEMENTS[bond]:
        raise StructureError(
            f"site atom {site_atom} of {smiles!r} is {atom.GetSymbol()}, not the {bond} atom"
        )
    if atom.GetTotalNumHs() < 1:
        raise StructureError(f"site atom {site_atom} of {smiles!r} carries no hydrogen")
    # Symmetry-class ranks (ties not broken) are graph invariants: symmetry-equivalent atoms
    # share one rank whatever the SMILES spelling, so they share one cache entry, computed on
    # the class member that comes first in the canonical SMILES (spec 7.4).
    ranks = [int(r) for r in Chem.CanonicalRankAtoms(mol, breakTies=False)]
    canonical = str(Chem.MolToSmiles(mol, isomericSmiles=True, canonical=True))
    order = [int(i) for i in mol.GetProp("_smilesAtomOutputOrder").strip("[], ").split(",") if i.strip()]
    rank = ranks[site_atom]
    return _Site(
        inchikey=molecule_info(smiles).inchikey,
        canonical_rank=rank,
        canonical_smiles=canonical,
        canonical_index=min(k for k, index in enumerate(order) if ranks[index] == rank),
    )


def cache_key_material(
    inchikey: str, canonical_rank: int, bond: str, settings: ComputedLayerSettings
) -> dict[str, JSONValue]:
    """The object whose canonical JSON SHA-256 is the cache key: exactly the fields spec 7.4
    lists. Raises ``XtbError`` for a seed, conformer count or step limit that is not an
    integer, or a negative spin-polarization scale."""
    return {
        "inchikey": inchikey,
        "site_canonical_rank": int(canonical_rank),
        "bond": bond,
        "method": settings["xtb_method"],
        "accuracy": float(settings["xtb_accuracy"]["value"]),
        "electronic_temperature_k": float(settings["xtb_electronic_temperature_k"]["value"]),
        "seed": _seed(settings),
        "n_conformers": _n_conformers(settings),
        "spin_polarization_scale": _spin_polarization_scale(settings),
        "optimizer_gtol_eh_per_bohr": float(settings["optimizer_gtol_eh_per_bohr"]["value"]),
        "optimizer_max_steps": _max_steps(settings),
        "tblite_version": tblite_version(),
        "rdkit_version": rdkit_version(),
    }


def bde_cache_key(smiles: str, site_atom: int, bond: str, settings: ComputedLayerSettings) -> str:
    """The cache key of one site without computing anything (spec 7.4)."""
    site = _validate_site(smiles, site_atom, bond)
    return canonical_sha256(cache_key_material(site.inchikey, site.canonical_rank, bond, settings))


def _method_label(settings: ComputedLayerSettings) -> str:
    return (
        f"{settings['xtb_method']} (tblite {tblite_version()}), accuracy "
        f"{settings['xtb_accuracy']['value']}, electronic temperature "
        f"{settings['xtb_electronic_temperature_k']['value']} K, spin polarization scale "
        f"{_spin_polarization_scale(settings)} for open-shell species; "
        f"{_n_conformers(settings)} ETKDGv3 conformers (seed "
        f"{_seed(settings)}) + MMFF94 (RDKit {rdkit_version()}), lowest "
        f"parent and lowest radical; L-BFGS-B to max |gradient| <= "
        f"{settings['optimizer_gtol_eh_per_bohr']['value']} Eh/bohr within "
        f"{_max_steps(settings)} steps; electronic energies, no zero-point or "
        "thermal correction"
    )


def _read_cache(path: Path, expected_key: str) -> BdeResult:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise XtbError(f"cannot read BDE cache entry {path}: {exc}") from exc
    try:
        data = parse_json_text(text, str(path))
    except Exception as exc:  # ConfigLoadError from the strict parser
        raise XtbError(f"BDE cache entry {path} is not valid JSON: {exc}") from exc
    names = [field.name for field in dataclasses.fields(BdeResult)]
    if not isinstance(data, dict) or sorted(data) != sorted(names):
        raise XtbError(f"BDE cache entry {path} does not hold the fields of BdeResult")
    try:
        result = BdeResult(
            inchikey=_text(data["inchikey"]),
            site_atom=_integer(data["site_atom"]),
            bond=_text(data["bond"]),
            bde_kj_per_mol=_number(data["bde_kj_per_mol"]),
            parent_energy_hartree=_number(data["parent_energy_hartree"]),
            radical_energy_hartree=_number(data["radical_energy_hartree"]),
            hydrogen_energy_hartree=_number(data["hydrogen_energy_hartree"]),
            conformers_embedded=_integer(data["conformers_embedded"]),
            parents_converged=_integer(data["parents_converged"]),
            radicals_converged=_integer(data["radicals_converged"]),
            method=_text(data["method"]),
            cache_key=_text(data["cache_key"]),
        )
    except TypeError as exc:
        raise XtbError(f"BDE cache entry {path} has a field of the wrong type: {exc}") from exc
    if not _enough_converged(result.conformers_embedded, result.parents_converged, result.radicals_converged):
        raise XtbError(
            f"BDE cache entry {path} has inconsistent convergence counts "
            f"({result.conformers_embedded}, {result.parents_converged}, {result.radicals_converged})"
        )
    if result.cache_key != expected_key or path.stem != expected_key:
        raise XtbError(f"BDE cache entry {path} names key {result.cache_key!r}, expected {expected_key!r}")
    energy_bde = (
        (result.radical_energy_hartree + result.hydrogen_energy_hartree - result.parent_energy_hartree)
        * HARTREE_TO_J_PER_MOL
        / J_PER_KJ
    )
    if result.bde_kj_per_mol != energy_bde:
        raise XtbError(f"BDE cache entry {path} has a BDE inconsistent with its energies")
    return result


def _text(value: JSONValue) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {value!r}")
    return value


def _integer(value: JSONValue) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer, got {value!r}")
    return value


def _number(value: JSONValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise TypeError(f"expected a finite number, got {value!r}")
    return float(value)


def _write_cache(path: Path, result: BdeResult) -> None:
    payload = canonical_json_bytes(to_json(result))
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    except OSError as exc:
        raise XtbError(f"cannot write BDE cache entry {path}: {exc.strerror}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise XtbError(
                    f"cannot remove temporary BDE cache entry {temporary}: {exc.strerror}"
                ) from exc


def _enough_converged(embedded: int, parents: int, radicals: int) -> bool:
    """Spec 7.4: at least half of the parent optimizations (one per embedded conformer) and
    at least half of the radical optimizations (one per converged parent) converged. Half is
    taken of the optimizations that ran, as spec 7.4 states."""
    return 0 <= radicals <= parents <= embedded and 2 * parents >= embedded >= 1 and 2 * radicals >= parents


def _compute(site: _Site, bond: str, settings: ComputedLayerSettings, key: str) -> BdeResult:
    mol = mol_from_smiles(site.canonical_smiles)
    with_h, conformer_ids = embed_conformers(
        mol,
        _seed(settings),
        _n_conformers(settings),
    )
    numbers = tuple(int(atom.GetAtomicNum()) for atom in with_h.GetAtoms())
    charge = int(Chem.GetFormalCharge(with_h))
    hydrogens = [
        int(neighbor.GetIdx())
        for neighbor in with_h.GetAtomWithIdx(site.canonical_index).GetNeighbors()
        if neighbor.GetAtomicNum() == HYDROGEN_ATOMIC_NUMBER
    ]
    if not hydrogens:
        raise StructureError(f"site of {site.canonical_smiles!r} carries no hydrogen after AddHs")
    removed = min(hydrogens)
    keep = [i for i in range(len(numbers)) if i != removed]
    radical_numbers = tuple(numbers[i] for i in keep)

    # Spec 7.4: a conformer whose parent or radical optimization does not converge within the
    # step limit is left out of that minimum and counted.
    parent_energies: list[float] = []
    radical_energies: list[float] = []
    for conformer_id in conformer_ids:
        start = np.asarray(with_h.GetConformer(conformer_id).GetPositions(), dtype=np.float64)
        try:
            parent, parent_positions = _optimize(numbers, start * ANGSTROM_TO_BOHR, charge, 0, settings)
        except ConvergenceError:
            continue
        parent_energies.append(parent.energy_hartree)
        try:
            radical, _radical_positions = _optimize(
                radical_numbers, parent_positions[keep], charge, 1, settings
            )
        except ConvergenceError:
            continue
        radical_energies.append(radical.energy_hartree)
    embedded = len(conformer_ids)
    if not _enough_converged(embedded, len(parent_energies), len(radical_energies)):
        raise ConvergenceError(
            f"{site.canonical_smiles!r}: {len(parent_energies)} of {embedded} parent and "
            f"{len(radical_energies)} of {len(parent_energies)} radical optimizations converged within "
            "the optimizer step limit; spec 7.4 needs at least half of each"
        )
    hydrogen_energy, _gradient = xtb_singlepoint(
        (HYDROGEN_ATOMIC_NUMBER,), ((0.0, 0.0, 0.0),), 0, 1, settings
    )
    parent_energy = min(parent_energies)
    radical_energy = min(radical_energies)

    bde = (radical_energy + hydrogen_energy - parent_energy) * HARTREE_TO_J_PER_MOL / J_PER_KJ
    if not math.isfinite(bde):
        raise XtbError(f"non-finite BDE for {site.canonical_smiles!r}")
    return BdeResult(
        inchikey=site.inchikey,
        site_atom=site.canonical_rank,
        bond=bond,
        bde_kj_per_mol=bde,
        parent_energy_hartree=parent_energy,
        radical_energy_hartree=radical_energy,
        hydrogen_energy_hartree=hydrogen_energy,
        conformers_embedded=embedded,
        parents_converged=len(parent_energies),
        radicals_converged=len(radical_energies),
        method=_method_label(settings),
        cache_key=key,
    )


def bond_dissociation_energy(
    smiles: str, site_atom: int, bond: str, settings: ComputedLayerSettings, cache_dir: Path | None = None
) -> BdeResult:
    """Electronic BDE of the X-H bond at ``site_atom`` (spec 7.4): lowest parent (uhf 0)
    over the converged conformers and lowest radical (uhf 1, H removed from each converged
    parent conformer then re-optimized), hydrogen atom single point (uhf 1);
    BDE = (E_radical + E_H - E_parent) x HARTREE_TO_J_PER_MOL / 1000. No zero-point or
    thermal correction (cancels to first order in Evans-Polanyi differences against a
    reference computed the same way). The cache is read first:
    ``<cache_dir>/<cache_key>.json`` holds the canonical JSON of the BdeResult; a miss runs
    xTB and writes the entry. With ``cache_dir`` None, use ``PHARMACY_XTB_CACHE_DIR`` when
    set, otherwise ``data/pharmacy/xtb-cache`` under the
    repository root (committed, spec 7.4).

    ``site_atom`` indexes the molecule parsed from ``smiles``; the returned
    ``BdeResult.site_atom`` is the symmetry-class canonical rank of that atom (RDKit
    ``CanonicalRankAtoms`` without tie breaking), so a cache entry depends neither on how the
    SMILES was written nor on which of several symmetry-equivalent atoms was named (one
    entry per class, computed on the class member first in the canonical SMILES).

    Raises ``StructureError`` (unparsable SMILES, wrong element, no hydrogen on the site),
    ``XtbError`` (tblite failure, unreadable or inconsistent cache entry) or
    ``ConvergenceError`` (fewer than half of the parent or of the radical optimizations
    converged)."""
    site = _validate_site(smiles, site_atom, bond)
    key = canonical_sha256(cache_key_material(site.inchikey, site.canonical_rank, bond, settings))
    directory = (
        Path(os.environ.get("PHARMACY_XTB_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
        if cache_dir is None
        else cache_dir
    )
    path = directory / f"{key}.json"
    if path.exists():
        result = _read_cache(path, key)
        if (
            result.inchikey != site.inchikey
            or result.site_atom != site.canonical_rank
            or result.bond != bond
            or result.method != _method_label(settings)
            or result.conformers_embedded > _n_conformers(settings)
        ):
            raise XtbError(f"BDE cache entry {path} does not match the requested site or method")
        return result
    result = _compute(site, bond, settings, key)
    _write_cache(path, result)
    return result
