"""Molecular structures with RDKit: parsing, canonical identifiers, reaction-class matching.

Owner: B2. Contract: docs/ENGINE-SPEC.md section 7.1 and 7.2.

Every function takes SMILES text and parses it with sanitization, so results depend only on
the SMILES and the pinned RDKit version. Atom indices are RDKit indices of the molecule
parsed from the SMILES as given (no explicit hydrogens).
"""

import dataclasses
from collections.abc import Sequence
from typing import Any

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors
from rdkit.rdBase import BlockLogs

from pharmacy.errors import StructureError
from pharmacy.types import MoleculeInfo, ReactionClassDoc, ReactiveSite

_CHEM: Any = Chem
_DESCRIPTORS: Any = Descriptors
"""RDKit modules typed as ``Any`` for the functions its stubs leave untyped or omit
(``MolToInchiKey``, ``FindMolChiralCenters``, ``Descriptors.MolWt``)."""

_RDKIT_DEFAULT_MAX_MATCHES = 1000
"""RDKit's own default for ``GetSubstructMatches(maxMatches=...)``. Reaching it means the
match list may be truncated, which this module treats as an error rather than silently
missing sites (it is the library default, not a tunable)."""


def mol_from_smiles(smiles: str) -> Any:
    """RDKit Mol from SMILES (sanitized). Raises ``StructureError`` when RDKit returns
    None. RDKit is untyped, hence ``Any``.

    A SMILES without atoms (the empty string) is also a ``StructureError``: it parses but
    names no molecule."""
    with BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise StructureError(f"RDKit cannot parse or sanitize SMILES {smiles!r}")
    if mol.GetNumAtoms() == 0:
        raise StructureError(f"SMILES {smiles!r} contains no atoms")
    return mol


def canonical_smiles(smiles: str) -> str:
    """Isomeric canonical SMILES (RDKit) of a SMILES string."""
    return str(Chem.MolToSmiles(mol_from_smiles(smiles), isomericSmiles=True, canonical=True))


def same_molecule(smiles_a: str, smiles_b: str) -> bool:
    """Identity check of spec 7.3.1: two SMILES denote the same molecule when their RDKit
    canonical isomeric SMILES are equal (stereochemistry included)."""
    return canonical_smiles(smiles_a) == canonical_smiles(smiles_b)


def molecule_info(smiles: str) -> MoleculeInfo:
    """Canonical SMILES (isomeric), InChIKey, Hill formula, average molar mass (RDKit
    Descriptors.MolWt), defined stereocenters (atom indices with an assigned CIP label,
    from FindMolChiralCenters with includeUnassigned=False) and heavy atom count
    (spec 7.1). Deterministic for a given RDKit version (pinned)."""
    mol = mol_from_smiles(smiles)
    with BlockLogs():
        inchikey = str(_CHEM.MolToInchiKey(mol))
    if not inchikey:
        raise StructureError(f"RDKit cannot compute an InChIKey for {smiles!r}")
    centers = _CHEM.FindMolChiralCenters(mol, includeUnassigned=False)
    return MoleculeInfo(
        smiles_input=smiles,
        canonical_smiles=str(Chem.MolToSmiles(mol, isomericSmiles=True, canonical=True)),
        inchikey=inchikey,
        formula=str(rdMolDescriptors.CalcMolFormula(mol)),
        molar_mass_g_per_mol=float(_DESCRIPTORS.MolWt(mol)),
        defined_stereocenters=tuple(sorted(int(idx) for idx, _label in centers)),
        heavy_atom_count=int(mol.GetNumHeavyAtoms()),
    )


def defined_stereocenters(mol: Any) -> frozenset[int]:
    """Atom indices of ``mol`` with an assigned CIP label (spec 7.1)."""
    return frozenset(int(idx) for idx, _label in _CHEM.FindMolChiralCenters(mol, includeUnassigned=False))


# =========================================================================================
# Reaction-class matching (spec 7.2)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class SiteMatch:
    """A site with the size of the SMARTS pattern that produced it. The size decides
    which of two classes of the same mechanism keeps a shared site (spec 7.2)."""

    site: ReactiveSite
    pattern_atoms: int


def _compile_smarts(smarts: str, class_id: str) -> Any:
    with BlockLogs():
        pattern = Chem.MolFromSmarts(smarts)
    if pattern is None or pattern.GetNumAtoms() == 0:
        raise StructureError(f"reaction class {class_id!r}: invalid SMARTS {smarts!r}")
    return pattern


def _pattern_index_of_map(pattern: Any, map_number: int, class_id: str, smarts: str) -> int:
    hits = [atom.GetIdx() for atom in pattern.GetAtoms() if atom.GetAtomMapNum() == map_number]
    if len(hits) != 1:
        raise StructureError(
            f"reaction class {class_id!r}: SMARTS {smarts!r} must carry atom map {map_number} exactly once"
        )
    return int(hits[0])


