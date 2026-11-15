from collections.abc import Mapping
from dataclasses import dataclass

from crew_health_bench.domain.cases import Case


@dataclass(frozen=True)
class VisibleCase:
    case_id: str
    seed_steps: tuple[Mapping[str, object], ...]
    opening_complaint: str
    script: tuple[Mapping[str, object], ...]
    canary: str


@dataclass(frozen=True)
class ProductCase:
    case: Case
    visible: VisibleCase
