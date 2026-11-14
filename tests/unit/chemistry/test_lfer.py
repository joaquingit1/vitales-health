"""Unit tests of pharmacy.chemistry.lfer (spec 7.3). Hand values are from mpmath at 30
digits with the constants of config/pharmacy/lfer-constants.json written out here:
rho(hammett_benzoate_alkaline) = 2.494, rho*(taft_ester_acyl) = 2.48, delta = 1.0,
sigma_para(nitro) = 0.778, sigma_para(chloro) = 0.227, sigma_meta(methyl) = -0.069,
sigma_para(ethoxycarbonyl) = 0.45, sigma*(ethyl) = -0.1, E_s(ethyl) = -0.07,
sigma*(tert-butyl) = -0.3, E_s(tert-butyl) = -1.54, alpha = 0.5."""

import copy
import math
from typing import Any

import pytest

from pharmacy.chemistry import bde
from pharmacy.chemistry import lfer as lf
from pharmacy.chemistry.structure import match_reaction_class, mol_from_smiles
from pharmacy.config import ConfigBundle
from pharmacy.constants import GAS_CONSTANT_J_PER_MOL_K
from pharmacy.errors import ConfigReferenceError, ModelDomainError
from pharmacy.types import Anchor, ComputedDetail, ComputedLayerSettings, ReactionClassDoc
from tests.unit.chemistry.support import reaction_class

LN10 = 2.30258509299404568
T_REF = 298.15
ACETAMINOPHEN = "CC(=O)NC1=CC=C(C=C1)O"
PROMETHAZINE = "CC(CN1C2=CC=CC=C2SC3=CC=CC=C31)N(C)C"
EPINEPHRINE = "CNC[C@@H](C1=CC(=C(C=C1)O)O)O"


# --- the four formulas -------------------------------------------------------------------------
def test_hammett_hand_values() -> None:
    assert lf.hammett_delta_ln_k(2.494, 0.778, 0.0) == pytest.approx(4.46777953865932265, rel=1e-14)
    assert lf.hammett_delta_ln_k(2.494, 0.227, -0.069) == pytest.approx(1.69982357769043638, rel=1e-14)
    assert lf.hammett_delta_ln_k(2.494, 0.3, 0.3) == 0.0
    with pytest.raises(ModelDomainError):
        lf.hammett_delta_ln_k(math.nan, 0.0, 0.0)


def test_taft_hand_values() -> None:
    assert lf.taft_delta_ln_k(2.48, 1.0, -0.1, -0.07) == pytest.approx(-0.732222059572106528, rel=1e-14)
    assert lf.taft_delta_ln_k(2.48, 1.0, -0.3, -1.54) == pytest.approx(-5.25910435239840034, rel=1e-14)
    with pytest.raises(ModelDomainError):
        lf.taft_delta_ln_k(2.48, 1.0, math.inf, 0.0)


def test_evans_polanyi_hand_value() -> None:
    # -0.5 * (-10 kJ/mol) * 1000 / (R * 298.15 K)
    assert lf.evans_polanyi_delta_ln_k(0.5, 350.0, 360.0, 298.15) == pytest.approx(
        2.01697727725517427, rel=1e-13
    )
    assert lf.evans_polanyi_delta_ln_k(0.5, 360.0, 350.0, 298.15) == pytest.approx(
        -2.01697727725517427, rel=1e-13
    )
    for bad_t in (0.0, -1.0, math.nan):
        with pytest.raises(ModelDomainError):
            lf.evans_polanyi_delta_ln_k(0.5, 350.0, 360.0, bad_t)
    with pytest.raises(ModelDomainError):
        lf.evans_polanyi_delta_ln_k(0.5, math.nan, 360.0, 298.15)


