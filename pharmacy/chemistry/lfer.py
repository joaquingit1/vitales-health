"""Linear free-energy relationships for the computed layer (Hammett, Taft, Evans-Polanyi).

Owner: B2. Contract: docs/ENGINE-SPEC.md section 7.3. All constants come from
lfer-constants.json; none are code.
"""

import dataclasses
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pharmacy.chemistry import bde
from pharmacy.chemistry.structure import (
    aromatic_ring_of,
    attached_fragment,
    canonical_fragment,
    is_benzene_ring,
    match_reaction_class,
    mol_from_smiles,
    same_molecule,
    substituent_fragments,
)
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K, J_PER_KJ
from pharmacy.errors import ConfigReferenceError, ModelDomainError
from pharmacy.types import (
    Anchor,
    ComputedDetail,
    ComputedLayerSettings,
    ComputedMethodKind,
    EvansPolanyiEntry,
    HammettSeries,
    LferConstantsDoc,
    ReactionClassDoc,
    ReactiveSite,
    TaftSeries,
)

LN10 = math.log(10.0)

ORTHO_POSITION = 2
META_POSITION = 3
PARA_POSITION = 4
"""Ring positions relative to the reacting group (structural, spec 7.3.2)."""


def _finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ModelDomainError(f"{name} must be finite, got {value!r}")


def hammett_delta_ln_k(rho: float, sigma_site_sum: float, sigma_anchor_sum: float) -> float:
    """Delta ln k = ln(10) rho (sum sigma_site - sum sigma_anchor) (spec 7.3.2)."""
    for name, value in (
        ("rho", rho),
        ("sigma_site_sum", sigma_site_sum),
        ("sigma_anchor_sum", sigma_anchor_sum),
    ):
        _finite(name, value)
    result = LN10 * rho * (sigma_site_sum - sigma_anchor_sum)
    _finite("Hammett delta", result)
    return result


def taft_delta_ln_k(rho_star: float, delta: float, d_sigma_star: float, d_es: float) -> float:
    """Delta ln k = ln(10) (rho* d sigma* + delta d E_s) (spec 7.3.3)."""
    for name, value in (
        ("rho_star", rho_star),
        ("delta", delta),
        ("d_sigma_star", d_sigma_star),
        ("d_es", d_es),
    ):
        _finite(name, value)
    result = LN10 * (rho_star * d_sigma_star + delta * d_es)
    _finite("Taft delta", result)
    return result


def evans_polanyi_delta_ln_k(
    alpha: float, bde_site_kj_per_mol: float, bde_reference_kj_per_mol: float, reference_temperature_k: float
) -> float:
    """delta_s = -alpha (BDE_site - BDE_ref) x 1000 / (R T*) (spec 7.3.4), without the
    applicability-domain check (``evans_polanyi_site_delta`` applies it)."""
    for name, value in (
        ("alpha", alpha),
        ("bde_site", bde_site_kj_per_mol),
        ("bde_reference", bde_reference_kj_per_mol),
    ):
        _finite(name, value)
    if not math.isfinite(reference_temperature_k) or reference_temperature_k <= 0.0:
        raise ModelDomainError(f"reference temperature must be positive, got {reference_temperature_k!r}")
    result = (
        -alpha
        * (bde_site_kj_per_mol - bde_reference_kj_per_mol)
        * J_PER_KJ
        / (GAS_CONSTANT_J_PER_MOL_K * reference_temperature_k)
    )
    _finite("Evans-Polanyi delta", result)
    return result


def evans_polanyi_site_delta(
    alpha: float,
    bde_site_kj_per_mol: float,
    bde_reference_kj_per_mol: float,
    reference_temperature_k: float,
    max_abs_delta_bde_kj_per_mol: float,
) -> tuple[float, bool]:
    """delta_s of one site and whether it lies inside the applicability domain (spec 7.3.4):
    when |BDE_site - BDE_ref| exceeds ``max_abs_delta_bde_kj_per_mol`` the relation is not
    applied and delta_s = 0 (the site keeps its statistical weight in 7.3.5)."""
    if not math.isfinite(max_abs_delta_bde_kj_per_mol) or max_abs_delta_bde_kj_per_mol < 0.0:
        raise ModelDomainError(
            f"max_abs_delta_bde_kj_per_mol must be finite and >= 0, got {max_abs_delta_bde_kj_per_mol!r}"
        )
    delta = evans_polanyi_delta_ln_k(
        alpha, bde_site_kj_per_mol, bde_reference_kj_per_mol, reference_temperature_k
    )
    if abs(bde_site_kj_per_mol - bde_reference_kj_per_mol) > max_abs_delta_bde_kj_per_mol:
        return 0.0, False
    return delta, True


