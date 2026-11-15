"""System-assisted HealthBench generation through BenchmarkAnswerPort (E13 provides, E18 declares)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from crew_health.kernel.ids import KernelIdFactory
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.benchmark import BenchmarkGenerator
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import HealthBenchVisible
from crew_health_bench.domain.model import BenchmarkAnswer, ChatMessage
from crew_health_bench.domain.preregistration import preregistration

pytestmark = [pytest.mark.e18]


class RecordedPort:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[ChatMessage, ...], str, str, Quantity]] = []

    def answer(
        self,
        messages: Sequence[ChatMessage],
        *,
        evidence_release_id: str,
        tool_profile: str,
        timeout: Quantity,
    ) -> BenchmarkAnswer:
        self.calls.append((tuple(messages), evidence_release_id, tool_profile, timeout))
        return BenchmarkAnswer("recorded answer", (), ("passage_1",), ("not_supplied_by_benchmark",), "stop")


@pytest.mark.trace("E18-02", "INV-E18-08", "V34")
def test_system_assisted_generation_uses_the_read_only_benchmark_profile(tmp_path: Path) -> None:
    port = RecordedPort()
    ids = KernelIdFactory(new_uuid_hex=lambda: "1" * 32)
    deadline = Quantity(Decimal(30), Unit("s"))
    case = HealthBenchVisible("synthetic-0", (("user", "Synthetic protocol prompt."),))
    with FileArtifactStore(tmp_path, max_line_bytes=1048576, max_records=4) as store:
        generator = BenchmarkGenerator(
            port=port,
            cases={"synthetic-0": case},
            evidence_release_id="evidence_1",
            deadline=deadline,
            store=store,
            clock=MemoryClock(datetime(2026, 11, 15, tzinfo=UTC), 0, "epoch_1"),
            ids=ids,
            run_id="benchrun_1",
        )
        generation = generator.generate("synthetic-0", 0)
        with pytest.raises(BenchError, match="case_invalid"):
            generator.generate("unknown", 0)
    assert generation.status == "completed" and generation.model_calls is None
    assert port.calls == [
        ((ChatMessage("user", "Synthetic protocol prompt."),), "evidence_1", "benchmark_readonly", deadline)
    ]
    assert b"recorded answer" in (tmp_path / generation.output_ref).read_bytes()


@pytest.mark.trace("E18-02", "R22", "T-E18-13")
def test_preregistration_refuses_incomplete_or_unpinned_inputs() -> None:
    seconds = [Quantity(Decimal(1), Unit("s"))] * 2
    budget = Quantity(Decimal(10), Unit("s"))
    good = {"plan_sha256": "a" * 64, "variants": {"main": ("a", "b", "c")}, "timings": {"main": seconds}}
    assert preregistration(
        good["plan_sha256"],
        good["variants"],
        good["timings"],
        seed="18",  # type: ignore[arg-type]
        frame_exclusions=(),
        warmup_n=2,
        budget=budget,
        frozen_by="evaluation owner",
    )
    for plan, variants, timings, frozen_by, warmup_n in (
        ("a" * 64, {"main": ("a", "b", "c")}, {"Hard": seconds}, "evaluation owner", 2),
        ("a" * 64, {"main": ("a", "b", "c")}, {"main": seconds}, "", 2),
        ("not a digest", {"main": ("a", "b", "c")}, {"main": seconds}, "evaluation owner", 2),
        ("A" * 64, {"main": ("a", "b", "c")}, {"main": seconds}, "evaluation owner", 2),
        ("a" * 64, {"main": ("a", "b")}, {"main": seconds}, "evaluation owner", 2),
        ("a" * 64, {"main": ("a", "b", "c")}, {"main": seconds}, "evaluation owner", 0),
    ):
        with pytest.raises(BenchError, match="warmup_failed"):
            preregistration(
                plan,
                variants,
                timings,
                seed="18",
                frame_exclusions=(),
                warmup_n=warmup_n,
                budget=budget,
                frozen_by=frozen_by,
            )
