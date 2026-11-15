"""T-E18-13, T-E18-20, T-E18-23: frozen samples and run identities."""

from decimal import Decimal
from hashlib import sha256

import pytest
from crew_health.kernel.testing import MemoryIds
from crew_health.kernel.units import Quantity, Unit
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.sample import eligible_order, run_id, sample_size, validate_grader

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "INV-E18-05", "INV-E18-07")]


@given(st.lists(st.text(alphabet="abcdef0123456789", min_size=1, max_size=12), unique=True, max_size=30))
def test_t_e18_13_prefix_and_warmup(ids: list[str]) -> None:
    warmup = tuple(ids[-2:])
    order = eligible_order(ids, seed="seed", frame_exclusions=warmup)
    assert not set(order) & set(warmup)
    for k in range(len(order) + 1):
        assert eligible_order(ids[::-1], seed="seed", frame_exclusions=warmup)[:k] == order[:k]


def test_t_e18_13_budget_cannot_include_warmup() -> None:
    assert sample_size(2, Quantity(Decimal(100), Unit("s")), Quantity(Decimal(3), Unit("s"))) == 2
    for value, unit in [(Decimal(0), "s"), (Decimal(1), "ms")]:
        with pytest.raises(BenchError, match="^warmup_failed$"):
            sample_size(2, Quantity(Decimal(100), Unit("s")), Quantity(value, Unit(unit)))


def test_t_e18_20_manifest_fields_and_seed_bind_identity() -> None:
    factory = MemoryIds(identifiers=())
    manifest: dict[str, object] = {"seed": 1, "prompt_sha256": "a", "grader": "independent"}
    original = run_id(manifest, factory)
    assert original == run_id(dict(reversed(list(manifest.items()))), factory)
    for key in manifest:
        assert original != run_id({**manifest, key: "changed"}, factory)
    with pytest.raises(BenchError, match="grader"):
        validate_grader("candidate", "candidate")
    validate_grader("candidate", "independent")


def test_t_e18_13_order_is_the_seeded_hash_order_of_design_4_2() -> None:
    """`eligible_order` sorts by `SHA-256(seed + prompt_id)`; duplicate IDs are a malformed corpus."""
    ids = [format(index, "x") for index in range(40)]
    expected = sorted(ids, key=lambda item: sha256(("seed" + item).encode()).digest())
    assert eligible_order(ids, seed="seed", frame_exclusions=()) == tuple(expected)
    assert eligible_order(ids, seed="other", frame_exclusions=()) != tuple(expected)
    with pytest.raises(BenchError, match="^case_invalid$"):
        eligible_order(["a", "a"], seed="seed", frame_exclusions=())


def test_t_e18_13_sample_size_is_the_floor_of_budget_over_case_time() -> None:
    """`n = min(N_variant, floor(budget / t_case))` (design 4.2), in seconds only."""

    def seconds(value: str, unit: str = "s") -> Quantity:
        return Quantity(Decimal(value), Unit(unit))

    assert sample_size(100, seconds("10"), seconds("3")) == 3
    assert sample_size(100, seconds("9"), seconds("3")) == 3
    assert sample_size(100, seconds("2.9"), seconds("3")) == 0
    assert sample_size(2, seconds("10"), seconds("3")) == 2
    assert sample_size(0, seconds("10"), seconds("3")) == 0
    assert sample_size(100, seconds("1"), seconds("0.5")) == 2, "a one second budget is a budget"
    assert sample_size(100, seconds("0.5"), seconds("0.25")) == 2
    for eligible, budget, t_case in (
        (-1, seconds("10"), seconds("3")),
        (1, seconds("10", "min"), seconds("3")),
        (1, seconds("10"), seconds("3", "ms")),
        (1, seconds("0"), seconds("3")),
        (1, seconds("-1"), seconds("3")),
        (1, seconds("10"), seconds("0")),
    ):
        with pytest.raises(BenchError, match="^warmup_failed$"):
            sample_size(eligible, budget, t_case)


def test_t_e18_07_grader_must_be_named_and_independent() -> None:
    for candidate, grader in (("", "grader"), ("candidate", ""), ("same", "same")):
        with pytest.raises(BenchError, match="^grader_unavailable$"):
            validate_grader(candidate, grader)
    assert run_id({"seed": 1}, MemoryIds(identifiers=())).startswith("benchrun_")
