"""E27 4.3 RunWhatIf: the same computation with extra instruments, supplies or skill holders.

Read only: it opens a read snapshot, holds no repository with a write method, stores nothing and sends no event
(V77, D29, KM-I7). Rings move only with instruments; supplies and skills go to their owners' computations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.errors import FieldError
from crew_health.kernel.units import Quantity, Unit
from crew_health.kitmap.application.errors import KitMapUnavailable, WhatIfInvalid
from crew_health.kitmap.application.ports import (
    CoverChange,
    CoverPlanningPort,
    KitMapContext,
    PlanChange,
    PlanningDelta,
    RosterPort,
    SkillAddition,
    SupplyAddition,
)
from crew_health.kitmap.application.queries import KitMapReads, ViewResult
from crew_health.kitmap.domain.model import ConditionEvaluation, KitMapDoc, StateCounts
from crew_health.kitmap.domain.rules import diff_states


@dataclass(frozen=True, slots=True)
class WhatIfBounds:
    """Hub settings kitmap.whatif_* (E27 section 11)."""

    max_instruments: int
    max_supplies: int
    max_skills: int
    max_people_per_skill: int
    max_quantity: Decimal
    max_quantity_scale: int


@dataclass(frozen=True, slots=True)
class WhatIfCommand:
    add_instruments: tuple[str, ...]
    add_supplies: tuple[tuple[str, Decimal], ...]
    add_skills: tuple[tuple[str, tuple[str, ...]], ...]
    environment_id: str | None


@dataclass(frozen=True, slots=True)
class WhatIfResult:
    base: ViewResult
    after: ViewResult
    added_instrument_ids: tuple[str, ...]
    ignored_instrument_ids: tuple[str, ...]
    changed: tuple[tuple[ConditionEvaluation, ConditionEvaluation], ...]
    added_supplies: tuple[tuple[str, Decimal], ...]
    added_skills: tuple[tuple[str, tuple[str, ...]], ...]
    plans_changed: tuple[PlanChange, ...]
    cover_changed: tuple[CoverChange, ...]

    @property
    def base_counts(self) -> StateCounts:
        return self.base.counts


class RunWhatIf:
    def __init__(self, reads: KitMapReads, bounds: WhatIfBounds) -> None:
        self._reads = reads
        self._bounds = bounds
        self.roster: RosterPort | None = None
        self.cover: CoverPlanningPort | None = None

    def _check_bounds(self, cmd: WhatIfCommand) -> None:
        b = self._bounds
        errors = [
            FieldError(pointer, "max_items", "too many items")
            for pointer, size, limit in (
                ("/add_instruments", len(cmd.add_instruments), b.max_instruments),
                ("/add_supplies", len(cmd.add_supplies), b.max_supplies),
                ("/add_skills", len(cmd.add_skills), b.max_skills),
            )
            if size > limit
        ]
        errors.extend(
            FieldError(f"/add_skills/{i}/person_ids", "max_items", "too many people")
            for i, (_, people) in enumerate(cmd.add_skills)
            if len(people) > b.max_people_per_skill
        )
        if errors:
            raise WhatIfInvalid(errors)

    def _validate(self, ctx: KitMapContext[KitMapDoc], cmd: WhatIfCommand) -> list[FieldError]:
        doc = ctx.release.doc
        known = {i.id for i in doc.instruments}
        errors = [
            FieldError(f"/add_instruments/{i}", "unknown", "unknown instrument")
            for i, instrument in enumerate(cmd.add_instruments)
            if instrument not in known
        ]
        seen: set[str] = set()
        for i, (supply_id, quantity) in enumerate(cmd.add_supplies):
            if supply_id not in doc.supplies or supply_id in seen:
                errors.append(
                    FieldError(f"/add_supplies/{i}/supply_id", "unknown", "unknown or repeated supply")
                )
            seen.add(supply_id)
            exponent = quantity.as_tuple().exponent
            scale = -exponent if isinstance(exponent, int) and exponent < 0 else 0
            if not (quantity.is_finite() and Decimal(0) < quantity <= self._bounds.max_quantity) or (
                scale > self._bounds.max_quantity_scale
            ):
                errors.append(FieldError(f"/add_supplies/{i}/quantity", "range", "quantity out of range"))
        errors.extend(self._validate_skills(ctx, cmd))
        return errors

    def _validate_skills(self, ctx: KitMapContext[KitMapDoc], cmd: WhatIfCommand) -> list[FieldError]:
        if not cmd.add_skills:
            return []
        if self.roster is None:
            raise KitMapUnavailable("roster unavailable for a skills what-if")
        crew = self.roster.person_ids()
        errors: list[FieldError] = []
        seen: set[str] = set()
        for i, (skill_id, people) in enumerate(cmd.add_skills):
            if skill_id not in ctx.release.skill_ids or skill_id in seen:
                errors.append(FieldError(f"/add_skills/{i}/skill_id", "unknown", "unknown or repeated skill"))
            seen.add(skill_id)
            errors.extend(
                FieldError(f"/add_skills/{i}/person_ids/{p}", "unknown", "unknown person")
                for p, person in enumerate(people)
                if person not in crew
            )
        return errors

    def execute(self, cmd: WhatIfCommand) -> WhatIfResult:
        self._check_bounds(cmd)
        with self._reads.snapshot() as snap:
            ctx = self._reads.context(snap, cmd.environment_id)
            errors = self._validate(ctx, cmd)
            if errors:
                raise WhatIfInvalid(errors)
            return self._compute(ctx, cmd)

    def _compute(self, ctx: KitMapContext[KitMapDoc], cmd: WhatIfCommand) -> WhatIfResult:
        doc = ctx.release.doc
        kit = doc.kits[ctx.environment_id]
        ignored = tuple(i for i in cmd.add_instruments if i in kit)
        added = tuple(i for i in cmd.add_instruments if i not in kit)
        base = self._reads.compute_view(ctx, kit)
        after = self._reads.compute_view(ctx, kit | frozenset(added))
        supplies = tuple(
            SupplyAddition(s, Quantity(q, Unit(doc.supplies[s].unit))) for s, q in cmd.add_supplies
        )
        delta = PlanningDelta(added, supplies, tuple(SkillAddition(s, p) for s, p in cmd.add_skills))
        return WhatIfResult(
            base=base,
            after=after,
            added_instrument_ids=added,
            ignored_instrument_ids=ignored,
            changed=diff_states(base.evaluations, after.evaluations),
            added_supplies=cmd.add_supplies,
            added_skills=cmd.add_skills,
            plans_changed=self._plans_changed(ctx, kit, delta),
            cover_changed=self._cover_changed(ctx, supplies),
        )

    def _plans_changed(
        self, ctx: KitMapContext[KitMapDoc], kit: frozenset[str], delta: PlanningDelta
    ) -> tuple[PlanChange, ...]:
        if not ctx.release.has_onboard_plans:
            return ()  # no onboard-plans document in this release: no plan can change
        plans = self._reads.plans
        if plans is None:
            raise KitMapUnavailable("onboard plans present but no plan columns provider")
        return tuple(plans.plans_changed(ctx, base_kit=kit, delta=delta))

    def _cover_changed(
        self, ctx: KitMapContext[KitMapDoc], supplies: tuple[SupplyAddition, ...]
    ) -> tuple[CoverChange, ...]:
        if not supplies:
            return ()
        if self.cover is None:
            raise KitMapUnavailable("cover planning unavailable for a supplies what-if")
        return tuple(self.cover.cover_changed(ctx, add_supplies=supplies))