def combine_weighted_sites(
    delta_ln_k_per_site: tuple[float, ...], hydrogens_per_site: tuple[int, ...], reference_hydrogens: int
) -> float:
    """Evans-Polanyi sites with reaction path degeneracy (spec 7.3.5):
    ln( sum_s n_H,s exp(delta_s) / sum_r n_H,r ), with ``reference_hydrogens`` the sum over the
    reference (or anchor) molecule's sites of the class; computed with log-sum-exp."""
    if not delta_ln_k_per_site:
        raise ModelDomainError("combine_weighted_sites needs at least one site")
    if len(hydrogens_per_site) != len(delta_ln_k_per_site):
        raise ModelDomainError("one hydrogen count per site is required")
    if any(n < 1 for n in hydrogens_per_site) or reference_hydrogens < 1:
        raise ModelDomainError("hydrogen counts must be at least 1")
    for value in delta_ln_k_per_site:
        _finite("site delta", value)
    largest = max(delta_ln_k_per_site)
    total = math.fsum(
        n * math.exp(d - largest) for d, n in zip(delta_ln_k_per_site, hydrogens_per_site, strict=True)
    )
    return largest + math.log(total / reference_hydrogens)


def combine_sites(delta_ln_k_per_site: tuple[float, ...], n_anchor_sites: int) -> float:
    """Delta ln k for a molecule with several equivalent-class sites (spec 7.3.5):
    ln( sum_s exp(Delta_s) / n_anchor_sites ), computed with log-sum-exp."""
    if not delta_ln_k_per_site:
        raise ModelDomainError("combine_sites needs at least one site")
    if n_anchor_sites < 1:
        raise ModelDomainError(f"n_anchor_sites must be at least 1, got {n_anchor_sites}")
    for value in delta_ln_k_per_site:
        _finite("site delta", value)
    largest = max(delta_ln_k_per_site)
    total = math.fsum(math.exp(value - largest) for value in delta_ln_k_per_site)
    return largest + math.log(total / n_anchor_sites)


@dataclasses.dataclass(frozen=True, slots=True)
class SubstituentAssignment:
    fragment_smiles: str
    position: int
    substituent_id: str | None
    sigma: float | None


# =========================================================================================
# Series application (spec 7.3.2 and 7.3.3)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _SeriesOutcome:
    """One series at one site: its delta (None when it does not apply) and the variance
    additions and trace entries it produced."""

    kind: ComputedMethodKind
    delta: float | None
    variance_ln: float
    flags: tuple[str, ...]
    descriptors: tuple[tuple[str, float], ...]


def _site_atom_for_map(site: ReactiveSite, map_number: int) -> int | None:
    for number, atom in site.mapped_atoms:
        if number == map_number:
            return atom
    return None


def _ring_atom(smiles: str, site: ReactiveSite, map_number: int) -> int | None:
    """The aromatic ring atom attached at the atom carrying ``map_number``: the atom itself
    when aromatic, else its aromatic neighbor with the lowest index (spec 7.3.2). None when
    there is none."""
    mapped = _site_atom_for_map(site, map_number)
    if mapped is None:
        return None
    mol = mol_from_smiles(smiles)
    atom = mol.GetAtomWithIdx(mapped)
    if atom.GetIsAromatic():
        return mapped
    neighbors = sorted(int(n.GetIdx()) for n in atom.GetNeighbors() if n.GetIsAromatic())
    return neighbors[0] if neighbors else None


@dataclasses.dataclass(frozen=True, slots=True)
class _RingReading:
    """The reacting ring of one molecule for one Hammett series."""

    benzene: bool
    substituents: tuple[tuple[str, int], ...]


