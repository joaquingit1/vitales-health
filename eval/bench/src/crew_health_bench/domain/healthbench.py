from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from crew_health.kernel.units import Quantity


@dataclass(frozen=True)
class HealthBenchVisible:
    prompt_id: str
    messages: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class OfficialGradeResult:
    prompt_id: str
    score: Quantity
    metrics: Mapping[str, Quantity]
    explanation: str
    rubric_items: tuple[Mapping[str, object], ...]