def test_evans_polanyi_site_delta_domain() -> None:
    """Spec 7.3.4: inside |BDE_site - BDE_ref| <= limit the relation applies; beyond it the
    site's delta is 0 and it is reported as outside the domain."""
    delta, inside = lf.evans_polanyi_site_delta(0.5, 380.0, 350.0, 298.15, 30.0)
    assert inside
    assert delta == pytest.approx(-6.05093183176552280, rel=1e-13)
    assert delta == lf.evans_polanyi_delta_ln_k(0.5, 380.0, 350.0, 298.15)
    assert lf.evans_polanyi_site_delta(0.5, 320.0, 350.0, 298.15, 30.0)[1]
    assert lf.evans_polanyi_site_delta(0.5, 380.5, 350.0, 298.15, 30.0) == (0.0, False)
    assert lf.evans_polanyi_site_delta(0.5, 319.5, 350.0, 298.15, 30.0) == (0.0, False)
    assert lf.evans_polanyi_site_delta(0.5, 350.0, 350.0, 298.15, 0.0) == (0.0, True)
    for bad in (-1.0, math.nan, math.inf):
        with pytest.raises(ModelDomainError, match="max_abs_delta"):
            lf.evans_polanyi_site_delta(0.5, 350.0, 350.0, 298.15, bad)
    with pytest.raises(ModelDomainError):
        lf.evans_polanyi_site_delta(0.5, math.nan, 350.0, 298.15, 30.0)


def test_combine_weighted_sites_hand_values() -> None:
    """Spec 7.3.5 path degeneracy: ln(sum_s n_H,s exp(delta_s) / sum_r n_H,r)."""
    assert lf.combine_weighted_sites((1.0, 2.0, -3.0), (1, 3, 3), 9) == pytest.approx(
        1.02304273218220378, rel=1e-14
    )
    assert lf.combine_weighted_sites((0.5, 0.5, 0.0), (1, 1, 1), 2) == pytest.approx(
        0.764872907387088400, rel=1e-14
    )
    assert lf.combine_weighted_sites((0.0,), (1,), 1) == 0.0
    assert lf.combine_weighted_sites((0.0, 0.0), (3, 3), 9) == pytest.approx(math.log(6.0 / 9.0), rel=1e-15)
    assert lf.combine_weighted_sites((800.0, 800.0), (3, 3), 6) == pytest.approx(800.0, rel=1e-15)
    for bad in (
        ((), (), 1),
        ((0.0,), (1, 1), 1),
        ((0.0,), (0,), 1),
        ((0.0,), (1,), 0),
        ((math.nan,), (1,), 1),
    ):
        with pytest.raises(ModelDomainError):
            lf.combine_weighted_sites(*bad)


def test_combine_sites_hand_values() -> None:
    assert lf.combine_sites((1.0, 2.0, -3.0), 2) == pytest.approx(1.62502824868750877, rel=1e-14)
    assert lf.combine_sites((0.0,), 1) == 0.0
    assert lf.combine_sites((0.0, 0.0), 1) == pytest.approx(math.log(2.0), rel=1e-15)
    # Large deltas do not overflow (log-sum-exp).
    assert lf.combine_sites((800.0, 800.0), 2) == pytest.approx(800.0, rel=1e-15)
    for bad in [((), 1), ((0.0,), 0), ((math.nan,), 1)]:
        with pytest.raises(ModelDomainError):
            lf.combine_sites(*bad)


# --- substituent constants --------------------------------------------------------------------------
def test_hammett_assignments(bundle: ConfigBundle) -> None:
    out = lf.hammett_assignments(
        [("*Cl", 2), ("*Cl", 3), ("*Cl", 4), ("*C(C)=O", 4), ("*[N+](=O)[O-]", 2)], bundle.lfer
    )
    assert out == (
        lf.SubstituentAssignment("*Cl", 2, "chloro", 0.0),
        lf.SubstituentAssignment("*Cl", 3, "chloro", 0.373),
        lf.SubstituentAssignment("*Cl", 4, "chloro", 0.227),
        lf.SubstituentAssignment("*C(C)=O", 4, None, None),
        lf.SubstituentAssignment("*[N+](=O)[O-]", 2, "nitro", 0.0),
    )
    assert lf.hammett_assignments([("*C(C)=O", 2)], bundle.lfer) == (
        lf.SubstituentAssignment("*C(C)=O", 2, None, 0.0),
    )
    with pytest.raises(ModelDomainError):
        lf.hammett_assignments([("*Cl", 5)], bundle.lfer)


def test_evans_polanyi_entry(bundle: ConfigBundle) -> None:
    entry = lf.evans_polanyi_entry(bundle.lfer, "phenol_autoxidation")
    assert entry is not None
    assert entry["reference_smiles"] == "Oc1ccccc1"
    assert lf.evans_polanyi_entry(bundle.lfer, "ester_hydrolysis") is None


# --- computed_adjustment: Hammett and Taft -------------------------------------------------------
def _anchor(bundle: ConfigBundle, class_id: str, smiles: str) -> Anchor:
    base = copy.deepcopy(reaction_class(bundle, "ester_hydrolysis")["anchors"][0])
    base["anchor_id"] = f"test_{class_id}"
    base["smiles"] = smiles
    return base


