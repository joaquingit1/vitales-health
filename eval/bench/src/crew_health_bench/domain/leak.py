from __future__ import annotations

from dataclasses import dataclass

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.text import fingerprints, normalized


@dataclass(frozen=True, slots=True)
class Fingerprint:
    case_ref: str
    shingles: frozenset[str]
    canaries: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.case_ref or (not self.shingles and not self.canaries):
            raise BenchError("case_invalid")
        if any(not normalized(canary) for canary in self.canaries):
            raise BenchError("case_invalid")


@dataclass(frozen=True, slots=True)
class LeakFinding:
    target: str
    kind: str
    case_ref: str
    jaccard: float


def scan_text(
    content: str,
    holdout: tuple[Fingerprint, ...],
    *,
    fp_salt: str,
    leak_jaccard: float,
    target: str,
) -> tuple[LeakFinding, ...]:
    if not holdout or not fp_salt or not 0 < leak_jaccard <= 1:
        raise BenchError("case_invalid")
    grams = fingerprints(content, fp_salt=fp_salt)
    value = normalized(content)
    findings: list[LeakFinding] = []
    for item in holdout:
        if any(canary and normalized(canary) in value for canary in item.canaries):
            findings.append(LeakFinding(target, "canary", item.case_ref, 1.0))
        if item.shingles:
            overlap = len(grams & item.shingles)
            containment = overlap / len(item.shingles)
            if containment >= leak_jaccard:
                findings.append(
                    LeakFinding(target, "containment", item.case_ref, overlap / len(grams | item.shingles))
                )
    return tuple(findings)
