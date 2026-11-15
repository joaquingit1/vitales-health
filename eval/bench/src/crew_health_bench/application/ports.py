from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from crew_health_bench.domain.cases import SimAnswer, SimMeasurement
from crew_health_bench.domain.runs import Generation, Grade


class Simulator(Protocol):
    def answer(self, question_text: str, choices: Sequence[str]) -> SimAnswer: ...
    def measure(self, capability_code: str) -> SimMeasurement: ...


class ArtifactStore(Protocol):
    def append(self, run_id: str, stream: str, record: Mapping[str, object]) -> None: ...
    def read_valid(self, run_id: str, stream: str) -> Sequence[Mapping[str, object]]: ...
    def write_atomic(self, path: Path, data: bytes) -> None: ...


class RunInputs(Protocol):
    def measure(self) -> Mapping[str, object]: ...
    def verify(self, run_id: str) -> None: ...


class CaseGenerator(Protocol):
    def generate(self, case_id: str, repeat: int) -> Generation: ...


class CaseGrader(Protocol):
    def grade(self, generation: Generation) -> Grade: ...


class AtomicArtifacts(Protocol):
    def write_atomic(self, path: Path, data: bytes) -> None: ...


class RunReporter(Protocol):
    def build(
        self,
        run_id: str,
        manifest: Mapping[str, object],
        generations: tuple[Generation, ...],
        grades: tuple[Grade, ...],
    ) -> Mapping[str, bytes]: ...
