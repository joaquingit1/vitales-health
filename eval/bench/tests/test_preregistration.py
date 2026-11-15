from decimal import Decimal
from pathlib import Path

import pytest
from crew_health.kernel.units import Quantity, Unit
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.preregistration import PreRegister
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.preregistration import preregistration
from crew_health_bench.domain.sample import eligible_order
from hypothesis import given
from hypothesis import strategies as st


class Owner:
    def require_owner(self) -> None:
        return None


@pytest.mark.trace("R22", "R23")
@given(st.integers(min_value=4, max_value=80), st.integers(min_value=1, max_value=100))
def test_warmup_excluded_before_seeded_prefix_and_budget(n: int, budget: int) -> None:
    ids = tuple(str(index) for index in range(n))
    result = preregistration(
        "a" * 64,
        {"main": ids},
        {"main": (Quantity(Decimal(2), Unit("s")),)},
        seed="18",
        frame_exclusions=(),
        warmup_n=1,
        budget=Quantity(Decimal(budget), Unit("s")),
        frozen_by="evaluation owner",
    )
    variant = result.variants["main"]
    order = eligible_order(ids, seed="18", frame_exclusions=())
    assert variant.warmup_ids == order[-1:]
    assert variant.case_ids == order[: min(n - 1, budget // 2)]
    assert not set(variant.warmup_ids).intersection(variant.case_ids)
    assert variant.t_case == Quantity(Decimal(2), Unit("s"))


@pytest.mark.trace("R22", "R23", "invariant")
def test_preregistration_is_durable_and_frozen_before_outputs(tmp_path: Path) -> None:
    result = preregistration(
        "b" * 64,
        {"main": ("a", "b", "c")},
        {"main": (Quantity(Decimal(2), Unit("s")), Quantity(Decimal(4), Unit("s")))},
        seed="18",
        frame_exclusions=(),
        warmup_n=2,
        budget=Quantity(Decimal(6), Unit("s")),
        frozen_by="evaluation owner",
    )
    assert result.variants["main"].t_case == Quantity(Decimal(3), Unit("s"))
    with FileArtifactStore(tmp_path, max_line_bytes=100000, max_records=10) as store:
        register = PreRegister(store, Owner())
        register.freeze(result, generated_plan_hashes=())
        register.freeze(result, generated_plan_hashes=())
        with pytest.raises(BenchError, match="already_generated"):
            register.freeze(result, generated_plan_hashes=(result.plan_sha256,))
        with pytest.raises(BenchError, match="preregistration_conflict"):
            register.freeze(
                preregistration(
                    "b" * 64,
                    {"main": ("a", "b", "c")},
                    {"main": (Quantity(Decimal(2), Unit("s")),)},
                    seed="18",
                    frame_exclusions=(),
                    warmup_n=1,
                    budget=Quantity(Decimal(6), Unit("s")),
                    frozen_by="evaluation owner",
                ),
                generated_plan_hashes=(),
            )
    assert (tmp_path / "preregistration.json").is_file()
    with FileArtifactStore(tmp_path, max_line_bytes=100000, max_records=10) as store:
        PreRegister(store, Owner()).freeze(result, generated_plan_hashes=())


@pytest.mark.trace("R22", "invariant")
@pytest.mark.parametrize(
    "timings", [(), (Quantity(Decimal(0), Unit("s")),), (Quantity(Decimal(1), Unit("ms")),)]
)
def test_failed_or_missing_warmup_never_substitutes_timing(timings: tuple[Quantity, ...]) -> None:
    with pytest.raises(BenchError, match="warmup_failed"):
        preregistration(
            "a" * 64,
            {"main": ("a", "b")},
            {"main": timings},
            seed="18",
            frame_exclusions=(),
            warmup_n=1,
            budget=Quantity(Decimal(2), Unit("s")),
            frozen_by="evaluation owner",
        )
