from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity

from crew_health_bench.application.ports import AtomicArtifacts, Simulator
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.labels import check_preconditions
from crew_health_bench.domain.readiness import SuiteReadiness
from crew_health_bench.domain.runs import Generation
from crew_health_bench.domain.simulator import LookupSimulator
from crew_health_bench.domain.timing import elapsed
from crew_health_bench.domain.trace import CaseTrace
from crew_health_bench.domain.visible import ProductCase, VisibleCase


class SystemUnderTest(Protocol):
    def run_case(
        self, case: VisibleCase, simulator: Simulator, *, track: str, deadline: Quantity
    ) -> CaseTrace: ...


class SuiteGenerator:
    def __init__(
        self,
        *,
        system: SystemUnderTest | None,
        cases: Mapping[str, ProductCase],
        track: str,
        deadline: Quantity,
        documents: Mapping[str, object],
        store: AtomicArtifacts,
        clock: Clock,
        ids: IdFactory,
        run_id: str,
        readiness: SuiteReadiness | None = None,
    ) -> None:
        if track not in ("rules_only", "system_assisted"):
            raise BenchError("case_invalid")
        readiness = readiness or SuiteReadiness(True, None, ())
        if readiness.ready == (system is None):
            raise BenchError("case_invalid")
        self.readiness = readiness
        self.system, self.cases, self.track, self.deadline = system, cases, track, deadline
        self.documents, self.store, self.clock, self.ids, self.run_id = documents, store, clock, ids, run_id

    def generate(self, case_id: str, repeat: int) -> Generation:
        if case_id not in self.cases:
            raise BenchError("case_invalid")
        product = self.cases[case_id]
        clock = self.clock
        started, mark = clock.now(), (clock.process_epoch(), clock.monotonic_ns())
        status = "not_run"
        count: int | None = 0
        output: dict[str, object] = {"reason": "config_precondition"}
        if self.system is None:
            output = {
                "reason": self.readiness.reason,
                "missing_operations": list(self.readiness.missing_operations),
            }
        elif check_preconditions(product.case.requires, self.documents):
            if product.case.revealable is None:
                raise BenchError("case_invalid")
            trace = self.system.run_case(
                product.visible,
                LookupSimulator(product.case.revealable),
                track=self.track,
                deadline=self.deadline,
            )
            status, count = self._status(trace)
            output = asdict(trace)
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

    def _status(self, trace: CaseTrace) -> tuple[str, int | None]:
        if trace.requests_between is None:
            return "harness_error", None
        count = len(trace.requests_between)
        if self.track == "rules_only" and count:
            return "harness_error", count
        return "completed", count
