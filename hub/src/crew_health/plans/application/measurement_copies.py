# Consumer owned on main; importing it breaks plans-independent-measurement (DECISIONS A1).
# Follow-up F1 makes these classes importable.
"""Exact structural copy of measurement's gap-plan seam."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GapPlan:
    plan_id: str
    check_another_way_step_ids: tuple[str, ...]


class GapPlanPort(Protocol):
    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None: ...
