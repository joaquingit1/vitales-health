"""T-E18-01, 02, 17: disjoint families and exposure history."""

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.adapters.documents import load_document
from crew_health_bench.domain.cases import Case, CaseKey, CaseReview, Partition, Suite, validate_cases
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.partition import PartitionPolicy, assign_groups, group_cases, partition

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "INV-E18-01", "INV-E18-04")]


def case(number: int, family: str | None = None) -> Case:
    name = str(number)
    return Case(
        name,
        Suite.NASA_SCENARIO,
        CaseKey(family or name, name, name, name),
        "authored",
        None,
        (),
        (),
        name,
        None,
        "0" * 64,
        "canary_" + name,
        CaseReview("unreviewed", ()),
    )


def policy() -> PartitionPolicy:
    return PartitionPolicy.from_document(
        load_document(Path(__file__).parents[1] / "config/partition-policy.json", max_file_bytes=16777216)
    )


@given(st.integers(min_value=2, max_value=30))
def test_t_e18_01_family_and_derivative(n: int) -> None:
    cases = tuple(case(i) for i in range(n))
    derivative = replace(cases[0], case_id="derivative", origin="paraphrase", derived_from="0")
    result = partition((*cases, derivative), policy(), salt=b"s" * 32, previous={})
    assert result["0"] == result["derivative"]
    assert result == partition((*cases[::-1], derivative), policy(), salt=b"s" * 32, previous={})


def test_t_e18_02_representative_families_have_usable_partitions() -> None:
    cases = tuple(case(i) for i in range(50))
    result = partition(cases, policy(), salt=b"s" * 32, previous={})
    assert set(result.values()) == {Partition.DEV, Partition.HOLDOUT}
    other = partition(cases, policy(), salt=b"t" * 32, previous={})
    assert result != other
    assert partition(cases, policy(), salt=b"t" * 32, previous=result) == result


def test_t_e18_01_contaminating_merge_refused() -> None:
    cases = (case(0, "family"), case(1, "family"))
    with pytest.raises(BenchError, match="^group_spans_partitions$"):
        partition(cases, policy(), salt=b"s" * 32, previous={"0": Partition.DEV, "1": Partition.HOLDOUT})
    with pytest.raises(BenchError, match="^salt_missing$"):
        partition(cases, policy(), salt=b"", previous={})


def test_t_e18_17_threshold_merges_without_changing_existing_exposure() -> None:
    a, b = case(0), case(1)
    a = replace(a, visible="alpha beta gamma delta epsilon zeta eta theta")
    b = replace(b, visible="alpha beta gamma delta epsilon zeta eta iota")
    stricter = replace(policy(), near_duplicate_jaccard=Decimal("1"))
    before = partition((a, b), stricter, salt=b"s" * 32, previous={})
    merged = partition(
        (a, b), replace(policy(), near_duplicate_jaccard=Decimal("0.5")), salt=b"s" * 32, previous={}
    )
    assert merged["0"] == merged["1"]
    assert before == partition((a, b), stricter, salt=b"s" * 32, previous={})


def test_t_e18_01_invalid_case_derivations_refused() -> None:
    a = case(0)
    invalid_sets = (
        (a, a),
        (replace(a, canary=""),),
        (replace(a, origin="paraphrase"),),
        (replace(a, derived_from="missing"),),
        (replace(a, derived_from=a.case_id),),
        (a, replace(case(1), derived_from=a.case_id)),
        (replace(a, derived_from="b"), replace(a, case_id="b", derived_from=a.case_id)),
    )
    for cases in invalid_sets:
        with pytest.raises(BenchError, match="^case_invalid$"):
            validate_cases(cases)
    b = replace(a, case_id="b", origin="paraphrase", derived_from=a.case_id)
    validate_cases((a, b, replace(b, case_id="c", derived_from=b.case_id)))


def test_t_e18_01_external_benchmark_never_becomes_development() -> None:
    external = replace(case(0), suite=Suite.HEALTHBENCH, origin="external")
    assert partition((external,), policy(), salt=b"s" * 32, previous={}) == {"0": Partition.BENCHMARK}
    for cases, previous in (((external,), {"0": Partition.DEV}), ((case(0),), {"0": Partition.BENCHMARK})):
        with pytest.raises(BenchError, match="^group_spans_partitions$"):
            partition(cases, policy(), salt=b"s" * 32, previous=previous)


