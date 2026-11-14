"""Escalation of sensor-limit warnings within a group (E24 3.7, DE24-7).

The group is `(sensor_type_id, channel_id)`. Equal levels coexist and each renders its own limit. A
higher crossing supersedes the lower active ones in the same decision. A lower crossing while a higher
warning is active is not raised. A level never changes: de-escalation raises a new, lower warning
linked to the one that cleared. While clears are frozen nothing is superseded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crew_health.rules.domain.definitions import WarningDefinition
from crew_health.rules.domain.vocabulary import Level
from crew_health.rules.domain.warning import Warning


@dataclass(frozen=True, slots=True)
class EscalationPlan:
    raises: tuple[WarningDefinition, ...]
    supersedes: tuple[Warning, ...]
    previous: Warning | None


def _highest(warnings: Sequence[Warning]) -> Warning | None:
    if not warnings:
        return None
    return max(warnings, key=lambda w: (w.level.rank, w.raised_at, w.warning_id))


def _candidates(crossed: Sequence[WarningDefinition], active: Sequence[Warning]) -> list[WarningDefinition]:
    """Crossed definitions that may raise: no active warning of the same key at the same or a higher level."""
    out: list[WarningDefinition] = []
    for definition in crossed:
        blockers = [w for w in active if w.definition_id == definition.id]
        if all(w.level.rank < definition.level.rank for w in blockers):
            out.append(definition)
    return sorted(out, key=lambda d: d.id)


def plan_group(
    crossed: Sequence[WarningDefinition],
    active: Sequence[Warning],
    *,
    cleared: Sequence[Warning] = (),
    frozen: bool,
) -> EscalationPlan:
    """What a sensor evaluation raises and supersedes in one group.

    `active` is the group's active warnings before the decision, `cleared` those the same decision
    clears by their own dwell. `frozen` forbids any supersession while an evaluation is pending.
    """
    cleared_ids = {w.warning_id for w in cleared}
    remaining = [w for w in active if w.warning_id not in cleared_ids]
    candidates = _candidates(crossed, remaining)
    top_active = _highest(remaining)
    raises: tuple[WarningDefinition, ...] = ()
    if candidates:
        crossed_level = max(c.level for c in candidates)
        if top_active is None or crossed_level.rank >= top_active.level.rank:
            raises = tuple(c for c in candidates if c.level is crossed_level)
    new_top = max([w.level for w in remaining] + [r.level for r in raises], default=None)
    superseded: tuple[Warning, ...] = ()
    if not frozen and new_top is not None:
        superseded = tuple(sorted((w for w in remaining if w.level.rank < new_top.rank), key=_key))
    return EscalationPlan(raises, superseded, _link(raises, superseded, cleared))


def _key(w: Warning) -> tuple[str, str]:
    return (w.definition_id, w.warning_id)


def _link(
    raises: tuple[WarningDefinition, ...], superseded: tuple[Warning, ...], cleared: Sequence[Warning]
) -> Warning | None:
    if not raises:
        return None
    level: Level = raises[0].level
    pool = list(superseded) + [w for w in cleared if w.level.rank > level.rank]
    return _highest(pool)