def site_matches(mol: Any, reaction_class: ReactionClassDoc) -> tuple[SiteMatch, ...]:
    """Sites of one reaction class in ``mol`` with the size of the pattern that matched.

    Every SMARTS runs with ``GetSubstructMatches(uniquify=False)`` and matches are then
    collapsed by site atom, first pattern then first match winning (spec 7.2). RDKit's
    uniquify would drop a match whose atom set equals an earlier match, losing the second
    oxygen of a catechol (``[OX2H1:1][c:2][c][OX2H1]`` matched in reverse)."""
    smarts_list = reaction_class["smarts"]
    map_number = reaction_class["site_atom_map"]
    if not smarts_list:
        return ()
    if map_number is None:
        raise StructureError(f"reaction class {reaction_class['class_id']!r} has SMARTS but no site_atom_map")
    stereocenters = defined_stereocenters(mol) if reaction_class["requires_defined_stereocenter"] else None
    found: dict[int, SiteMatch] = {}
    for smarts in smarts_list:
        pattern = _compile_smarts(smarts, reaction_class["class_id"])
        site_index = _pattern_index_of_map(pattern, map_number, reaction_class["class_id"], smarts)
        mapped = [
            (atom.GetAtomMapNum(), atom.GetIdx()) for atom in pattern.GetAtoms() if atom.GetAtomMapNum() > 0
        ]
        matches = mol.GetSubstructMatches(pattern, uniquify=False, maxMatches=_RDKIT_DEFAULT_MAX_MATCHES)
        if len(matches) >= _RDKIT_DEFAULT_MAX_MATCHES:
            raise StructureError(
                f"reaction class {reaction_class['class_id']!r}: SMARTS {smarts!r} "
                "reached RDKit's match limit"
            )
        for match in matches:
            site_atom = int(match[site_index])
            if site_atom in found:
                continue
            if stereocenters is not None and site_atom not in stereocenters:
                continue
            mapped_atoms = tuple(sorted((int(number), int(match[idx])) for number, idx in mapped))
            found[site_atom] = SiteMatch(
                site=ReactiveSite(
                    class_id=reaction_class["class_id"], site_atom=site_atom, mapped_atoms=mapped_atoms
                ),
                pattern_atoms=int(pattern.GetNumAtoms()),
            )
    return tuple(found[atom] for atom in sorted(found))


def match_reaction_class(smiles: str, reaction_class: ReactionClassDoc) -> tuple[ReactiveSite, ...]:
    """Every match of the class's SMARTS patterns (spec 7.2). A site is identified by the
    atom carrying ``site_atom_map``; matches of several patterns at the same site atom
    collapse to one site (first pattern wins). With ``requires_defined_stereocenter`` the
    site atom must be a defined stereocenter. Sites are sorted by atom index. Classes
    without SMARTS (radiolysis) return no sites. Raises ``StructureError`` on an invalid
    SMARTS."""
    mol = mol_from_smiles(smiles)
    return tuple(match.site for match in site_matches(mol, reaction_class))


def match_all(smiles: str, classes: Sequence[ReactionClassDoc]) -> tuple[ReactiveSite, ...]:
    """``match_reaction_class`` for each class, concatenated in class order."""
    mol = mol_from_smiles(smiles)
    out: list[ReactiveSite] = []
    for reaction_class in classes:
        out.extend(match.site for match in site_matches(mol, reaction_class))
    return tuple(out)


# =========================================================================================
# Substituent fragments (spec 7.3.2)
# =========================================================================================


def aromatic_ring_of(mol: Any, ring_atom: int) -> tuple[int, ...] | None:
    """The aromatic ring (atom indices in ring order as RDKit lists them) that contains
    ``ring_atom``: the smallest fully aromatic ring holding it, first in RDKit ring order on
    ties. None when the atom is in no aromatic ring."""
    candidates = [
        tuple(int(i) for i in ring)
        for ring in mol.GetRingInfo().AtomRings()
        if ring_atom in ring and all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring)
    ]
    if not candidates:
        return None
    return min(candidates, key=len)


def is_benzene_ring(mol: Any, ring: Sequence[int]) -> bool:
    """True for a six-membered aromatic ring of carbon atoms (spec 7.3.2: benzene only)."""
    return len(ring) == 6 and all(mol.GetAtomWithIdx(i).GetAtomicNum() == 6 for i in ring)


def canonical_fragment(fragment_smiles: str) -> str:
    """Canonical form of a substituent fragment SMILES with a dummy atom ``*`` (used to
    compare molecule fragments with the lfer-constants table)."""
    return canonical_smiles(fragment_smiles)


def _ring_position(ring: Sequence[int], ring_atom: int, other: int) -> int:
    i = ring.index(ring_atom)
    j = ring.index(other)
    steps = abs(i - j)
    return min(steps, len(ring) - steps) + 1


