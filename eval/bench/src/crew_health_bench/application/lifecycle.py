from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity

from crew_health_bench.application.ports import (
    ArtifactStore,
    AtomicArtifacts,
    CaseGenerator,
    CaseGrader,
    RunInputs,
    RunReporter,
)
from crew_health_bench.domain.documents import integer, number, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.manifest import identity
from crew_health_bench.domain.runs import Generation, Grade, require_transition


class RunLifecycle:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        inputs: RunInputs,
        clock: Clock,
        ids: IdFactory,
        manifest: Mapping[str, object],
        cases: tuple[str, ...],
    ) -> None:
        self.run_id = identity(manifest, cases, ids)
        self.manifest = canonical_json(manifest)
        self.cases, self.repeats = cases, integer(manifest["repeats"])
        self.store, self.inputs, self.clock = store, inputs, clock

    def state(self) -> str:
        previous = ""
        for row in self.store.read_valid(self.run_id, "state"):
            current = text(row.get("state"))
            require_transition(previous, current)
            previous = current
        return previous

    def _transition(self, target: str, **evidence: object) -> None:
        require_transition(self.state(), target)
        self.store.append(
            self.run_id,
            "state",
            {
                "state": target,
                "at": self.clock.now().isoformat(),
                **evidence,
            },
        )

    def _verify(self) -> None:
        self.inputs.verify(self.run_id)
        if canonical_json(self.inputs.measure()) != self.manifest:
            self._transition("aborted", reason="drift")
            raise BenchError("drift")

    def plan(self) -> str:
        state = self.state()
        if state:
            return self.run_id
        self.inputs.verify(self.run_id)
        if canonical_json(self.inputs.measure()) != self.manifest:
            raise BenchError("drift")
        self.store.write_atomic(Path(self.run_id) / "manifest.json", self.manifest)
        self._transition("planned")
        return self.run_id

    def _records(self, stream: str) -> dict[tuple[str, int], Mapping[str, object]]:
        result: dict[tuple[str, int], Mapping[str, object]] = {}
        for row in self.store.read_valid(self.run_id, stream):
            key = text(row.get("case_id")), integer(row.get("repeat"))
            if (
                row.get("run_id") != self.run_id
                or key in result
                or key[0] not in self.cases
                or not 0 <= key[1] < self.repeats
            ):
                raise BenchError("integrity", exit_code=6)
            result[key] = row
        return result

    def generate(self, generator: CaseGenerator, *, budget: Quantity | None = None) -> None:
        state = self.state()
        if state not in ("planned", "generating", "generated"):
            raise BenchError("invalid_transition")
        self._verify()
        records = self._records("generations")
        if state == "generated":
            self._generation_commit(records)
            return
        if budget is not None and (budget.unit.code != "s" or budget.value <= 0):
            raise BenchError("case_invalid")
        if state == "planned":
            self._transition("generating")
        for case_id in self.cases:
            for repeat in range(self.repeats):
                if (case_id, repeat) in records:
                    continue
                if (
                    budget is not None
                    and sum(Generation.from_record(dict(row)).wall.value for row in records.values())
                    >= budget.value
                ):
                    break
                self._verify()
                try:
                    generation = generator.generate(case_id, repeat)
                except BenchError as error:
                    if error.reason in ("drift", "model_identity_mismatch", "model_unavailable"):
                        self._transition("aborted", reason=error.reason)
                    if error.reason == "model_unavailable":
                        raise BenchError("model_unavailable", exit_code=6) from error
                    raise
                if (generation.case_id, generation.repeat) != (case_id, repeat):
                    raise BenchError("case_invalid")
                self.store.append(self.run_id, "generations", generation.record(self.run_id))
                records[(case_id, repeat)] = generation.record(self.run_id)
            if (
                budget is not None
                and sum(Generation.from_record(dict(row)).wall.value for row in records.values())
                >= budget.value
            ):
                break
        self._verify()
        digest = sha256(canonical_json(tuple(self._records("generations").values()))).hexdigest()
        self.store.write_atomic(Path(self.run_id) / "generations.sha256", digest.encode("ascii"))
        self._transition(
            "generated",
            generations_sha256=digest,
            process_epoch=self.clock.process_epoch(),
            completeness="full" if len(records) == len(self.cases) * self.repeats else "partial",
            n=len(records),
            N=len(self.cases) * self.repeats,
            budget=None if budget is None else {"value": float(budget.value), "unit": "s"},
        )

    def _generation_commit(
        self, generations: dict[tuple[str, int], Mapping[str, object]]
    ) -> Mapping[str, object]:
        commits = tuple(
            row for row in self.store.read_valid(self.run_id, "state") if row.get("state") == "generated"
        )
        if (
            len(commits) != 1
            or commits[0].get("generations_sha256")
            != sha256(canonical_json(tuple(generations.values()))).hexdigest()
        ):
            raise BenchError("integrity", exit_code=6)
        commit = commits[0]
        total = len(self.cases) * self.repeats
        expected = tuple((case, repeat) for case in self.cases for repeat in range(self.repeats))
        if tuple(generations) != expected[: len(generations)]:
            raise BenchError("integrity", exit_code=6)
        if commit.get("completeness", "full") == "full":
            if len(generations) != total:
                raise BenchError("integrity", exit_code=6)
        elif commit.get("completeness") == "partial":
            budget = object_map(commit.get("budget"))

            if (
                not 0 < len(generations) < total
                or tuple(generations) != expected[: len(generations)]
                or commit.get("n") != len(generations)
                or commit.get("N") != total
                or budget.get("unit") != "s"
                or number(budget.get("value")) <= 0
                or sum(Generation.from_record(dict(row)).wall.value for row in generations.values())
                < number(budget.get("value"))
            ):
                raise BenchError("integrity", exit_code=6)
        else:
            raise BenchError("integrity", exit_code=6)
        return commit

    def grade(self, grader: CaseGrader) -> None:
        state = self.state()
        if state not in ("generated", "grading", "graded"):
            raise BenchError("invalid_transition")
        self._verify()
        generations, grades = self._records("generations"), self._records("grades")
        if not grades.keys() <= generations.keys():
            raise BenchError("integrity", exit_code=6)
        commit = self._generation_commit(generations)
        if commit.get("process_epoch") == self.clock.process_epoch():
            raise BenchError("grader_unavailable", exit_code=5)
        if state == "graded":
            if len(grades) != len(generations):
                raise BenchError("integrity", exit_code=6)
            return
        if state == "generated":
            self._transition("grading")
        for key, row in generations.items():
            if key in grades:
                continue
            grade = grader.grade(Generation.from_record(dict(row)))
            if (grade.case_id, grade.repeat) != key:
                raise BenchError("case_invalid")
            self.store.append(self.run_id, "grades", grade.record(self.run_id))
        self._verify()
        self._transition("graded")

    def invalidate(self, reason: str, evidence: tuple[str, ...]) -> None:
        if not reason or not evidence:
            raise BenchError("case_invalid")
        if self.state() == "invalidated":
            row = self.store.read_valid(self.run_id, "state")[-1]
            if row.get("reason") != reason or row.get("evidence") != list(evidence):
                raise BenchError("invalid_transition")
            return
        self._transition("invalidated", reason=reason, evidence=list(evidence))

    def report(self, reporter: RunReporter, published: AtomicArtifacts) -> None:
        state = self.state()
        if state == "reported":
            return
        if state != "graded":
            raise BenchError("invalid_transition")
        self._verify()
        generations = tuple(
            Generation.from_record(dict(row)) for row in self._records("generations").values()
        )
        grades = tuple(Grade.from_record(dict(row)) for row in self._records("grades").values())
        self._generation_commit(self._records("generations"))
        if not generations or len(generations) != len(grades):
            raise BenchError("integrity", exit_code=6)
        artifacts = reporter.build(self.run_id, object_map(json.loads(self.manifest)), generations, grades)
        required = {
            "run_manifest.json",
            "aggregate.json",
            "case_results.jsonl",
            "privacy_scan.json",
            "REPORT.md",
        }
        if set(artifacts) != required:
            raise BenchError("case_invalid")
        for name in (*sorted(required - {"run_manifest.json"}), "run_manifest.json"):
            published.write_atomic(Path(self.run_id) / name, artifacts[name])
        self._transition("reported")
