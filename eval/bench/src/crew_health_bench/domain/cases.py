from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from crew_health.kernel.units import Quantity

from crew_health_bench.domain.errors import BenchError


class Suite(StrEnum):
    NASA_SCENARIO = "nasa_scenario"
    PLAN = "plan"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    PRIVACY = "privacy"
    ROBUSTNESS = "robustness"
    HEALTHBENCH = "healthbench"


class Partition(StrEnum):
    DEV = "dev"
    HOLDOUT = "holdout"
    BENCHMARK = "benchmark"


@dataclass(frozen=True, slots=True)
class CaseKey:
    family_id: str
    source_vignette_id: str
    patient_id: str
    episode_id: str


@dataclass(frozen=True, slots=True)
class ConfigPrecondition:
    document: str
    pointer: str
    must_be: Literal["set", "null"]


@dataclass(frozen=True, slots=True)
class SimAnswer:
    state: Literal["present", "absent", "unknown", "declined"]
    value: Quantity | None = None

    def __post_init__(self) -> None:
        if self.state != "present" and self.value is not None:
            raise BenchError("case_invalid")


@dataclass(frozen=True, slots=True)
class SimMeasurement:
    outcome: Literal["accepted", "failed", "unavailable"]
    value: Quantity | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.outcome == "accepted") != (self.value is not None):
            raise BenchError("case_invalid")
        if self.outcome == "failed" and not self.reason:
            raise BenchError("case_invalid")


@dataclass(frozen=True, slots=True)
class QuestionMatch:
    any_of: tuple[str, ...]
    none_of: tuple[str, ...]
    answer: SimAnswer


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    capability_code: str
    answer: SimMeasurement


@dataclass(frozen=True, slots=True)
class RevealTable:
    questions: tuple[QuestionMatch, ...]
    capabilities: tuple[CapabilityMatch, ...]


@dataclass(frozen=True, slots=True)
class CaseReview:
    state: Literal["unreviewed", "reviewed"]
    reviewer_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    suite: Suite
    key: CaseKey
    origin: Literal["authored", "paraphrase", "perturbation", "external"]
    derived_from: str | None
    condition_ids: tuple[str, ...]
    requires: tuple[ConfigPrecondition, ...]
    visible: str
    revealable: RevealTable | None
    hidden_sha256: str
    canary: str
    review: CaseReview


def validate_cases(cases: tuple[Case, ...]) -> None:
    by_id = {case.case_id: case for case in cases}
    if len(by_id) != len(cases):
        raise BenchError("case_invalid")
    for case in cases:
        if (
            not case.canary
            or not case.hidden_sha256
            or not all(
                (
                    case.case_id,
                    case.key.family_id,
                    case.key.source_vignette_id,
                    case.key.patient_id,
                    case.key.episode_id,
                )
            )
        ):
            raise BenchError("case_invalid")
        if case.origin in ("paraphrase", "perturbation") and case.derived_from is None:
            raise BenchError("case_invalid")
        if case.derived_from is not None:
            parent = by_id.get(case.derived_from)
            if parent is None or parent.key != case.key or parent.case_id == case.case_id:
                raise BenchError("case_invalid")
            visited = {case.case_id}
            for _ in cases:
                if parent.case_id in visited:
                    raise BenchError("case_invalid")
                visited.add(parent.case_id)
                if parent.derived_from is None:
                    break
                next_parent = by_id.get(parent.derived_from)
                if next_parent is None:
                    raise BenchError("case_invalid")
                parent = next_parent
