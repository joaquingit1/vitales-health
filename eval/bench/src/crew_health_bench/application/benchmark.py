from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity

from crew_health_bench.application.model_ports import BenchmarkAnswerPort
from crew_health_bench.application.ports import AtomicArtifacts
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import HealthBenchVisible
from crew_health_bench.domain.model import ChatMessage
from crew_health_bench.domain.runs import Generation
from crew_health_bench.domain.timing import elapsed


class BenchmarkGenerator:
    def __init__(
        self,
        *,
        port: BenchmarkAnswerPort | None,
        cases: Mapping[str, HealthBenchVisible],
        evidence_release_id: str,
        deadline: Quantity,
        store: AtomicArtifacts,
        clock: Clock,
        ids: IdFactory,
        run_id: str,
    ) -> None:
        self.port, self.cases, self.evidence_release_id, self.deadline = (
            port,
            cases,
            evidence_release_id,
            deadline,
        )
        self.store, self.clock, self.ids, self.run_id = store, clock, ids, run_id

    def generate(self, case_id: str, repeat: int) -> Generation:
        if case_id not in self.cases:
            raise BenchError("case_invalid")
        clock = self.clock
        started, mark = clock.now(), (clock.process_epoch(), clock.monotonic_ns())
        output: dict[str, object] = {"reason": "no_evaluation_entry_point"}
        status: str = "not_run"
        count: int | None = 0
        if self.port is not None:
            answer = self.port.answer(
                tuple(ChatMessage(role, content) for role, content in self.cases[case_id].messages),
                evidence_release_id=self.evidence_release_id,
                tool_profile="benchmark_readonly",
                timeout=self.deadline,
            )
            output, status, count = asdict(answer), "completed", None
        reference = Path(self.run_id) / "outputs" / self.ids.new("benchoutput")
        raw = canonical_json(output)
        self.store.write_atomic(reference, raw)
        return Generation(
            case_id,
            repeat,
            status,
            started,
            clock.now(),
            clock.process_epoch(),
            elapsed(mark, clock),
            sha256(raw).hexdigest(),
            reference.as_posix(),
            reference.as_posix(),
            count,
        )
