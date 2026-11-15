"""T-E18-19/20/21/28/33/34: resumable runs against real file storage."""

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.testing import MemoryClock, MemoryIds
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.lifecycle import RunLifecycle
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation, Grade

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "T-E18-19", "T-E18-21", "T-E18-33", "T-E18-34")]


def manifest() -> dict[str, object]:
    return {
        "suite": "tool",
        "track": "system_assisted",
        "partition": "dev",
        "variant": None,
        "case_set_sha256": sha256(canonical_json(("case", "second"))).hexdigest(),
        "dataset_sha256": {},
        "harness": {},
        "model_release": {},
        "prompt_sha256": "a" * 64,
        "tool_catalog_sha256": "b" * 64,
        "evidence_release_id": "evidence",
        "evidence_sha256": "c" * 64,
        "registry_version": "registry",
        "config_release_id": "config",
        "bench_documents_sha256": {},
        "grader": {"model": "deterministic"},
        "decoding": {},
        "seed": "1",
        "preregistration_sha256": "none",
        "hardware": {},
        "software": {},
        "integrity": "modified",
        "repeats": 1,
    }


class Inputs:
    def __init__(self) -> None:
        self.value = manifest()
        self.verified: list[str] = []

    def measure(self) -> dict[str, object]:
        return self.value.copy()

    def verify(self, run_id: str) -> None:
        self.verified.append(run_id)


class Generator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.interrupt = True

    def generate(self, case_id: str, repeat: int) -> Generation:
        self.calls.append(case_id)
        if case_id == "second" and self.interrupt:
            raise BenchError("interrupted", exit_code=7)
        at = datetime(2026, 11, 14, tzinfo=UTC)
        return Generation(
            case_id,
            repeat,
            "completed",
            at,
            at,
            "generator",
            Quantity(Decimal(1), Unit("s")),
            "a" * 64,
            "private/output",
            "private/trace",
            0,
        )


class Grader:
    def grade(self, value: Generation) -> Grade:
        return Grade(value.case_id, value.repeat, (("NoFabricatedResult", "pass", ()),), None, 0, 0)


def engine(files: FileArtifactStore, inputs: Inputs, epoch: str = "generator") -> RunLifecycle:
    return RunLifecycle(
        store=files,
        inputs=inputs,
        clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, epoch),
        ids=MemoryIds(identifiers=()),
        manifest=manifest(),
        cases=("case", "second"),
    )


def test_resume_skips_only_committed_cases_and_keeps_identity(tmp_path: Path) -> None:
    inputs, generator = Inputs(), Generator()
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        original = run.plan()
        with pytest.raises(BenchError, match="interrupted"):
            run.generate(generator)
        assert run.state() == "generating"
    generator.interrupt = False
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        assert run.plan() == original
        run.generate(generator)
        assert generator.calls == ["case", "second", "second"]
        assert run.state() == "generated"
        grader = engine(files, inputs, "grader")
        grader.grade(Grader())
        assert grader.state() == "graded"
        grader.grade(Grader())
        assert len(files.read_valid(original, "grades")) == 2
        grader.invalidate("review", ("evidence_ref",))
        grader.invalidate("review", ("evidence_ref",))
        assert grader.state() == "invalidated"
        assert len(inputs.verified) >= 3


@pytest.mark.parametrize("field", ["prompt_sha256", "config_release_id", "model_release"])
def test_drift_aborts_before_next_case_or_grading(tmp_path: Path, field: str) -> None:
    inputs, generator = Inputs(), Generator()
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        run.plan()
        inputs.value[field] = "changed"
        with pytest.raises(BenchError, match="drift"):
            run.generate(generator)
        assert run.state() == "aborted"
        assert not generator.calls


def test_grading_in_generation_epoch_is_refused(tmp_path: Path) -> None:
    inputs, generator = Inputs(), Generator()
    generator.interrupt = False
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs, "epoch")
        run.plan()
        run.generate(generator)
        with pytest.raises(BenchError, match="grader_unavailable"):
            run.grade(Grader())
        assert run.state() == "generated"