def _side_atoms(mol: Any, start: int, blocked: frozenset[int]) -> frozenset[int]:
    seen = {start}
    stack = [start]
    while stack:
        atom = mol.GetAtomWithIdx(stack.pop())
        for neighbor in atom.GetNeighbors():
            idx = int(neighbor.GetIdx())
            if idx in blocked or idx in seen:
                continue
            seen.add(idx)
            stack.append(idx)
    return frozenset(seen)


def _fragment_smiles(mol: Any, start: int, attachments: Sequence[tuple[int, int]]) -> str:
    """Canonical SMILES of the side of ``mol`` that holds ``start`` after cutting the
    ``attachments`` bonds (inside atom, ring atom), with a plain dummy ``*`` at each cut.
    A fragment that RDKit cannot re-sanitize on its own (a piece of a fused aromatic ring)
    keeps the fragment SMILES RDKit wrote; it matches no table entry either way."""
    bonds = [int(mol.GetBondBetweenAtoms(inside, outside).GetIdx()) for inside, outside in attachments]
    with BlockLogs():
        pieces = Chem.FragmentOnBonds(mol, bonds, addDummies=True, dummyLabels=[(0, 0)] * len(bonds))
        groups = Chem.GetMolFrags(pieces, asMols=False, sanitizeFrags=False)
        side = next(group for group in groups if start in group)
        text = str(
            Chem.MolFragmentToSmiles(pieces, atomsToUse=list(side), canonical=True, isomericSmiles=True)
        )
    try:
        return canonical_smiles(text)
    except StructureError:
        return text


def attached_fragment(smiles: str, atom: int, exclude_atoms: Sequence[int]) -> str:
    """Canonical SMILES of the group that starts at ``atom``: every atom reachable from it
    without passing through ``exclude_atoms`` (the reacting group), with a dummy ``*`` at
    each cut bond. Used for the Taft substituent at ``substituent_attachment_map``
    (spec 7.3.3). A group that closes a ring through the reacting group (a lactone) carries
    two dummies and matches no table entry."""
    mol = mol_from_smiles(smiles)
    if not 0 <= atom < mol.GetNumAtoms():
        raise StructureError(f"atom {atom} is not in {smiles!r}")
    blocked = frozenset(int(i) for i in exclude_atoms) - {atom}
    side = _side_atoms(mol, atom, blocked)
    attachments = [
        (inside, int(other.GetIdx()))
        for inside in sorted(side)
        for other in mol.GetAtomWithIdx(inside).GetNeighbors()
        if int(other.GetIdx()) in blocked
    ]
    if not attachments:
        raise StructureError(f"atom {atom} of {smiles!r} is not bonded to the excluded atoms")
    return _fragment_smiles(mol, atom, attachments)


def substituent_fragments(
    smiles: str, ring_atom: int, exclude_atoms: Sequence[int]
) -> tuple[tuple[str, int], ...]:
    """Substituents on the aromatic ring that contains ``ring_atom`` (spec 7.3.2), each as
    (canonical fragment SMILES with a dummy atom ``*`` at the attachment, ring position
    relative to ``ring_atom``: 2 ortho, 3 meta, 4 para). Atoms in ``exclude_atoms`` (the
    reacting group) are not substituents. Hydrogens are not listed.

    A substituent is everything reachable from its first atom without passing through the
    ring. A substituent that bonds back to the ring (a fused ring) carries one dummy per
    attachment and is listed once, at its smallest position; it matches no table entry.
    Fragments are sorted by (position, SMILES). Raises ``StructureError`` when
    ``ring_atom`` is in no aromatic ring."""
    mol = mol_from_smiles(smiles)
    if not 0 <= ring_atom < mol.GetNumAtoms():
        raise StructureError(f"atom {ring_atom} is not in {smiles!r}")
    ring = aromatic_ring_of(mol, ring_atom)
    if ring is None:
        raise StructureError(f"atom {ring_atom} of {smiles!r} is not in an aromatic ring")
    ring_set = frozenset(ring)
    excluded = frozenset(int(i) for i in exclude_atoms)
    seen_sets: set[frozenset[int]] = set()
    out: list[tuple[str, int]] = []
    for position_atom in ring:
        atom = mol.GetAtomWithIdx(position_atom)
        for neighbor in atom.GetNeighbors():
            start = int(neighbor.GetIdx())
            if start in ring_set or start in excluded or neighbor.GetAtomicNum() == 1:
                continue
            side = _side_atoms(mol, start, ring_set)
            if side in seen_sets:
                continue
            seen_sets.add(side)
            attachments = [
                (inside, int(other.GetIdx()))
                for inside in sorted(side)
                for other in mol.GetAtomWithIdx(inside).GetNeighbors()
                if int(other.GetIdx()) in ring_set
            ]
            positions = [_ring_position(ring, ring_atom, outside) for _inside, outside in attachments]
            out.append((_fragment_smiles(mol, start, attachments), min(positions)))
    return tuple(sorted(out, key=lambda item: (item[1], item[0])))
