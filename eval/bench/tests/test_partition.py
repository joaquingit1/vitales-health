"""T-E18-01, 02, 17: disjoint families and exposure history."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.adapters.documents import load_document
from crew_health_bench.domain.cases import Case, CaseKey, CaseReview, Partition, Suite, validate_cases
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.partition import PartitionPolicy, partition

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
    with pytest.raises(BenchError, match="group_spans_partitions"):
        partition(cases, policy(), salt=b"s" * 32, previous={"0": Partition.DEV, "1": Partition.HOLDOUT})
    with pytest.raises(BenchError, match="salt_missing"):
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
        with pytest.raises(BenchError):
            validate_cases(cases)
    b = replace(a, case_id="b", origin="paraphrase", derived_from=a.case_id)
    validate_cases((a, b, replace(b, case_id="c", derived_from=b.case_id)))


def test_t_e18_01_external_benchmark_never_becomes_development() -> None:
    external = replace(case(0), suite=Suite.HEALTHBENCH, origin="external")
    assert partition((external,), policy(), salt=b"s" * 32, previous={}) == {"0": Partition.BENCHMARK}
    for cases, previous in (((external,), {"0": Partition.DEV}), ((case(0),), {"0": Partition.BENCHMARK})):
        with pytest.raises(BenchError):
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
        with pytest.raises(BenchError):
            PartitionPolicy(base.group_by, permutations, bands, base.seed, near, holdout, min_groups)