def _with_series(bundle: ConfigBundle, series_ids: list[str]) -> ReactionClassDoc:
    cls = copy.deepcopy(reaction_class(bundle, "ester_hydrolysis"))
    cls["computed_method"]["series_ids"] = series_ids
    return cls


def _adjust(
    bundle: ConfigBundle, smiles: str, anchor_smiles: str, cls: ReactionClassDoc | None = None
) -> ComputedDetail:
    cls = cls or reaction_class(bundle, "ester_hydrolysis")
    settings = bundle.settings["computed_layer"]
    return lf.computed_adjustment(
        smiles,
        match_reaction_class(smiles, cls),
        cls,
        _anchor(bundle, cls["class_id"], anchor_smiles),
        bundle.lfer,
        settings["same_molecule_sd_log10"]["value"],
        settings["unparameterized_substituent_sd_log10"]["value"],
        settings["ortho_substituent_sd_log10"]["value"],
        settings["heteroaromatic_ring_sd_log10"]["value"],
    )


def test_same_molecule_is_anchor_only(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "OC(=O)c1ccccc1OC(C)=O", "CC(=O)OC1=CC=CC=C1C(=O)O")
    assert detail.method == "anchor_only"
    assert detail.delta_ln_k == 0.0
    assert detail.sd_ln_k == pytest.approx(0.3 * LN10, rel=1e-15)
    assert detail.flags == ("same_molecule",)
    assert detail.cache_keys == ()


