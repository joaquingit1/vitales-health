from __future__ import annotations

from decimal import Decimal
from itertools import permutations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import fuse_ranks


@pytest.mark.trace("T-E12-032", "E12-02")
def test_rank_fusion_has_independent_golden_and_stable_ties() -> None:
    assert fuse_ranks((("a", "b"), ("b", "c")), k_rrf=Decimal(1), k_max=50, candidates_max=150) == (
        "b",
        "a",
        "c",
    )
    for lists in permutations((("b", "a"), ("a", "b"), ())):
        assert fuse_ranks(lists, k_rrf=Decimal(1), k_max=50, candidates_max=150) == ("a", "b")


@pytest.mark.trace("T-E12-032", "E12-02")
@given(st.lists(st.sampled_from(["a", "b", "c"]), unique=True, max_size=3))
def test_one_ranking_preserves_its_order(ids: list[str]) -> None:
    assert fuse_ranks((tuple(ids),), k_rrf=Decimal(60), k_max=50, candidates_max=150) == tuple(ids)


@pytest.mark.trace("T-E12-032", "T-E12-005", "I2")
@pytest.mark.parametrize("ranks", [(("a", "a"),), (("a",) * 51,), ((), (), (), ())])
def test_invalid_provider_ranks_do_not_inflate_scores(ranks: tuple[tuple[str, ...], ...]) -> None:
    with pytest.raises(ToolsInvalid):
        fuse_ranks(ranks, k_rrf=Decimal(1), k_max=50, candidates_max=150)


@pytest.mark.trace("T-E12-032", "E12-02")
def test_large_rank_constant_does_not_round_distinct_ranks_into_a_tie() -> None:
    assert fuse_ranks((("z", "a"),), k_rrf=Decimal("1e100"), k_max=50, candidates_max=150) == ("z", "a")


@pytest.mark.trace("T-E12-032", "T-E12-005", "I2")
@pytest.mark.parametrize("constant", [Decimal(0), Decimal(-1), Decimal("Infinity"), Decimal("NaN")])
def test_invalid_rank_constant_refuses_ranking(constant: Decimal) -> None:
    with pytest.raises(ToolsInvalid):
        fuse_ranks((("a",),), k_rrf=constant, k_max=50, candidates_max=150)