def _read_ring(smiles: str, site: ReactiveSite, series: HammettSeries) -> _RingReading | None:
    ring_atom = _ring_atom(smiles, site, series["ring_attachment_map"])
    if ring_atom is None:
        return None
    mol = mol_from_smiles(smiles)
    ring = aromatic_ring_of(mol, ring_atom)
    if ring is None:  # pragma: no cover - _ring_atom only returns aromatic ring atoms
        return None
    exclude = [index for _number, index in site.mapped_atoms]
    if not is_benzene_ring(mol, ring):
        return _RingReading(benzene=False, substituents=())
    return _RingReading(benzene=True, substituents=substituent_fragments(smiles, ring_atom, exclude))


def hammett_assignments(
    substituents: Sequence[tuple[str, int]], lfer: LferConstantsDoc
) -> tuple[SubstituentAssignment, ...]:
    """Sigma of each ring substituent (spec 7.3.2): meta and para use sigma_meta and
    sigma_para from the table; ortho uses 0 (``sigma`` 0.0, ``substituent_id`` as found);
    a fragment missing from the table has ``substituent_id`` None and ``sigma`` None."""
    table = {canonical_fragment(entry["fragment_smiles"]): entry for entry in lfer["hammett_substituents"]}
    out: list[SubstituentAssignment] = []
    for fragment, position in substituents:
        entry = table.get(fragment)
        if position == ORTHO_POSITION:
            out.append(
                SubstituentAssignment(fragment, position, entry["substituent_id"] if entry else None, 0.0)
            )
        elif entry is None:
            out.append(SubstituentAssignment(fragment, position, None, None))
        elif position == META_POSITION:
            out.append(
                SubstituentAssignment(
                    fragment, position, entry["substituent_id"], entry["sigma_meta"]["value"]
                )
            )
        elif position == PARA_POSITION:
            out.append(
                SubstituentAssignment(
                    fragment, position, entry["substituent_id"], entry["sigma_para"]["value"]
                )
            )
        else:  # positions beyond para do not occur on a six-membered ring
            raise ModelDomainError(f"substituent {fragment!r} at ring position {position}")
    return tuple(out)


def _sigma_sum(assignments: Sequence[SubstituentAssignment]) -> float:
    return math.fsum(a.sigma for a in assignments if a.sigma is not None)


def _residual(
    ingredient: Sequence[SubstituentAssignment], anchor: Sequence[SubstituentAssignment]
) -> list[SubstituentAssignment]:
    """Substituents that do not cancel between ingredient and anchor (same fragment at the
    same position cancels exactly whatever its constant)."""
    common = Counter((a.fragment_smiles, a.position) for a in ingredient) & Counter(
        (a.fragment_smiles, a.position) for a in anchor
    )
    out: list[SubstituentAssignment] = []
    for side in (ingredient, anchor):
        remaining = Counter(common)
        for assignment in side:
            key = (assignment.fragment_smiles, assignment.position)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            out.append(assignment)
    return out


