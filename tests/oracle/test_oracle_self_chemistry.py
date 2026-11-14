"""Self-tests of the oracle's computed-layer rules (spec 7.3.4, 7.3.5, 9.5), no engine code."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.oracle import chemistry as oc
from tests.oracle.kinetics import rate_function
from tests.oracle.precision import MP, R_MP

HYPOTHESIS = settings(derandomize=True, max_examples=200, deadline=None)
SHIFTS = st.lists(st.one_of(st.none(), st.floats(-30.0, 30.0)), min_size=1, max_size=6)


MEMBERS = st.lists(
    st.builds(
        oc.SplitMember, st.booleans(), st.one_of(st.none(), st.floats(-30.0, 30.0)), st.floats(0.0, 3.0)
    ),
    min_size=1,
    max_size=6,
)
TIGHT = MP.mpf(10) ** -40


@HYPOTHESIS
@given(mu_total=st.floats(-40.0, 0.0), members=MEMBERS)
def test_split_keeps_the_total(mu_total: float, members: list[oc.SplitMember]) -> None:
    priors = oc.split_class_prior(mu_total, 1.0, members)
    total = MP.log(MP.fsum(MP.exp(p.mu) for p in priors))
    assert abs(total - mu_total) < TIGHT


@HYPOTHESIS
@given(mu_total=st.floats(-40.0, 0.0), members=MEMBERS)
def test_anchored_members_keep_the_equal_split(mu_total: float, members: list[oc.SplitMember]) -> None:
    equal, s_p = oc.class_prior_ln_k(mu_total, 0.7, len(members))
    for member, prior in zip(members, oc.split_class_prior(mu_total, 0.7, members), strict=True):
        if member.anchored:
            assert abs(prior.mu - equal) < TIGHT
            assert prior.sd == s_p
            assert prior.shared_weight == 1


@HYPOTHESIS
@given(mu_total=st.floats(-40.0, 0.0), n=st.integers(1, 6), anchored=st.integers(0, 6))
def test_no_shift_is_the_equal_split(mu_total: float, n: int, anchored: int) -> None:
    members = [oc.SplitMember(i < anchored) for i in range(n)]
    equal, s_p = oc.class_prior_ln_k(mu_total, 0.7, n)
    for prior in oc.split_class_prior(mu_total, 0.7, members):
        assert abs(prior.mu - equal) < MP.mpf(10) ** -45
        assert abs(prior.sd - s_p) < TIGHT
        assert prior.shared_weight == 1


@HYPOTHESIS
@given(mu_total=st.floats(-40.0, 0.0), members=MEMBERS)
def test_free_differences_are_the_shift_differences(mu_total: float, members: list[oc.SplitMember]) -> None:
    priors = oc.split_class_prior(mu_total, 1.0, members)
    free = [i for i, m in enumerate(members) if not m.anchored]
    delta = [MP.mpf(0) if m.shift is None else MP.mpf(m.shift) for m in members]
    for i in free:
        for j in free:
            assert abs((priors[i].mu - priors[j].mu) - (delta[i] - delta[j])) < TIGHT


@HYPOTHESIS
@given(
    mu_total=st.floats(-40.0, 0.0),
    shift=st.floats(-30.0, 30.0),
    s_m=st.floats(0.0, 3.0),
    n_anchored=st.integers(0, 4),
)
def test_a_free_group_of_one_ignores_its_shift(
    mu_total: float, shift: float, s_m: float, n_anchored: int
) -> None:
    members = [
        *(oc.SplitMember(True, 1.0, 1.0) for _ in range(n_anchored)),
        oc.SplitMember(False, shift, s_m),
    ]
    equal, s_p = oc.class_prior_ln_k(mu_total, 0.7, len(members))
    lone = oc.split_class_prior(mu_total, 0.7, members)[-1]
    assert abs(lone.mu - equal) < TIGHT
    assert abs(lone.sd - s_p) < TIGHT
    assert abs(lone.shared_weight - 1) < TIGHT


def test_two_free_members_by_hand() -> None:
    """N = 3, one anchored; free shifts ln 3 (s_m 0.4) and none: pi = (3/4, 1/4), block sd
    s_p, w = 1, free shares (ln 3, 0.4) and (0, 0)."""
    members = [
        oc.SplitMember(True, 5.0, 1.0),
        oc.SplitMember(False, math.log(3.0), 0.4),
        oc.SplitMember(False),
    ]
    priors = oc.split_class_prior(-20.0, 0.5, members)
    base = MP.mpf(-20) + MP.log(MP.mpf(2) / 3)
    assert abs(priors[1].mu - (base + MP.log(MP.mpf(3) / 4))) < MP.mpf(10) ** -15
    assert abs(priors[2].mu - (base + MP.log(MP.mpf(1) / 4))) < MP.mpf(10) ** -15
    assert priors[1].free_share == (MP.mpf(math.log(3.0)), MP.mpf(0.4))
    assert priors[2].free_share == (MP.mpf(0), MP.mpf(0))
    assert priors[0].free_share is None


@HYPOTHESIS
@given(members=MEMBERS)
def test_every_block_keeps_the_class_spread(members: list[oc.SplitMember]) -> None:
    """Final 9.5: every block has sd s_p and w = 1; free shares only in groups of two or more."""
    priors = oc.split_class_prior(-20.0, 0.7, members)
    n_free = sum(1 for m in members if not m.anchored)
    for member, prior in zip(members, priors, strict=True):
        assert prior.sd == MP.mpf(0.7)
        assert prior.shared_weight == 1
        assert (prior.free_share is not None) is (not member.anchored and n_free >= 2)


FREE_GROUP = st.integers(2, 6).flatmap(
    lambda n: st.tuples(
        st.lists(st.floats(-30.0, 30.0), min_size=n, max_size=n),
        st.lists(st.floats(0.0, 3.0), min_size=n, max_size=n),
        st.lists(st.floats(-5.0, 5.0), min_size=n, max_size=n),
    )
)


@HYPOTHESIS
@given(
    mu_total=st.floats(-40.0, 0.0), n_anchored=st.integers(0, 3), z_cls=st.floats(-5.0, 5.0), group=FREE_GROUP
)
def test_free_group_draw_keeps_the_class_total(
    mu_total: float, n_anchored: int, z_cls: float, group: tuple[list[float], list[float], list[float]]
) -> None:
    """Every draw: sum_F exp(ln k_i) = exp(mu_total + s_p z_cls) N_F / N exactly."""
    deltas, s_m, z_f = group
    members = [
        *(oc.SplitMember(True) for _ in range(n_anchored)),
        *(oc.SplitMember(False, d, s) for d, s in zip(deltas, s_m, strict=True)),
    ]
    priors = oc.split_class_prior(mu_total, 0.7, members)[n_anchored:]
    ln_k = oc.free_group_ln_k([p.mu for p in priors], 0.7, z_cls, deltas, s_m, z_f)
    n_f, n = len(deltas), len(members)
    expected = MP.mpf(mu_total) + MP.mpf(0.7) * MP.mpf(z_cls) + MP.log(MP.mpf(n_f) / n)
    assert abs(oc.log_sum_exp(ln_k) - expected) < MP.mpf(10) ** -35


@HYPOTHESIS
@given(group=FREE_GROUP)
def test_free_group_draw_at_nominal_is_the_block_mean(
    group: tuple[list[float], list[float], list[float]],
) -> None:
    deltas, s_m, _ = group
    mu = [-20.0 + i for i in range(len(deltas))]
    ln_k = oc.free_group_ln_k(mu, 0.7, 0.0, deltas, s_m, [0.0] * len(deltas))
    assert all(abs(k - MP.mpf(m)) < MP.mpf(10) ** -40 for k, m in zip(ln_k, mu, strict=True))


def test_free_group_draw_moves_rate_to_the_member_whose_shift_rises() -> None:
    """Two members, delta (0, 0), s_m (1, 0): z_1 = 1 gives shares e/(e+1) and 1/(e+1)."""
    ln_k = oc.free_group_ln_k([-21.0, -21.0], 0.5, 0.0, [0.0, 0.0], [1.0, 0.0], [1.0, 0.0])
    e = MP.e
    assert abs(ln_k[0] - (MP.mpf(-21) + MP.log(2 * e / (e + 1)))) < MP.mpf(10) ** -40
    assert abs(ln_k[1] - (MP.mpf(-21) + MP.log(2 / (e + 1)))) < MP.mpf(10) ** -40


def test_free_group_draw_needs_two_members() -> None:
    with pytest.raises(ValueError, match="two or more"):
        oc.free_group_ln_k([-20.0], 0.5, 0.0, [0.0], [0.0], [0.0])


def test_split_needs_a_member() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        oc.split_class_prior(-20.0, 1.0, [])


@pytest.mark.parametrize(
    ("site", "reference", "limit", "outside"),
    [
        (380.0, 350.0, 30.0, False),
        (380.0001, 350.0, 30.0, True),
        (320.0, 350.0, 30.0, False),
        (319.9, 350.0, 30.0, True),
        (500.0, 350.0, None, False),
    ],
)
def test_evans_polanyi_domain(site: float, reference: float, limit: float | None, outside: bool) -> None:
    assert oc.outside_applicability_domain(site, reference, limit) is outside
    delta = oc.evans_polanyi_delta_ln_k(0.5, site, reference, 298.15, limit)
    if outside:
        assert delta == 0
    else:
        expected = -MP.mpf("0.5") * (MP.mpf(site) - MP.mpf(reference)) * 1000 / (R_MP * MP.mpf(298.15))
        assert abs(delta - expected) < MP.mpf(10) ** -40


@HYPOTHESIS
@given(deltas=st.lists(st.floats(-20.0, 20.0), min_size=1, max_size=5), n_ref=st.integers(1, 9))
def test_weighted_combination_with_one_hydrogen_per_site_is_unweighted(
    deltas: list[float], n_ref: int
) -> None:
    weighted = oc.combine_sites_weighted(deltas, [1] * len(deltas), n_ref)
    assert abs(weighted - oc.combine_sites(deltas, n_ref)) < MP.mpf(10) ** -40


def test_weighted_combination_examples() -> None:
    refs = oc.EVANS_POLANYI_REFERENCE_HYDROGENS
    # The reference molecule against itself: trimethylamine has three CH3 sites of 3 H each.
    assert (
        abs(oc.combine_sites_weighted([0.0, 0.0, 0.0], [3, 3, 3], refs["trimethylamine"])) < MP.mpf(10) ** -45
    )
    assert abs(oc.combine_sites_weighted([0.0, 0.0], [1, 1], refs["catechol"])) < MP.mpf(10) ** -45
    # One alpha CH2 (2 H) at delta 1 against trimethylamine: ln(2 e / 9).
    value = oc.combine_sites_weighted([1.0], [2], refs["trimethylamine"])
    assert abs(value - (1 + MP.log(MP.mpf(2) / 9))) < MP.mpf(10) ** -45
    assert math.isfinite(float(oc.combine_sites_weighted([700.0, 699.0], [2, 3], 9)))


@pytest.mark.parametrize(
    ("deltas", "hydrogens", "reference"), [([], [], 1), ([0.0], [1, 2], 1), ([0.0], [0], 1), ([0.0], [1], 0)]
)
def test_weighted_combination_rejects_bad_counts(
    deltas: list[float], hydrogens: list[int], reference: int
) -> None:
    with pytest.raises(ValueError, match="hydrogen"):
        oc.combine_sites_weighted(deltas, hydrogens, reference)


def test_anchor_correlated_variance_and_covariance_algebra() -> None:
    sp, va, sz = MP.mpf("0.9"), MP.mpf("0.25"), MP.mpf("0.4")
    post, cov, effective = oc.correlated_anchor_moments(sp, va, sz)
    vc = va + sz**2
    weight = post / vc
    a = post / sp
    b = weight * MP.sqrt(va)
    c = -weight * sz
    assert abs(a * a + b * b + c * c - post) < TIGHT
    assert abs(c * sz - cov) < TIGHT
    assert abs(effective - ((sp**2 + sz**2) * va / (sp**2 + vc) + sz**4 / (sp**2 + vc))) < TIGHT
    coefficients = [
        oc.correlated_anchor_draw(0.0, MP.sqrt(post), sp, vc, sz, *z)
        for z in [(1, 0, 0), (0, 1, 0), (0, 0, sz)]
    ]
    assert abs(sum(x * x for x in coefficients) - post) < TIGHT


def test_reference_rate_constant_condition() -> None:
    mean, variance = oc.reference_rate(-20, 0.5, 298.15, 0, 40, 90000, 20000, 0.04, 0.01, 298.15, 40)
    assert mean == -20
    assert variance == MP.mpf("0.25")
    packaged = oc.packaged_reference_rate(
        -20, 0.5, 298.15, 0, 40, 90000, 20000, 0.04, 0.01, 298.15, 40, 1000, None, 0.5, 0.4
    )
    assert packaged[0] == MP.mpf("-20.5")
    assert abs(packaged[1] - (variance + MP.mpf(0.4) ** 2)) < TIGHT


@pytest.mark.parametrize("arguments", [(0, 1, 1), (1, -1, 1), (1, 0, 0)])
def test_invalid_correlated_moments(arguments: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError, match="variance"):
        oc.correlated_anchor_moments(*arguments)


def test_invalid_correlated_draw() -> None:
    with pytest.raises(ValueError, match="variance"):
        oc.correlated_anchor_draw(-20, 0.5, 1, 0.1, 1, 0, 0, 0)


def test_invalid_anchor_duration() -> None:
    with pytest.raises(ValueError, match="duration"):
        oc.packaged_reference_rate(-20, 0.5, 298, 1, 40, 90000, 20000, 0.04, 0.01, 298, 40, 0, None)


def test_d2_rate_near_zero_keeps_subtraction_precision() -> None:
    alpha = MP.mpf("1e-100")
    assert abs(rate_function("D2", alpha) * alpha - 1) < TIGHT
