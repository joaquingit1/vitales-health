from decimal import Decimal
from pathlib import Path

import pytest
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.store import FileArtifactStore

from .test_lifecycle import Generator, Grader, Inputs, engine


@pytest.mark.trace("R22", "T-E18-13", "T-E18-19")
def test_only_a_committed_budget_limited_prefix_can_be_graded_and_resumed(tmp_path: Path) -> None:
    inputs, generator = Inputs(), Generator()
    generator.interrupt = False
    with FileArtifactStore(tmp_path, max_line_bytes=10000, max_records=16) as store:
        lifecycle = engine(store, inputs)
        lifecycle.plan()
        lifecycle.generate(generator, budget=Quantity(Decimal(1), Unit("s")))
        assert lifecycle.state() == "generated"
        assert generator.calls == ["case"]
        assert store.read_valid(lifecycle.run_id, "state")[-1]["completeness"] == "partial"
        lifecycle.generate(generator, budget=Quantity(Decimal(1), Unit("s")))
        assert generator.calls == ["case"]
        grader = engine(store, inputs, epoch="grader")
        grader.grade(Grader())
        assert grader.state() == "graded"
        assert len(store.read_valid(lifecycle.run_id, "grades")) == 1