def _hammett_outcome(
    smiles: str,
    site: ReactiveSite,
    anchor_smiles: str,
    anchor_sites: Sequence[ReactiveSite],
    series: HammettSeries,
    lfer: LferConstantsDoc,
    unparameterized_sd_log10: float,
    ortho_sd_log10: float,
    heteroaromatic_sd_log10: float,
) -> _SeriesOutcome:
    series_id = series["series_id"]
    not_applicable = _SeriesOutcome("hammett", None, 0.0, (f"series_not_applicable:{series_id}",), ())
    if series["rho"] is None:
        return _SeriesOutcome("hammett", None, 0.0, (f"no_rho:{series_id}",), ())
    ingredient_ring = _read_ring(smiles, site, series)
    anchor_ring = next(
        (reading for s in anchor_sites if (reading := _read_ring(anchor_smiles, s, series)) is not None), None
    )
    if ingredient_ring is None or anchor_ring is None:
        return not_applicable
    rho = series["rho"]["value"]
    variance = 0.0
    flags: list[str] = []
    if not (ingredient_ring.benzene and anchor_ring.benzene):
        variance += (heteroaromatic_sd_log10 * LN10) ** 2
        flags.append("heteroaromatic_ring")
    ingredient = hammett_assignments(ingredient_ring.substituents, lfer) if ingredient_ring.benzene else ()
    anchor = hammett_assignments(anchor_ring.substituents, lfer) if anchor_ring.benzene else ()
    for assignment in _residual(ingredient, anchor):
        if assignment.position == ORTHO_POSITION:
            variance += (ortho_sd_log10 * LN10) ** 2
            flags.append(f"ortho_substituent:{assignment.fragment_smiles}")
        elif assignment.sigma is None:
            variance += (unparameterized_sd_log10 * LN10) ** 2
            flags.append(f"unparameterized_substituent:{assignment.fragment_smiles}")
    sigma_site = _sigma_sum(ingredient)
    sigma_anchor = _sigma_sum(anchor)
    delta = hammett_delta_ln_k(rho, sigma_site, sigma_anchor)
    descriptors = (
        (f"{series_id}:rho", rho),
        (f"{series_id}:sigma_sum_site_{site.site_atom}", sigma_site),
        (f"{series_id}:sigma_sum_anchor", sigma_anchor),
    )
    return _SeriesOutcome("hammett", delta, variance, tuple(flags), descriptors)


def _taft_fragment(smiles: str, site: ReactiveSite, series: TaftSeries) -> str | None:
    atom = _site_atom_for_map(site, series["substituent_attachment_map"])
    if atom is None:
        return None
    mol = mol_from_smiles(smiles)
    if mol.GetAtomWithIdx(atom).GetIsAromatic():
        return None
    reacting = [index for _number, index in site.mapped_atoms if index != atom]
    return attached_fragment(smiles, atom, reacting)


def _taft_outcome(
    smiles: str,
    site: ReactiveSite,
    anchor_smiles: str,
    anchor_sites: Sequence[ReactiveSite],
    series: TaftSeries,
    lfer: LferConstantsDoc,
    unparameterized_sd_log10: float,
) -> _SeriesOutcome:
    series_id = series["series_id"]
    if series["rho_star"] is None or series["delta"] is None:
        return _SeriesOutcome("taft", None, 0.0, (f"no_rho:{series_id}",), ())
    ingredient_fragment = _taft_fragment(smiles, site, series)
    anchor_fragment = next(
        (frag for s in anchor_sites if (frag := _taft_fragment(anchor_smiles, s, series)) is not None), None
    )
    if ingredient_fragment is None or anchor_fragment is None:
        return _SeriesOutcome("taft", None, 0.0, (f"series_not_applicable:{series_id}",), ())
    table = {canonical_fragment(entry["fragment_smiles"]): entry for entry in lfer["taft_substituents"]}
    missing = [frag for frag in (ingredient_fragment, anchor_fragment) if frag not in table]
    if ingredient_fragment == anchor_fragment:
        missing = []
    if missing:
        variance = len(missing) * (unparameterized_sd_log10 * LN10) ** 2
        flags = tuple(f"unparameterized_substituent:{frag}" for frag in missing)
        return _SeriesOutcome("taft", None, variance, flags, ())
    if ingredient_fragment == anchor_fragment:
        return _SeriesOutcome(
            "taft", 0.0, 0.0, (), ((f"{series_id}:d_sigma_star", 0.0), (f"{series_id}:d_es", 0.0))
        )
    ing = table[ingredient_fragment]
    anc = table[anchor_fragment]
    d_sigma = ing["sigma_star"]["value"] - anc["sigma_star"]["value"]
    d_es = ing["steric_es"]["value"] - anc["steric_es"]["value"]
    delta = taft_delta_ln_k(series["rho_star"]["value"], series["delta"]["value"], d_sigma, d_es)
    return _SeriesOutcome(
        "taft", delta, 0.0, (), ((f"{series_id}:d_sigma_star", d_sigma), (f"{series_id}:d_es", d_es))
    )


# =========================================================================================
# Evans-Polanyi (spec 7.3.4)
# =========================================================================================


