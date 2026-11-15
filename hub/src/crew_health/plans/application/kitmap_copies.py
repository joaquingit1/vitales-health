# Consumer owned on main; importing it breaks plans-independent-kitmap (DECISIONS A1).
# Follow-up F1 makes these classes importable.
"""Exact structural copies of kitmap's planning seam."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type Snap = UnitOfWork | ReadSnapshot


class KitMapReleaseView(Protocol):
    @property
    def release_id(self) -> str: ...
    @property
    def has_onboard_plans(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class KitMapContext:
    snapshot: Snap
    release: KitMapReleaseView
    environment_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class SupplyAddition:
    supply_id: str
    quantity: Quantity


@dataclass(frozen=True, slots=True)
class SkillAddition:
    skill_id: str
    person_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanningDelta:
    add_instruments: tuple[str, ...]
    add_supplies: tuple[SupplyAddition, ...]
    add_skills: tuple[SkillAddition, ...]


@dataclass(frozen=True, slots=True)
class PlanColumns:
    plan_id: str | None
    written_for: Literal["condition", "family", "flow"] | None
    plan_aboard: bool


@dataclass(frozen=True, slots=True)
class PlanChange:
    plan_id: str
    condition_id: str | None
    flow_id: str | None
    before_plan_aboard: bool
    before_waits_for_mo: bool
    after_plan_aboard: bool
    after_waits_for_mo: bool


class PlanColumnsPort(Protocol):
    def row_columns(
        self, ctx: KitMapContext, *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]: ...
    def plans_changed(
        self, ctx: KitMapContext, *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]: ...
