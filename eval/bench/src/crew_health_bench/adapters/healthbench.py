from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Protocol

from crew_health.kernel.units import Quantity, Unit
from pydantic import ValidationError
from simple_evals import healthbench_eval
from simple_evals.healthbench_eval import HealthBenchEval, RubricItem, _aggregate_get_clipped_mean
from simple_evals.types import MessageList, SamplerBase, SamplerResponse, SingleEvalResult

from crew_health_bench.adapters.documents import BenchSettings, decode_json, read_json
from crew_health_bench.contracts_gen.healthbench import HealthBenchCase
from crew_health_bench.domain.documents import object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import HealthBenchVisible, OfficialGradeResult
from crew_health_bench.domain.sample import validate_grader


class GraderClient(Protocol):
    def complete(self, messages: tuple[tuple[str, str], ...], timeout: Quantity) -> str: ...


def _quantity(value: float) -> Quantity:
    number = Decimal(str(value))
    if not number.is_finite():
        raise BenchError("grader_nonfinite")
    return Quantity(number, Unit("1"))


class HealthBenchCorpus:
    def __init__(
        self, path: Path, digest: str, size_bytes: int, examples: int, settings: BenchSettings
    ) -> None:
        self.path = path
        self._cases: dict[str, HealthBenchCase] = {}
        maximum = settings.integer("bench.store.max_line_bytes")
        if size_bytes <= 0 or examples <= 0 or path.is_symlink() or not path.is_file():
            raise BenchError("dataset_unavailable")
        try:
            if path.stat().st_size != size_bytes:
                raise BenchError("dataset_size")
            checksum = sha256()
            with path.open("rb") as stream:
                for _ in range(examples):
                    raw = stream.readline(maximum + 1)
                    if not raw or len(raw) > maximum:
                        raise BenchError("dataset_line_bound")
                    checksum.update(raw)
                if stream.read(1):
                    raise BenchError("dataset_examples")
            if checksum.hexdigest() != digest:
                raise BenchError("dataset_sha256")
            self._load(examples, maximum)
        except OSError as exc:
            raise BenchError("dataset_unavailable") from exc

    def _load(self, examples: int, maximum: int) -> None:
        with self.path.open("rb") as stream:
            for _ in range(examples):
                raw = stream.readline(maximum + 1)
                try:
                    case = HealthBenchCase.model_validate(decode_json(raw))
                except ValidationError as exc:
                    raise BenchError("healthbench_case_invalid") from exc
                if not any(item.points > 0 for item in case.rubrics):
                    raise BenchError("healthbench_positive_denominator")
                if case.prompt_id in self._cases:
                    raise BenchError("duplicate_prompt_id")
                self._cases[case.prompt_id] = case

    @property
    def prompt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._cases))

    def case(self, prompt_id: str) -> HealthBenchCase:
        if prompt_id not in self._cases:
            raise BenchError("unknown_prompt_id")
        return self._cases[prompt_id]

    def visible(self, prompt_id: str) -> HealthBenchVisible:
        case = self.case(prompt_id)
        return HealthBenchVisible(case.prompt_id, tuple((m.role, m.content) for m in case.prompt))


class PreparedGrader:
    def __init__(self, candidate: str, grader: str, client: GraderClient, settings: BenchSettings) -> None:
        validate_grader(candidate, grader)
        self.client = client
        self.timeout = settings.quantity("bench.grader.call_timeout")
        self.retries = settings.integer("bench.grader.max_retries")
        self.concurrency = settings.integer("bench.grader.max_concurrency")
        self.maximum = settings.integer("bench.store.max_line_bytes")


class _BoundedSampler(SamplerBase):
    def __init__(self, prepared: PreparedGrader, rubric_count: int) -> None:
        self._prepared = prepared
        self._remaining = rubric_count * (1 + prepared.retries)
        self._lock = Lock()
        self._semaphore = BoundedSemaphore(prepared.concurrency)

    def __call__(self, message_list: MessageList) -> SamplerResponse:
        prepared = self._prepared
        with self._lock:
            if self._remaining <= 0:
                raise BenchError("grader_call_limit")
            self._remaining -= 1
        with self._semaphore:
            response = prepared.client.complete(
                tuple((row["role"], row["content"]) for row in message_list), prepared.timeout
            )
        if len(response.encode("utf-8")) > prepared.maximum:
            raise BenchError("grader_response_bound")
        return SamplerResponse(response, message_list, {})


class OfficialGrader:
    def __init__(self, corpus: HealthBenchCorpus, prepared: PreparedGrader, plan_path: Path) -> None:
        plan = read_json(plan_path, max_file_bytes=prepared.maximum)
        harness = object_map(plan["harness"])
        actual = Path(healthbench_eval.__file__).read_bytes()
        if sha256(actual).hexdigest() != text(harness["module_sha256"]):
            raise BenchError("official_harness_sha256")
        self._corpus = corpus
        self._prepared = prepared

    @property
    def corpus(self) -> HealthBenchCorpus:
        return self._corpus

    def grade(self, prompt_id: str, response: str) -> OfficialGradeResult:
        case = self._corpus.case(prompt_id)
        prepared = self._prepared
        engine = HealthBenchEval(
            _BoundedSampler(prepared, len(case.rubrics)),
            input_path=str(self._corpus.path),
            n_threads=prepared.concurrency,
        )
        metrics, explanation, items = engine.grade_sample(
            [{"role": m.role, "content": m.content} for m in case.prompt],
            response,
            case.example_tags,
            [RubricItem(r.criterion, r.points, r.tags) for r in case.rubrics],
        )
        measured = {name: _quantity(value) for name, value in sorted(metrics.items())}
        return OfficialGradeResult(prompt_id, measured["overall_score"], measured, explanation, tuple(items))

    def aggregate(self, grades: tuple[OfficialGradeResult, ...]) -> dict[str, Quantity]:
        if not grades:
            raise BenchError("no_graded_examples")
        rows = [
            SingleEvalResult(
                score=float(g.score.value),
                metrics={key: float(value.value) for key, value in g.metrics.items()},
            )
            for g in grades
        ]
        result = _aggregate_get_clipped_mean(rows)
        if result.score is None or result.metrics is None:
            raise BenchError("official_aggregate_missing")
        return {
            "score": _quantity(result.score),
            **{key: _quantity(value) for key, value in sorted(result.metrics.items())},
        }