def evans_polanyi_entry(lfer: LferConstantsDoc, class_id: str) -> EvansPolanyiEntry | None:
    """The Evans-Polanyi entry of a class, or None."""
    return next((entry for entry in lfer["evans_polanyi"] if entry["class_id"] == class_id), None)


def _hydrogens(smiles: str, atom: int) -> int:
    return int(mol_from_smiles(smiles).GetAtomWithIdx(atom).GetTotalNumHs())


@dataclasses.dataclass(frozen=True, slots=True)
class _EvansPolanyiOutcome:
    delta: float
    descriptors: tuple[tuple[str, float], ...]
    cache_keys: tuple[str, ...]
    flags: tuple[str, ...]
    n_sites_in_domain: int


OUTSIDE_DOMAIN_FLAG = "outside_applicability_domain"
"""ComputedDetail flag of a pathway with a site outside the Evans-Polanyi domain (spec 7.3.4)."""


def _evans_polanyi(
    smiles: str,
    sites: Sequence[ReactiveSite],
    reference_smiles: str,
    reference_sites: Sequence[ReactiveSite],
    entry: EvansPolanyiEntry,
    settings: ComputedLayerSettings,
    reference_temperature_k: float,
    cache_dir: Path | None,
) -> _EvansPolanyiOutcome:
    """Delta ln k over the sites against the reference molecule's first site, each site
    weighted by its abstractable hydrogens against the hydrogens of all reference sites
    (spec 7.3.4, 7.3.5), with the trace descriptors, sorted cache keys and flags."""
    reference = bde.bond_dissociation_energy(
        reference_smiles, reference_sites[0].site_atom, entry["bond"], settings, cache_dir
    )
    alpha = entry["alpha"]["value"]
    limit = entry["max_abs_delta_bde_kj_per_mol"]["value"]
    reference_hydrogens = sum(_hydrogens(reference_smiles, s.site_atom) for s in reference_sites)
    deltas: list[float] = []
    hydrogens: list[int] = []
    descriptors: list[tuple[str, float]] = [
        ("evans_polanyi_alpha", alpha),
        ("max_abs_delta_bde_kj_per_mol", limit),
        ("bde_reference_kj_per_mol", reference.bde_kj_per_mol),
        ("n_reference_sites", float(len(reference_sites))),
        ("n_h_reference", float(reference_hydrogens)),
    ]
    keys = {reference.cache_key}
    n_inside = 0
    for site in sites:
        result = bde.bond_dissociation_energy(smiles, site.site_atom, entry["bond"], settings, cache_dir)
        keys.add(result.cache_key)
        delta, inside = evans_polanyi_site_delta(
            alpha, result.bde_kj_per_mol, reference.bde_kj_per_mol, reference_temperature_k, limit
        )
        n_inside += int(inside)
        n_h = _hydrogens(smiles, site.site_atom)
        deltas.append(delta)
        hydrogens.append(n_h)
        descriptors.extend(
            (
                (f"bde_site_{site.site_atom}_kj_per_mol", result.bde_kj_per_mol),
                (f"n_h_site_{site.site_atom}", float(n_h)),
                (f"delta_site_{site.site_atom}", delta),
            )
        )
    combined = combine_weighted_sites(tuple(deltas), tuple(hydrogens), reference_hydrogens)
    descriptors.append(("n_sites_in_domain", float(n_inside)))
    return _EvansPolanyiOutcome(
        delta=combined,
        descriptors=tuple(descriptors),
        cache_keys=tuple(sorted(keys)),
        flags=(OUTSIDE_DOMAIN_FLAG,) if n_inside < len(sites) else (),
        n_sites_in_domain=n_inside,
    )