@pytest.mark.parametrize(
    "changes",
    [
        {"repeats": 0},
        {"track": "unknown"},
        {"partition": "unknown"},
        {"integrity": "unknown"},
        {"case_set_sha256": "changed"},
        {"suite": ""},
        {"grader": "unparsed"},
        {"unexpected": "field"},
    ],
)
def test_invalid_manifest_refuses_before_any_record(tmp_path: Path, changes: dict[str, object]) -> None:
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files, pytest.raises(BenchError):
        RunLifecycle(
            store=files,
            inputs=Inputs(),
            clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch"),
            ids=MemoryIds(identifiers=()),
            manifest={**manifest(), **changes},
            cases=("case", "second"),
        )


def test_unplanned_calls_fail_and_plan_drift_writes_nothing(tmp_path: Path) -> None:
    inputs = Inputs()
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        for command in (
            lambda: run.generate(Generator()),
            lambda: run.grade(Grader()),
            lambda: run.invalidate("", ()),
        ):
            with pytest.raises(BenchError):
                command()
        inputs.value["seed"] = "changed"
        with pytest.raises(BenchError, match="drift"):
            run.plan()
        assert run.state() == ""


def test_generation_and_grade_retries_never_repeat_committed_work(tmp_path: Path) -> None:
    inputs, generator = Inputs(), Generator()
    generator.interrupt = False

    class InterruptedGrader:
        def grade(self, value: Generation) -> Grade:
            if value.case_id == "second":
                raise BenchError("grader_unavailable")
            return Grader().grade(value)

    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        run.plan()
        run.generate(generator)
        run.generate(generator)
        assert generator.calls == ["case", "second"]
        grading = engine(files, inputs, "grader")
        with pytest.raises(BenchError):
            grading.grade(InterruptedGrader())
        assert grading.state() == "grading"
        grading.grade(Grader())
        assert len(files.read_valid(run.run_id, "grades")) == 2
        grading.invalidate("review", ("ref",))
        with pytest.raises(BenchError):
            grading.invalidate("other review", ("other ref",))


def test_wrong_provider_identity_is_never_committed(tmp_path: Path) -> None:
    class WrongGenerator:
        def generate(self, case_id: str, repeat: int) -> Generation:
            from dataclasses import replace

            generator = Generator()
            return replace(generator.generate(case_id, repeat), case_id="other")

    class WrongGrader:
        def grade(self, value: Generation) -> Grade:
            return Grade("other", value.repeat, (), None, 0, 0)

    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        run.plan()
        with pytest.raises(BenchError):
            run.generate(WrongGenerator())
        assert not files.read_valid(run.run_id, "generations")
        generator = Generator()
        generator.interrupt = False
        run.generate(generator)
        with pytest.raises(BenchError):
            engine(files, Inputs(), "grader").grade(WrongGrader())
        assert not files.read_valid(run.run_id, "grades")


@pytest.mark.parametrize("changes", [{"run_id": "other"}, {"case_id": "unknown"}, {"repeat": 2}])
def test_unplanned_stored_record_is_integrity_error(tmp_path: Path, changes: dict[str, object]) -> None:
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        run.plan()
        generator = Generator()
        row = generator.generate("case", 0).record(run.run_id)
        files.append(run.run_id, "generations", {**row, **changes})
        with pytest.raises(BenchError, match="integrity"):
            run.generate(generator)


def test_incomplete_generation_commit_refuses_grading(tmp_path: Path) -> None:
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        run.plan()
        files.append(run.run_id, "state", {"state": "generating"})
        files.append(run.run_id, "state", {"state": "generated", "generations_sha256": "changed"})
        with pytest.raises(BenchError, match="integrity"):
            run.grade(Grader())
        with pytest.raises(BenchError, match="integrity"):
            run.generate(Generator())