def test_t_e18_17_invalid_partition_policy_refused() -> None:
    base = policy()
    for permutations, bands, near, holdout, min_groups in (
        (0, 4, Decimal("0.5"), Decimal("0.3"), 2),
        (16, 3, Decimal("0.5"), Decimal("0.3"), 2),
        (16, 4, Decimal(0), Decimal("0.3"), 2),
        (16, 4, Decimal("0.5"), Decimal(1), 2),
        (16, 4, Decimal("0.5"), Decimal("0.3"), 0),
    ):
        with pytest.raises(BenchError, match="^case_invalid$"):
            PartitionPolicy(base.group_by, permutations, bands, base.seed, near, holdout, min_groups)


def test_t_e18_01_assignment_follows_the_design_rule_from_the_smallest_case_id() -> None:
    """Design 3.4 steps 3 and 4: the group root is its smallest `case_id`; the group goes to holdout when
    `int(SHA-256(salt + root)[0:8], 16) mod 10000 < holdout_fraction * 10000`, else to dev."""
    salt = b"s" * 32
    rules = policy()

    def expected(root: str) -> Partition:
        bucket = int(sha256(salt + root.encode()).hexdigest()[:8], 16) % 10000
        return Partition.HOLDOUT if bucket < rules.holdout_fraction * 10000 else Partition.DEV

    cases = tuple(case(i) for i in range(40))
    result = partition(cases, rules, salt=salt, previous={})
    assert result == {item.case_id: expected(item.case_id) for item in cases}
    pairs = [(str(i) + "_b", str(i) + "_a") for i in range(40)]
    grouped = tuple(
        replace(case(0), case_id=member, key=CaseKey("family_" + a, member, member, member), visible=member)
        for a, b in pairs
        for member in (a, b)
    )
    merged = partition(grouped, rules, salt=salt, previous={})
    assert {item: merged[item] for pair in pairs for item in pair} == {
        item: expected(root) for b, root in pairs for item in (b, root)
    }
    assert any(expected(b) != expected(a) for b, a in pairs), (
        "the fixture tells the two candidate roots apart"
    )


def test_t_e18_01_fewer_groups_than_the_split_minimum_go_wholly_to_holdout() -> None:
    cases = tuple(case(i) for i in range(5))
    rules = replace(policy(), holdout_fraction=Decimal("0.0001"))
    assert set(partition(cases, rules, salt=b"s" * 32, previous={}).values()) == {Partition.DEV}
    assert set(
        partition(cases, replace(rules, min_groups_for_split=6), salt=b"s" * 32, previous={}).values()
    ) == {Partition.HOLDOUT}
    assert set(
        partition(cases, replace(rules, min_groups_for_split=5), salt=b"s" * 32, previous={}).values()
    ) == {Partition.DEV}


def test_t_e18_01_exposure_history_wins_over_the_salted_rule() -> None:
    cases = tuple(case(i) for i in range(10))
    fresh = partition(cases, policy(), salt=b"s" * 32, previous={})
    flipped = {
        key: Partition.DEV if value == Partition.HOLDOUT else Partition.HOLDOUT
        for key, value in fresh.items()
    }
    assert partition(cases, policy(), salt=b"s" * 32, previous=flipped) == flipped
    with pytest.raises(BenchError, match="^group_spans_partitions$"):
        partition(cases[:1], policy(), salt=b"s" * 32, previous={"0": Partition.BENCHMARK})
    external = replace(case(0), suite=Suite.HEALTHBENCH, origin="external")
    with pytest.raises(BenchError, match="^group_spans_partitions$"):
        partition((external,), policy(), salt=b"s" * 32, previous={"0": Partition.HOLDOUT})
    with pytest.raises(BenchError, match="^group_spans_partitions$"):
        partition((external, replace(case(1), key=external.key)), policy(), salt=b"s" * 32, previous={})


@pytest.mark.parametrize(
    "field,value",
    [
        ("group_by", ("family_id", "source_vignette_id", "patient_id")),
        ("group_by", ("family_id", "source_vignette_id", "patient_id", "patient_id")),
        ("group_by", ("family_id", "source_vignette_id", "patient_id", "episode_id", "episode_id")),
        ("permutations", 0),
        ("bands", 0),
        ("permutations", 130),
        ("near_duplicate_jaccard", Decimal("1.0001")),
        ("near_duplicate_jaccard", Decimal("-0.5")),
        ("holdout_fraction", Decimal(0)),
        ("holdout_fraction", Decimal(1)),
        ("min_groups_for_split", 0),
    ],
)
def test_t_e18_17_partition_policy_bounds_refuse(field: str, value: object) -> None:
    with pytest.raises(BenchError, match="^case_invalid$"):
        replace(policy(), **{field: value})


