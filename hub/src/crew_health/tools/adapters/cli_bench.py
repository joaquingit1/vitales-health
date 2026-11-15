from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Protocol

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.settings import HubSettings
from crew_health.tools.domain.errors import ToolsInvalid


def binary_metrics(
    returned_ids: tuple[str, ...], relevant_passage_ids: frozenset[str], *, k: int, k_max: int
) -> tuple[Decimal, Decimal, Decimal]:
    """Per answerable query only. Failed executions contribute the empty ranking to its fixed population."""
    if (
        type(k) is not int
        or type(k_max) is not int
        or not 1 <= k <= k_max
        or len(returned_ids) > k_max
        or not 1 <= len(relevant_passage_ids) <= k_max
    ):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    selected = tuple(dict.fromkeys(returned_ids))[:k]
    hits = len(set(selected).intersection(relevant_passage_ids))
    with localcontext() as context:
        context.prec = 28
        recall = Decimal(hits) / len(relevant_passage_ids)
        precision = Decimal(hits) / len(selected) if selected else Decimal(0)
        gain = sum(
            (
                Decimal(2).ln() / Decimal(rank + 1).ln()
                for rank, passage in enumerate(selected, 1)
                if passage in relevant_passage_ids
            ),
            start=Decimal(0),
        )
        ideal = sum(
            (
                Decimal(2).ln() / Decimal(rank + 1).ln()
                for rank in range(1, min(k, len(relevant_passage_ids)) + 1)
            ),
            start=Decimal(0),
        )
        return recall, gain / ideal, precision


def no_answer_accuracy(statuses: tuple[str, ...], *, max_queries: int) -> Decimal | None:
    if max_queries < 1 or len(statuses) > max_queries:
        raise ToolsInvalid("PAYLOAD_TOO_LARGE")
    if not statuses:
        return None
    with localcontext() as context:
        context.prec = 28
        return Decimal(statuses.count("no_result")) / len(statuses)


@dataclass(frozen=True, slots=True)
class DevFile:
    path: str
    raw: bytes


class ManifestMetadata(Protocol):
    @property
    def root(self) -> str: ...
    @property
    def files(self) -> Mapping[str, str]: ...


class DevManifestReader[T: ManifestMetadata]:
    """Dev restrictions and reads only. E18's FreezeLibrary owns manifest and path verification."""

    def __init__(
        self,
        repository_root: Path,
        settings: HubSettings,
        *,
        verify_tree: Callable[[Path, T], Sequence[object]],
    ) -> None:
        self._repository = repository_root
        self._verify = verify_tree
        self._max_queries = settings.integer("tools.bench.max_queries")
        self._max_bytes = int(settings.quantity("tools.max_schema_bytes").value)
        self._total_bytes = int(settings.quantity("tools.rss_bytes").value)

    def load(self, manifest: T, *, profile: str) -> tuple[DevFile, ...]:
        if profile != "eval" or manifest.root != "eval/bench/cases/dev":
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if not 1 <= len(manifest.files) <= self._max_queries * 2:
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        if any(not relative.endswith((".case.json", ".labels.json")) for relative in manifest.files):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        root = self._repository / manifest.root
        if self._verify(root, manifest):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        checked: list[DevFile] = []
        remaining = self._total_bytes
        for relative in sorted(manifest.files):
            try:
                with (root / relative).open("rb") as source:
                    raw = source.read(min(self._max_bytes, remaining) + 1)
            except OSError as error:
                raise StorageUnavailable("io_error") from error
            if len(raw) > min(self._max_bytes, remaining):
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            remaining -= len(raw)
            checked.append(DevFile(relative, raw))
        return tuple(checked)


@dataclass(frozen=True, slots=True)
class RetrievalLabel:
    query_case_id: str
    relevant_passage_ids: frozenset[str]
    no_answer: bool


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    answerable_count: int
    no_answer_count: int
    recall: Decimal | None
    ndcg: Decimal | None
    support_precision: Decimal | None
    no_answer_accuracy: Decimal | None
    failures: int


def benchmark_metrics(
    labels: tuple[RetrievalLabel, ...],
    rankings: Mapping[str, tuple[str, tuple[str, ...]]],
    *,
    k: int,
    max_queries: int,
    k_max: int,
) -> BenchmarkResult:
    if not 1 <= len(labels) <= max_queries or len({label.query_case_id for label in labels}) != len(labels):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    if set(rankings) - {label.query_case_id for label in labels}:
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    totals = [Decimal(0), Decimal(0), Decimal(0)]
    answerable = failures = 0
    no_answers: list[str] = []
    for label in labels:
        if type(label.no_answer) is not bool or label.no_answer != (not label.relevant_passage_ids):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        status, ids = rankings.get(label.query_case_id, ("unavailable", ()))
        if status not in {"ok", "no_result", "unavailable", "error"} or (status != "ok" and ids):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        failures += int(status in {"unavailable", "error"})
        if label.no_answer:
            no_answers.append(status)
            continue
        answerable += 1
        scores = binary_metrics(ids, label.relevant_passage_ids, k=k, k_max=k_max)
        totals = [total + score for total, score in zip(totals, scores, strict=True)]
    averages = tuple(total / answerable if answerable else None for total in totals)
    return BenchmarkResult(
        answerable,
        len(no_answers),
        averages[0],
        averages[1],
        averages[2],
        no_answer_accuracy(tuple(no_answers), max_queries=max_queries),
        failures,
    )