def evans_polanyi_prior_shift(
    smiles: str,
    sites: tuple[ReactiveSite, ...],
    reaction_class: ReactionClassDoc,
    lfer: LferConstantsDoc,
    settings: ComputedLayerSettings,
    reference_temperature_k: float,
    cache_dir: Path | None = None,
) -> ComputedDetail | None:
    """The Evans-Polanyi shift of a class without an anchor (spec 7.3.4, 7.3.5, 9.5): delta
    ln k of the ingredient's sites against the entry's reference molecule, with the class's
    method error as its standard deviation (ln units; 0 when no site lies inside the
    applicability domain, spec 9.5). None when the class has no Evans-Polanyi entry. The
    reference BDE is that of the reference molecule's first site (lowest atom index); the
    denominator is the hydrogens of all its sites of the class."""
    entry = evans_polanyi_entry(lfer, reaction_class["class_id"])
    if entry is None:
        return None
    if not sites:
        raise ModelDomainError(f"class {reaction_class['class_id']!r}: Evans-Polanyi needs at least one site")
    reference_sites = match_reaction_class(entry["reference_smiles"], reaction_class)
    if not reference_sites:
        raise ConfigReferenceError(
            "lfer-constants",
            [f"reference {entry['reference_smiles']!r} has no site of class {reaction_class['class_id']!r}"],
        )
    outcome = _evans_polanyi(
        smiles,
        sites,
        entry["reference_smiles"],
        reference_sites,
        entry,
        settings,
        reference_temperature_k,
        cache_dir,
    )
    # Spec 9.5: s_m = 0 when no BDE-based shift was applied at any site (every site outside
    # the domain), because the hydrogen-count term of 7.3.5 is exact.
    sd = (
        reaction_class["computed_method"]["method_error_sd_log10"]["value"] * LN10
        if outcome.n_sites_in_domain
        else 0.0
    )
    return ComputedDetail(
        method="evans_polanyi_bde",
        anchor_id=None,
        delta_ln_k=outcome.delta,
        sd_ln_k=sd,
        flags=("class_prior_adjusted", *outcome.flags),
        descriptors=outcome.descriptors,
        cache_keys=outcome.cache_keys,
    )


# =========================================================================================
# Computed adjustment against an anchor (spec 7.3.1 to 7.3.5)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _SeriesRef:
    """One LFER series named by a class: exactly one of the two members is set."""

    hammett: HammettSeries | None
    taft: TaftSeries | None


def _series_for(class_id: str, series_ids: Sequence[str], lfer: LferConstantsDoc) -> list[_SeriesRef]:
    hammett = {s["series_id"]: s for s in lfer["hammett_series"]}
    taft = {s["series_id"]: s for s in lfer["taft_series"]}
    out: list[_SeriesRef] = []
    for series_id in series_ids:
        if series_id in hammett:
            ref = _SeriesRef(hammett=hammett[series_id], taft=None)
            owner = hammett[series_id]["class_id"]
        elif series_id in taft:
            ref = _SeriesRef(hammett=None, taft=taft[series_id])
            owner = taft[series_id]["class_id"]
        else:
            raise ConfigReferenceError(
                "reaction-classes", [f"{class_id!r} names unknown LFER series {series_id!r}"]
            )
        if owner != class_id:
            raise ConfigReferenceError(
                "lfer-constants", [f"series {series_id!r} belongs to {owner!r}, not {class_id!r}"]
            )
        out.append(ref)
    return out


