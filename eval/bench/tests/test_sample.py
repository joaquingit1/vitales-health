"""T-E18-13, T-E18-20, T-E18-23: frozen samples and run identities."""

from decimal import Decimal

import pytest
from crew_health.kernel.testing import MemoryIds
from crew_health.kernel.units import Quantity, Unit
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.sample import eligible_order, run_id, sample_size, validate_grader
from hypothesis import given
from hypothesis import strategies as st

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
        with pytest.raises(BenchError):
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