def test_t_e18_17_partition_policy_bounds_accept_the_edges() -> None:
    base = policy()
    for changes in (
        {"near_duplicate_jaccard": Decimal(1)},
        {"holdout_fraction": Decimal("0.0001")},
        {"holdout_fraction": Decimal("0.9999")},
        {"min_groups_for_split": 1},
        {"permutations": 1, "bands": 1},
        {"group_by": tuple(reversed(base.group_by))},
    ):
        replace(base, **changes)  # type: ignore[arg-type]


def test_t_e18_01_near_duplicates_need_a_shared_band_and_the_exact_threshold() -> None:
    """Design 3.4 step 2: MinHash and LSH propose candidates; exact Jaccard confirms them. Three cases, two
    near duplicates of each other and one unrelated, give exactly one merged pair whatever the input order."""
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    a = replace(case(0), visible=" ".join(words))
    b = replace(case(1), visible=" ".join(words[:-1] + ["nu"]))
    c = replace(case(2), visible="completely different words about a solar particle event shelter drill")
    rules = replace(policy(), near_duplicate_jaccard=Decimal("0.5"), min_groups_for_split=1)
    groups = group_cases((c, b, a), rules)
    assert sorted(tuple(item.case_id for item in members) for members in groups.values()) == [
        ("0", "1"),
        ("2",),
    ]
    assert list(groups) == ["0", "2"], "groups are keyed by their smallest case_id, in order"
    assert len(group_cases((a, b, c), replace(rules, near_duplicate_jaccard=Decimal(1)))) == 3
    empty = replace(case(3), visible="")
    assert len(group_cases((a, empty), rules)) == 2, "a case with no shingles is never a near duplicate"


def test_t_e18_01_bucket_on_the_holdout_boundary_goes_to_dev() -> None:
    """Design 3.4 step 4 is a strict `<`: `root_2090` hashes to bucket 3000 under this salt, exactly
    `holdout_fraction * 10000` for the configured 0.3, so its group goes to dev."""
    salt = b"s" * 32
    assert int(sha256(salt + b"root_2090").hexdigest()[:8], 16) % 10000 == 3000
    cases = (replace(case(0), case_id="root_2090"), case(1))
    result = partition(cases, policy(), salt=salt, previous={})
    assert result["root_2090"] == Partition.DEV


def test_t_e18_01_near_duplicates_use_word_five_grams_and_an_inclusive_threshold() -> None:
    """Design 3.4 step 2: word 5-gram shingles, confirmed when Jaccard is at least
    `near_duplicate_jaccard`."""
    words = [f"w{index}" for index in range(12)]
    a = replace(case(1), visible=" ".join(words))
    b = replace(case(2), visible=" ".join([*words[:-1], "changed"]))
    # Five-grams: 7 shared of 9 (0.78); six-grams would give 6 of 8 (0.75).
    rules = replace(policy(), near_duplicate_jaccard=Decimal("0.76"), min_groups_for_split=1)
    assert len(group_cases((a, b), rules)) == 1
    c = replace(case(3), visible=" ".join(words[:10]))
    d = replace(case(4), visible=" ".join([*words[:8], "x", "y"]))
    # Four shared five-grams of eight: exactly the configured 0.5.
    assert policy().near_duplicate_jaccard == Decimal("0.5")
    assert len(group_cases((c, d), replace(policy(), min_groups_for_split=1))) == 1
    empty_first = replace(case(0), visible="")
    assert sorted(len(members) for members in group_cases((empty_first, a, b), rules).values()) == [1, 2], (
        "a case with no text is skipped, not the end of the search"
    )


def test_t_e18_17_policy_reads_every_field_of_its_document() -> None:
    document = load_document(
        Path(__file__).parents[1] / "config/partition-policy.json", max_file_bytes=16777216
    )
    rules = PartitionPolicy.from_document(document)
    minhash = document["minhash"]
    assert isinstance(minhash, dict)
    assert (rules.permutations, rules.bands, rules.seed) == (
        minhash["permutations"],
        minhash["bands"],
        minhash["seed"],
    )
    assert rules.near_duplicate_jaccard == Decimal(str(document["near_duplicate_jaccard"]))
    assert rules.holdout_fraction == Decimal(str(document["holdout_fraction"]))
    assert rules.min_groups_for_split == document["min_groups_for_split"]


def test_t_e18_01_freeze_assignment_refuses_a_short_salt() -> None:
    """The freeze calls `assign_groups` directly (`partition_artifacts.py`); it needs the owner's 32-byte
    salt."""
    groups = group_cases((case(0),), policy())
    for salt in (b"", b"s" * 31, b"s" * 33):
        with pytest.raises(BenchError, match="^salt_missing$"):
            assign_groups(groups, policy(), salt=salt, previous={})
