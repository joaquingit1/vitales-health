from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from crew_health_bench.domain.documents import integer, object_map, text, text_list
from crew_health_bench.domain.text import normalized


@dataclass(frozen=True, slots=True)
class ClaimsPolicy:
    benchmark_disclaimer: str
    forbidden_near_benchmark: tuple[str, ...]
    window_words: int
    publisher_scores: tuple[tuple[str, str], ...]

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> ClaimsPolicy:
        scores = object_map(document["publisher_scores"])
        return cls(
            text(document["benchmark_disclaimer"]),
            text_list(document["forbidden_near_benchmark"]),
            integer(document["window_words"]),
            tuple((key, text(value)) for key, value in sorted(scores.items())),
        )


@dataclass(frozen=True, slots=True)
class ClaimFinding:
    kind: str
    position: int


def lint_claims(content: str, policy: ClaimsPolicy) -> tuple[ClaimFinding, ...]:
    words = normalized(content).split()
    findings: list[ClaimFinding] = []
    for index, word in enumerate(words):
        if "healthbench" in word:
            window = " ".join(words[max(0, index - policy.window_words) : index + policy.window_words + 1])
            if any(normalized(phrase) in window for phrase in policy.forbidden_near_benchmark):
                findings.append(ClaimFinding("forbidden_near_benchmark", index))
        for model, score in policy.publisher_scores:
            if normalized(model) in word:
                window = " ".join(
                    words[max(0, index - policy.window_words) : index + policy.window_words + 1]
                )
                if normalized(score) in window:
                    findings.append(ClaimFinding("publisher_scores", index))
    return tuple(findings)
