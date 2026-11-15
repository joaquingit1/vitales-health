from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile

from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters.documents import read_json
from crew_health_bench.adapters.grader_http import GraderHTTP
from crew_health_bench.adapters.healthbench import OfficialGrader
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.application.ports import ArtifactStore
from crew_health_bench.domain.documents import integer, number, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation, Grade


class HealthBenchCaseGrader:
    def __init__(
        self,
        *,
        official: OfficialGrader,
        client: GraderHTTP,
        store: ArtifactStore,
        root: Path,
        run_id: str,
        max_file_bytes: int,
    ) -> None:
        self.official, self.client, self.store = official, client, store
        self.root, self.run_id, self.maximum = root, run_id, max_file_bytes

    def _existing(self, generation: Generation) -> dict[str, object] | None:
        rows = [
            dict(row)
            for row in self.store.read_valid(self.run_id, "official_grades")
            if (row.get("case_id"), row.get("repeat")) == (generation.case_id, generation.repeat)
        ]
        if len(rows) > 1 or (rows and rows[0].get("output_sha256") != generation.output_sha256):
            raise BenchError("integrity", exit_code=6)
        return rows[0] if rows else None

    @staticmethod
    def _grade(row: dict[str, object]) -> Grade:
        value = row.get("official_score")
        return Grade(
            text(row["case_id"]),
            integer(row["repeat"]),
            (),
            None if value is None else number(value),
            integer(row["grader_calls"]),
            integer(row["grader_errors"]),
        )

    def grade(self, generation: Generation) -> Grade:
        previous = self._existing(generation)
        if previous is not None:
            return self._grade(previous)
        row: dict[str, object] = {
            "case_id": generation.case_id,
            "repeat": generation.repeat,
            "output_sha256": generation.output_sha256,
            "official_score": None,
            "metrics": {},
            "rubric_items": [],
            "grader_calls": 0,
            "grader_errors": 0,
            "graded_as_empty": generation.status != "completed",
        }
        self._evaluate(generation, row, self._completion(generation))
        self.store.append(self.run_id, "official_grades", row)
        return self._grade(row)

    def _completion(self, generation: Generation) -> str:
        """The completion to grade; a failed, timed-out or unrun generation is the empty response."""
        if generation.status != "completed":
            return ""
        path = vault_path(self.root, generation.output_ref)
        output = read_json(path, max_file_bytes=self.maximum)
        raw = canonical_json(output)
        if sha256(raw).hexdigest() != generation.output_sha256:
            raise BenchError("integrity", exit_code=6)
        return text(output.get("text"))

    def _evaluate(self, generation: Generation, row: dict[str, object], completion: str) -> None:
        calls, errors = self.client.calls, self.client.errors
        try:
            with TemporaryFile(mode="w+", encoding="utf-8") as log, redirect_stdout(log):
                result = self.official.grade(generation.case_id, completion)
                if log.tell() > self.maximum:
                    raise BenchError("target_unreadable", exit_code=6)
        except BenchError as error:
            if error.reason not in ("grader_unavailable", "grader_call_limit"):
                raise
            row.update(
                grader_calls=self.client.calls - calls, grader_errors=max(1, self.client.errors - errors)
            )
            return
        row.update(
            official_score=float(result.score.value),
            metrics={key: float(value.value) for key, value in result.metrics.items()},
            rubric_items=[dict(item) for item in result.rubric_items],
            grader_calls=self.client.calls - calls,
            grader_errors=self.client.errors - errors,
        )