def computed_adjustment(
    smiles: str,
    sites: tuple[ReactiveSite, ...],
    reaction_class: ReactionClassDoc,
    anchor: Anchor,
    lfer: LferConstantsDoc,
    same_molecule_sd_log10: float,
    unparameterized_sd_log10: float,
    ortho_sd_log10: float,
    heteroaromatic_sd_log10: float,
    *,
    xtb_settings: ComputedLayerSettings | None = None,
    reference_temperature_k: float | None = None,
    cache_dir: Path | None = None,
) -> ComputedDetail:
    """Delta ln k of the ingredient relative to the class anchor and its standard
    deviation (spec 7.3): same molecule as the anchor (canonical SMILES equal) gives
    Delta = 0 with ``same_molecule_sd_log10``; otherwise the class's computed method
    (Hammett or Taft series that apply to the site; Evans-Polanyi through chemistry.bde;
    anchor_only gives Delta = 0) with method error ``method_error_sd_log10`` plus the
    applicable variance additions (unparameterized substituent, ortho, heteroaromatic
    ring), all in log10 units converted to ln by multiplying by ln(10). Variances add.

    Spec 7.3.1: only the series named in ``computed_method.series_ids`` run and every
    applicable one adds its delta at a site; a site where none applies has delta 0; sites
    combine by 7.3.5 against the anchor's number of sites; substituents identical on both
    rings cancel and every other ortho or unmatched substituent on either ring adds its
    variance. Evans-Polanyi with an anchor (7.3.4) is taken against the anchor molecule's
    first site with the anchor's site hydrogens as the denominator; it needs the
    keyword-only ``xtb_settings`` and ``reference_temperature_k``."""
    method_error = reaction_class["computed_method"]["method_error_sd_log10"]["value"]
    if same_molecule(smiles, anchor["smiles"]):
        return ComputedDetail(
            method="anchor_only",
            anchor_id=anchor["anchor_id"],
            delta_ln_k=0.0,
            sd_ln_k=same_molecule_sd_log10 * LN10,
            flags=("same_molecule",),
            descriptors=(),
            cache_keys=(),
        )
    if not sites:
        raise ModelDomainError(
            f"class {reaction_class['class_id']!r}: computed layer needs at least one site"
        )
    anchor_sites = match_reaction_class(anchor["smiles"], reaction_class)
    if not anchor_sites:
        raise ConfigReferenceError(
            "reaction-classes",
            [f"anchor {anchor['anchor_id']!r} has no site of class {reaction_class['class_id']!r}"],
        )
    method_variance = (method_error * LN10) ** 2
    kind = reaction_class["computed_method"]["kind"]

    if kind == "evans_polanyi_bde":
        entry = evans_polanyi_entry(lfer, reaction_class["class_id"])
        if entry is not None:
            if xtb_settings is None or reference_temperature_k is None:
                raise ModelDomainError("Evans-Polanyi needs xtb_settings and reference_temperature_k")
            ep = _evans_polanyi(
                smiles,
                sites,
                anchor["smiles"],
                anchor_sites,
                entry,
                xtb_settings,
                reference_temperature_k,
                cache_dir,
            )
            return ComputedDetail(
                method="evans_polanyi_bde",
                anchor_id=anchor["anchor_id"],
                delta_ln_k=ep.delta,
                sd_ln_k=math.sqrt(method_variance),
                flags=ep.flags,
                descriptors=ep.descriptors,
                cache_keys=ep.cache_keys,
            )

    series_list = _series_for(
        reaction_class["class_id"], reaction_class["computed_method"]["series_ids"], lfer
    )
    deltas: list[float] = []
    variance = method_variance
    flags: list[str] = []
    descriptors_out: list[tuple[str, float]] = []
    applied: set[ComputedMethodKind] = set()
    for site in sites:
        site_delta = 0.0
        for ref in series_list:
            if ref.hammett is not None:
                outcome = _hammett_outcome(
                    smiles,
                    site,
                    anchor["smiles"],
                    anchor_sites,
                    ref.hammett,
                    lfer,
                    unparameterized_sd_log10,
                    ortho_sd_log10,
                    heteroaromatic_sd_log10,
                )
            elif ref.taft is not None:
                outcome = _taft_outcome(
                    smiles, site, anchor["smiles"], anchor_sites, ref.taft, lfer, unparameterized_sd_log10
                )
            else:  # pragma: no cover - _series_for always sets one member
                raise ModelDomainError("empty LFER series reference")
            variance += outcome.variance_ln
            flags.extend(outcome.flags)
            descriptors_out.extend(outcome.descriptors)
            if outcome.delta is not None:
                site_delta += outcome.delta
                applied.add(outcome.kind)
        deltas.append(site_delta)
    delta = combine_sites(tuple(deltas), len(anchor_sites))
    if len(applied) == 1:
        method: ComputedMethodKind = next(iter(applied))
    elif applied:
        method = kind if kind in applied else "hammett"
    else:
        method = "anchor_only"
    return ComputedDetail(
        method=method,
        anchor_id=anchor["anchor_id"],
        delta_ln_k=delta,
        sd_ln_k=math.sqrt(variance),
        flags=tuple(dict.fromkeys(flags)),
        descriptors=tuple(descriptors_out),
        cache_keys=(),
    )