def test_hammett_para_nitro(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccc(cc1)[N+](=O)[O-]", "CCOC(=O)c1ccccc1")
    assert detail.method == "hammett"
    assert detail.anchor_id == "test_ester_hydrolysis"
    assert detail.delta_ln_k == pytest.approx(4.46777953865932265, rel=1e-13)
    assert detail.sd_ln_k == pytest.approx(1.0 * LN10, rel=1e-14)  # method error only
    assert detail.flags == ()
    assert dict(detail.descriptors)["hammett_benzoate_alkaline:rho"] == 2.494


def test_hammett_both_rings_substituted(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccc(Cl)cc1", "CCOC(=O)c1cccc(C)c1")
    assert detail.delta_ln_k == pytest.approx(1.69982357769043638, rel=1e-13)
    assert detail.sd_ln_k == pytest.approx(LN10, rel=1e-14)


def test_hammett_identical_substituents_cancel(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccc([N+](=O)[O-])c(C(C)=O)c1", "CCOC(=O)c1cccc(C(C)=O)c1")
    # The unparameterized meta acetyl is on both rings and cancels: no variance addition.
    assert detail.delta_ln_k == pytest.approx(4.46777953865932265, rel=1e-13)
    assert detail.sd_ln_k == pytest.approx(LN10, rel=1e-14)
    assert detail.flags == ()


def test_hammett_ortho_and_unparameterized_add_variance(bundle: ConfigBundle) -> None:
    ortho = _adjust(bundle, "CCOC(=O)c1ccccc1Cl", "CCOC(=O)c1ccccc1")
    assert ortho.delta_ln_k == 0.0
    assert ortho.sd_ln_k == pytest.approx(2.57436839595618046, rel=1e-13)  # ln10 sqrt(1 + 0.25)
    assert ortho.flags == ("ortho_substituent:*Cl",)
    unknown = _adjust(bundle, "CCOC(=O)c1ccc(C(C)=O)cc1", "CCOC(=O)c1ccccc1")
    assert unknown.delta_ln_k == 0.0
    assert unknown.sd_ln_k == pytest.approx(2.57436839595618046, rel=1e-13)
    assert unknown.flags == ("unparameterized_substituent:*C(C)=O",)
    # An ortho substituent on the anchor ring counts too (spec 7.3.1).
    anchor_ortho = _adjust(bundle, "CCOC(=O)c1ccccc1", "CCOC(=O)c1ccccc1C")
    assert anchor_ortho.sd_ln_k == pytest.approx(2.57436839595618046, rel=1e-13)


def test_hammett_heteroaromatic_ring(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccncc1", "CCOC(=O)c1ccc(Cl)cc1")
    # Pyridine ring: no sigma on the ingredient side, the anchor's chloro still counts.
    assert detail.delta_ln_k == pytest.approx(LN10 * 2.494 * (0.0 - 0.227), rel=1e-13)
    assert detail.sd_ln_k == pytest.approx(2.57436839595618046, rel=1e-13)
    assert detail.flags == ("heteroaromatic_ring",)


def test_hammett_two_sites_combine(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccc(cc1)C(=O)OCC", "CCOC(=O)c1ccccc1")
    # Two sites, each with a para ethoxycarbonyl; one anchor site: d + ln 2.
    assert detail.delta_ln_k == pytest.approx(3.27733843042716278, rel=1e-13)


def test_hammett_not_applicable_without_ring(bundle: ConfigBundle) -> None:
    """The formulary case: ascorbic acid lactone against the aspirin anchor (acetyl acyl
    carbon, no ring at map 1) gives delta 0 with the method error only."""
    detail = _adjust(bundle, "C([C@@H]([C@@H]1C(=C(C(=O)O1)O)O)O)O", "CC(=O)OC1=CC=CC=C1C(=O)O")
    assert detail.method == "anchor_only"
    assert detail.delta_ln_k == 0.0
    assert detail.sd_ln_k == pytest.approx(LN10, rel=1e-14)
    assert detail.flags == ("series_not_applicable:hammett_benzoate_alkaline",)


def test_series_without_rho(bundle: ConfigBundle) -> None:
    detail = _adjust(
        bundle, "CC(=O)Oc1ccc(Cl)cc1", "CC(=O)Oc1ccccc1", _with_series(bundle, ["hammett_phenyl_ester"])
    )
    assert detail.method == "anchor_only"
    assert detail.delta_ln_k == 0.0
    assert detail.flags == ("no_rho:hammett_phenyl_ester",)


def test_taft_hand_value_in_adjustment(bundle: ConfigBundle) -> None:
    cls = _with_series(bundle, ["taft_ester_acyl"])
    detail = _adjust(bundle, "CCC(=O)OCC", "CC(=O)OCC", cls)
    assert detail.method == "taft"
    assert detail.delta_ln_k == pytest.approx(-0.732222059572106528, rel=1e-13)
    assert dict(detail.descriptors)["taft_ester_acyl:d_sigma_star"] == pytest.approx(-0.1)
    tbu = _adjust(bundle, "CC(C)(C)C(=O)OCC", "CC(=O)OCC", cls)
    assert tbu.delta_ln_k == pytest.approx(-5.25910435239840034, rel=1e-13)


def test_taft_identical_and_unparameterized_fragments(bundle: ConfigBundle) -> None:
    cls = _with_series(bundle, ["taft_ester_acyl"])
    same_group = _adjust(bundle, "CCC(=O)OC(C)C", "CCC(=O)OCC", cls)
    assert same_group.method == "taft"
    assert same_group.delta_ln_k == 0.0
    assert same_group.sd_ln_k == pytest.approx(LN10, rel=1e-14)
    unknown = _adjust(bundle, "O=C(OCC)C1CCCCC1", "CC(=O)OCC", cls)
    assert unknown.method == "anchor_only"
    assert unknown.delta_ln_k == 0.0
    assert unknown.sd_ln_k == pytest.approx(2.57436839595618046, rel=1e-13)
    assert unknown.flags == ("unparameterized_substituent:*C1CCCCC1",)
    # A cyclohexyl group on both sides is identical and cancels without a variance.
    both = _adjust(bundle, "O=C(OC)C1CCCCC1", "O=C(OCC)C1CCCCC1", cls)
    assert both.delta_ln_k == 0.0
    assert both.sd_ln_k == pytest.approx(LN10, rel=1e-14)


def test_taft_not_applicable_on_aromatic_acyl(bundle: ConfigBundle) -> None:
    detail = _adjust(bundle, "CCOC(=O)c1ccccc1", "CC(=O)OCC", _with_series(bundle, ["taft_ester_acyl"]))
    assert detail.flags == ("series_not_applicable:taft_ester_acyl",)
    assert detail.delta_ln_k == 0.0


def test_hammett_and_taft_on_different_sites(bundle: ConfigBundle) -> None:
    cls = _with_series(bundle, ["hammett_benzoate_alkaline", "taft_ester_acyl"])
    detail = _adjust(bundle, "CCC(=O)OCCOC(=O)c1ccc(cc1)[N+](=O)[O-]", "CC(=O)OCCOC(=O)c1ccccc1", cls)
    # ln((exp(taft ethyl vs methyl) + exp(hammett para nitro)) / 2 anchor sites)
    assert detail.delta_ln_k == pytest.approx(3.78013375324066635, rel=1e-13)
    assert detail.method == "hammett"  # the class's own kind when both applied
    assert set(detail.flags) == {
        "series_not_applicable:hammett_benzoate_alkaline",
        "series_not_applicable:taft_ester_acyl",
    }


def test_series_reference_errors(bundle: ConfigBundle) -> None:
    with pytest.raises(ConfigReferenceError, match="unknown LFER series"):
        _adjust(bundle, "CCC(=O)OCC", "CC(=O)OCC", _with_series(bundle, ["no_such_series"]))
    other = copy.deepcopy(reaction_class(bundle, "amide_hydrolysis"))
    other["computed_method"]["kind"] = "hammett"
    other["computed_method"]["series_ids"] = ["hammett_benzoate_alkaline"]
    with pytest.raises(ConfigReferenceError, match="belongs to"):
        _adjust(bundle, "CCNC(=O)c1ccccc1", "CC(=O)NC1=CC=C(C=C1)O", other)


def test_adjustment_errors(bundle: ConfigBundle) -> None:
    cls = reaction_class(bundle, "ester_hydrolysis")
    settings = bundle.settings["computed_layer"]
    args = (
        bundle.lfer,
        settings["same_molecule_sd_log10"]["value"],
        settings["unparameterized_substituent_sd_log10"]["value"],
        settings["ortho_substituent_sd_log10"]["value"],
        settings["heteroaromatic_ring_sd_log10"]["value"],
    )
    with pytest.raises(ModelDomainError, match="at least one site"):
        lf.computed_adjustment("CCC(=O)OCC", (), cls, _anchor(bundle, "x", "CC(=O)OCC"), *args)
    sites = match_reaction_class("CCC(=O)OCC", cls)
    with pytest.raises(ConfigReferenceError, match="no site"):
        lf.computed_adjustment("CCC(=O)OCC", sites, cls, _anchor(bundle, "x", "CCO"), *args)


# --- Evans-Polanyi through the committed BDE cache ------------------------------------------------
def _xtb(bundle: ConfigBundle) -> ComputedLayerSettings:
    return bundle.settings["computed_layer"]


def _bde(bundle: ConfigBundle, smiles: str, atom: int, bond: str) -> float:
    return bde.bond_dissociation_energy(smiles, atom, bond, _xtb(bundle)).bde_kj_per_mol


def _ep(alpha: float, site: float, ref: float) -> float:
    return -alpha * (site - ref) * 1000.0 / (GAS_CONSTANT_J_PER_MOL_K * T_REF)


def _hydrogens(smiles: str, atom: int) -> int:
    return int(mol_from_smiles(smiles).GetAtomWithIdx(atom).GetTotalNumHs())


def _weighted(deltas: list[float], hydrogens: list[int], reference_hydrogens: int) -> float:
    return math.log(
        math.fsum(n * math.exp(d) for d, n in zip(deltas, hydrogens, strict=True)) / reference_hydrogens
    )


def _limit(bundle: ConfigBundle, class_id: str) -> float:
    entry = lf.evans_polanyi_entry(bundle.lfer, class_id)
    assert entry is not None
    return entry["max_abs_delta_bde_kj_per_mol"]["value"]


def _domain_delta(bundle: ConfigBundle, class_id: str, site: float, ref: float) -> tuple[float, bool]:
    if abs(site - ref) > _limit(bundle, class_id):
        return 0.0, False
    return _ep(0.5, site, ref), True


def test_evans_polanyi_prior_shift_single_site(bundle: ConfigBundle) -> None:
    cls = reaction_class(bundle, "phenol_autoxidation")
    sites = match_reaction_class(ACETAMINOPHEN, cls)
    detail = lf.evans_polanyi_prior_shift(ACETAMINOPHEN, sites, cls, bundle.lfer, _xtb(bundle), T_REF)
    assert detail is not None
    site = _bde(bundle, ACETAMINOPHEN, 10, "O-H")
    ref = _bde(bundle, "Oc1ccccc1", 0, "O-H")
    assert abs(site - ref) <= _limit(bundle, "phenol_autoxidation")
    assert detail.delta_ln_k == pytest.approx(_ep(0.5, site, ref), rel=1e-13)
    assert detail.method == "evans_polanyi_bde"
    assert detail.anchor_id is None
    assert detail.sd_ln_k == pytest.approx(LN10, rel=1e-14)
    assert detail.flags == ("class_prior_adjusted",)
    descriptors = dict(detail.descriptors)
    assert descriptors["evans_polanyi_alpha"] == 0.5
    assert descriptors["max_abs_delta_bde_kj_per_mol"] == 30.0
    assert descriptors["n_reference_sites"] == 1.0
    assert descriptors["n_h_reference"] == 1.0
    assert descriptors["n_h_site_10"] == 1.0
    assert descriptors["bde_site_10_kj_per_mol"] == site
    assert descriptors["bde_reference_kj_per_mol"] == ref
    assert descriptors["n_sites_in_domain"] == 1.0
    assert len(detail.cache_keys) == 2
    assert list(detail.cache_keys) == sorted(detail.cache_keys)


def test_evans_polanyi_prior_shift_weights_hydrogens(bundle: ConfigBundle) -> None:
    """Promethazine against trimethylamine (9 alpha hydrogens): the methine (1 H) and the
    two N-methyls (3 H each) weighted by their hydrogens (spec 7.3.5)."""
    cls = reaction_class(bundle, "amine_alpha_oxidation")
    sites = match_reaction_class(PROMETHAZINE, cls)
    assert [s.site_atom for s in sites] == [1, 18, 19]
    assert [_hydrogens(PROMETHAZINE, a) for a in (1, 18, 19)] == [1, 3, 3]
    detail = lf.evans_polanyi_prior_shift(PROMETHAZINE, sites, cls, bundle.lfer, _xtb(bundle), T_REF)
    assert detail is not None
    ref = _bde(bundle, "CN(C)C", 0, "C-H")
    outcomes = [
        _domain_delta(bundle, "amine_alpha_oxidation", _bde(bundle, PROMETHAZINE, a, "C-H"), ref)
        for a in (1, 18, 19)
    ]
    expected = _weighted([d for d, _ in outcomes], [1, 3, 3], 9)
    assert detail.delta_ln_k == pytest.approx(expected, rel=1e-13)
    descriptors = dict(detail.descriptors)
    assert descriptors["n_reference_sites"] == 3.0
    assert descriptors["n_h_reference"] == 9.0
    assert [descriptors[f"delta_site_{a}"] for a in (1, 18, 19)] == [d for d, _ in outcomes]
    outside = not all(inside for _, inside in outcomes)
    assert ("outside_applicability_domain" in detail.flags) == outside
    # The two N-methyls are symmetry equivalent: one key for them, one for the methine and
    # one for trimethylamine.
    assert len(detail.cache_keys) == 3


def test_evans_polanyi_outside_the_domain(bundle: ConfigBundle) -> None:
    """A site beyond ``max_abs_delta_bde_kj_per_mol`` keeps its statistical weight with
    delta_s = 0 and flags the pathway (spec 7.3.4)."""
    cls = reaction_class(bundle, "phenol_autoxidation")
    sites = match_reaction_class(ACETAMINOPHEN, cls)
    site = _bde(bundle, ACETAMINOPHEN, 10, "O-H")
    ref = _bde(bundle, "Oc1ccccc1", 0, "O-H")
    lfer: Any = copy.deepcopy(bundle.lfer)
    for entry in lfer["evans_polanyi"]:
        if entry["class_id"] == "phenol_autoxidation":
            entry["max_abs_delta_bde_kj_per_mol"]["value"] = abs(site - ref) / 2.0
    detail = lf.evans_polanyi_prior_shift(ACETAMINOPHEN, sites, cls, lfer, _xtb(bundle), T_REF)
    assert detail is not None
    assert detail.delta_ln_k == 0.0
    assert detail.flags == ("class_prior_adjusted", "outside_applicability_domain")
    assert dict(detail.descriptors)["delta_site_10"] == 0.0
    # No BDE-based shift at any site: only the exact hydrogen-count term, no method error
    # (spec 9.5).
    assert detail.sd_ln_k == 0.0
    assert dict(detail.descriptors)["n_sites_in_domain"] == 0.0


def test_evans_polanyi_prior_shift_none_and_errors(bundle: ConfigBundle) -> None:
    ester = reaction_class(bundle, "ester_hydrolysis")
    assert lf.evans_polanyi_prior_shift("CCOC(C)=O", (), ester, bundle.lfer, _xtb(bundle), T_REF) is None
    cls = reaction_class(bundle, "phenol_autoxidation")
    with pytest.raises(ModelDomainError, match="at least one site"):
        lf.evans_polanyi_prior_shift(ACETAMINOPHEN, (), cls, bundle.lfer, _xtb(bundle), T_REF)
    lfer: Any = copy.deepcopy(bundle.lfer)
    for entry in lfer["evans_polanyi"]:
        if entry["class_id"] == "phenol_autoxidation":
            entry["reference_smiles"] = "CCO"
    sites = match_reaction_class(ACETAMINOPHEN, cls)
    with pytest.raises(ConfigReferenceError, match="no site"):
        lf.evans_polanyi_prior_shift(ACETAMINOPHEN, sites, cls, lfer, _xtb(bundle), T_REF)


def test_evans_polanyi_against_an_anchor(bundle: ConfigBundle) -> None:
    """With an anchor the reference is the anchor molecule (spec 7.3.4): a phenol anchor
    reproduces the class-prior shift of acetaminophen, as a computed-layer delta."""
    cls = copy.deepcopy(reaction_class(bundle, "phenol_autoxidation"))
    anchor = _anchor(bundle, "phenol_autoxidation", "Oc1ccccc1")
    settings = _xtb(bundle)
    sites = match_reaction_class(ACETAMINOPHEN, cls)
    args = (bundle.lfer, 0.3, 0.5, 0.5, 0.5)
    detail = lf.computed_adjustment(
        ACETAMINOPHEN, sites, cls, anchor, *args, xtb_settings=settings, reference_temperature_k=T_REF
    )
    shift = lf.evans_polanyi_prior_shift(ACETAMINOPHEN, sites, cls, bundle.lfer, settings, T_REF)
    assert shift is not None
    assert detail.method == "evans_polanyi_bde"
    assert detail.anchor_id == "test_phenol_autoxidation"
    assert detail.delta_ln_k == pytest.approx(shift.delta_ln_k, rel=1e-15)
    assert detail.sd_ln_k == pytest.approx(LN10, rel=1e-14)
    assert detail.flags == ()
    assert detail.cache_keys == shift.cache_keys
    with pytest.raises(ModelDomainError, match="xtb_settings"):
        lf.computed_adjustment(ACETAMINOPHEN, sites, cls, anchor, *args)


def test_evans_polanyi_anchor_hydrogens_form_the_denominator(bundle: ConfigBundle) -> None:
    """A catechol anchor for the phenol class: its two O-H sites (1 H each) are the
    denominator, so a single phenol site gets ln(1/2) on top of its delta."""
    cls = copy.deepcopy(reaction_class(bundle, "phenol_autoxidation"))
    anchor = _anchor(bundle, "phenol_autoxidation", "Oc1ccccc1O")
    sites = match_reaction_class(ACETAMINOPHEN, cls)
    detail = lf.computed_adjustment(
        ACETAMINOPHEN,
        sites,
        cls,
        anchor,
        bundle.lfer,
        0.3,
        0.5,
        0.5,
        0.5,
        xtb_settings=_xtb(bundle),
        reference_temperature_k=T_REF,
    )
    ref = _bde(bundle, "Oc1ccccc1O", 0, "O-H")
    delta, inside = _domain_delta(bundle, "phenol_autoxidation", _bde(bundle, ACETAMINOPHEN, 10, "O-H"), ref)
    assert detail.delta_ln_k == pytest.approx(delta + math.log(0.5), rel=1e-13)
    assert dict(detail.descriptors)["n_h_reference"] == 2.0
    assert detail.flags == (() if inside else ("outside_applicability_domain",))


def test_evans_polanyi_kind_without_entry_falls_back_to_series(bundle: ConfigBundle) -> None:
    cls = copy.deepcopy(reaction_class(bundle, "thioether_s_oxidation"))
    cls["computed_method"]["kind"] = "evans_polanyi_bde"
    smiles = "CSc1ccccc1"
    detail = lf.computed_adjustment(
        smiles,
        match_reaction_class(smiles, cls),
        cls,
        _anchor(bundle, "x", "CSC"),
        bundle.lfer,
        0.3,
        0.5,
        0.5,
        0.5,
    )
    assert detail.method == "anchor_only"
    assert detail.delta_ln_k == 0.0


def test_catechol_shift_uses_both_oxygens(bundle: ConfigBundle) -> None:
    cls = reaction_class(bundle, "catechol_autoxidation")
    sites = match_reaction_class(EPINEPHRINE, cls)
    detail = lf.evans_polanyi_prior_shift(EPINEPHRINE, sites, cls, bundle.lfer, _xtb(bundle), T_REF)
    assert detail is not None
    ref = _bde(bundle, "Oc1ccccc1O", 0, "O-H")
    outcomes = [
        _domain_delta(bundle, "catechol_autoxidation", _bde(bundle, EPINEPHRINE, a, "O-H"), ref)
        for a in (10, 11)
    ]
    descriptors = dict(detail.descriptors)
    assert descriptors["n_reference_sites"] == 2.0
    assert descriptors["n_h_reference"] == 2.0
    expected = _weighted([d for d, _ in outcomes], [1, 1], 2)
    assert detail.delta_ln_k == pytest.approx(expected, rel=1e-13)


# --- series edge cases with edited constant tables -------------------------------------------------
def _lfer_with(bundle: ConfigBundle, edit: Any) -> Any:
    lfer: Any = copy.deepcopy(bundle.lfer)
    edit(lfer)
    return lfer


def _adjust_with(
    bundle: ConfigBundle, lfer: Any, smiles: str, anchor_smiles: str, cls: ReactionClassDoc
) -> ComputedDetail:
    return lf.computed_adjustment(
        smiles,
        match_reaction_class(smiles, cls),
        cls,
        _anchor(bundle, cls["class_id"], anchor_smiles),
        lfer,
        0.3,
        0.5,
        0.5,
        0.5,
    )


def test_hammett_ring_on_aromatic_mapped_atom(bundle: ConfigBundle) -> None:
    """A series whose mapped atom is itself aromatic (map 4, the aryl carbon of an aryl
    ester) reads the ring containing that atom."""

    def add(lfer: Any) -> None:
        lfer["hammett_series"].append(
            {**copy.deepcopy(lfer["hammett_series"][0]), "series_id": "test_aryl", "ring_attachment_map": 4}
        )
        lfer["hammett_series"][-1]["rho"]["value"] = 1.0

    lfer = _lfer_with(bundle, add)
    detail = _adjust_with(
        bundle, lfer, "CC(=O)Oc1ccc(Cl)cc1", "CC(=O)Oc1ccccc1", _with_series(bundle, ["test_aryl"])
    )
    assert detail.delta_ln_k == pytest.approx(LN10 * 0.227, rel=1e-14)


def test_series_map_missing_from_site(bundle: ConfigBundle) -> None:
    def add(lfer: Any) -> None:
        lfer["hammett_series"].append(
            {**copy.deepcopy(lfer["hammett_series"][0]), "series_id": "test_nomap", "ring_attachment_map": 9}
        )
        lfer["taft_series"].append(
            {
                **copy.deepcopy(lfer["taft_series"][0]),
                "series_id": "test_taft_nomap",
                "substituent_attachment_map": 9,
            }
        )

    lfer = _lfer_with(bundle, add)
    cls = _with_series(bundle, ["test_nomap", "test_taft_nomap"])
    detail = _adjust_with(bundle, lfer, "CCOC(=O)c1ccccc1", "CCOC(=O)c1ccc(Cl)cc1", cls)
    assert detail.delta_ln_k == 0.0
    assert set(detail.flags) == {"series_not_applicable:test_nomap", "series_not_applicable:test_taft_nomap"}


def test_taft_series_without_rho_star(bundle: ConfigBundle) -> None:
    def drop(lfer: Any) -> None:
        lfer["taft_series"][0]["rho_star"] = None

    detail = _adjust_with(
        bundle, _lfer_with(bundle, drop), "CCC(=O)OCC", "CC(=O)OCC", _with_series(bundle, ["taft_ester_acyl"])
    )
    assert detail.method == "anchor_only"
    assert detail.flags == ("no_rho:taft_ester_acyl",)


def test_finite_inputs_cannot_return_overflowed_adjustments() -> None:
    with pytest.raises(ModelDomainError, match="finite"):
        lf.hammett_delta_ln_k(1e308, 2.0, 0.0)
    with pytest.raises(ModelDomainError, match="finite"):
        lf.taft_delta_ln_k(1e308, -1e308, 2.0, 2.0)
    with pytest.raises(ModelDomainError, match="finite"):
        lf.evans_polanyi_delta_ln_k(1e308, 10.0, 0.0, 298.15)