def test_commit_digest_mismatch_and_duplicate_grades_are_integrity_errors(tmp_path: Path) -> None:
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        run.plan()
        files.append(run.run_id, "state", {"state": "generating"})
        for case_id in ("case", "second"):
            generator = Generator()
            generator.interrupt = False
            files.append(run.run_id, "generations", generator.generate(case_id, 0).record(run.run_id))
        files.append(run.run_id, "state", {"state": "generated", "generations_sha256": "changed"})
        with pytest.raises(BenchError, match="integrity"):
            run.grade(Grader())


def test_missing_grade_after_completion_never_replays_as_success(tmp_path: Path) -> None:
    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        run.plan()
        generator = Generator()
        generator.interrupt = False
        run.generate(generator)
        files.append(run.run_id, "state", {"state": "grading"})
        files.append(run.run_id, "state", {"state": "graded"})
        with pytest.raises(BenchError, match="integrity"):
            engine(files, Inputs(), "grader").grade(Grader())


def test_report_commits_manifest_last_and_resumes_after_publication_failure(tmp_path: Path) -> None:
    from collections.abc import Mapping

    class Reporter:
        def build(
            self,
            run_id: str,
            manifest: Mapping[str, object],
            generations: tuple[Generation, ...],
            grades: tuple[Grade, ...],
        ) -> Mapping[str, bytes]:
            assert len(generations) == len(grades) == 2
            return {
                "run_manifest.json": b'{"status":"complete"}',
                "aggregate.json": b"{}",
                "case_results.jsonl": b"{}\n",
                "privacy_scan.json": b"{}",
                "REPORT.md": b"engineering",
            }

    class InterruptedPublication:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def write_atomic(self, path: Path, data: bytes) -> None:
            self.paths.append(path.name)
            if path.name == "privacy_scan.json":
                raise BenchError("target_unreadable", exit_code=6)

    inputs, generator = Inputs(), Generator()
    generator.interrupt = False
    with FileArtifactStore(tmp_path / "runs", max_line_bytes=4096, max_records=16) as files:
        run = engine(files, inputs)
        run.plan()
        run.generate(generator)
        grading = engine(files, inputs, "grader")
        grading.grade(Grader())
        interrupted = InterruptedPublication()
        with pytest.raises(BenchError):
            grading.report(Reporter(), interrupted)
        assert "run_manifest.json" not in interrupted.paths
        assert grading.state() == "graded"
        with FileArtifactStore(tmp_path / "results", max_line_bytes=4096, max_records=16) as public:
            grading.report(Reporter(), public)
            assert grading.state() == "reported"
            grading.report(Reporter(), public)
            assert (tmp_path / "results" / run.run_id / "run_manifest.json").is_file()


def test_invalid_report_does_not_mark_the_run_reported(tmp_path: Path) -> None:
    from collections.abc import Mapping

    class BadReporter:
        def build(
            self,
            run_id: str,
            manifest: Mapping[str, object],
            generations: tuple[Generation, ...],
            grades: tuple[Grade, ...],
        ) -> Mapping[str, bytes]:
            return {}

    with FileArtifactStore(tmp_path, max_line_bytes=4096, max_records=16) as files:
        run = engine(files, Inputs())
        with pytest.raises(BenchError, match="invalid_transition"):
            run.report(BadReporter(), files)
        run.plan()
        generator = Generator()
        generator.interrupt = False
        run.generate(generator)
        grading = engine(files, Inputs(), "grader")
        files.append(run.run_id, "state", {"state": "grading"})
        files.append(run.run_id, "state", {"state": "graded"})
        with pytest.raises(BenchError, match="integrity"):
            grading.report(BadReporter(), files)
        for case_id in ("case", "second"):
            files.append(run.run_id, "grades", Grade(case_id, 0, (), None, 0, 0).record(run.run_id))
        with pytest.raises(BenchError, match="case_invalid"):
            grading.report(BadReporter(), files)
        assert grading.state() == "graded"
