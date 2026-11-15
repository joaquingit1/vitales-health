"""Drills (E31 design 3.6, CONTRACTS 24): running, then completed or abandoned; nothing numeric is ever kept."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.roles.domain.errors import BadParticipants, InvalidDrill, NotRunning
from crew_health.roles.domain.freshness import Practice, period
from crew_health.roles.domain.rules import Calendar, PracticeRules, SkillDef

type DrillState = Literal["running", "completed", "abandoned"]
STORES_MEASUREMENTS = False  # EI10: a constant, never a setting


@dataclass(frozen=True, slots=True)
class Drill:
    drill_id: str
    revision: int
    scenario_id: str
    skill_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    completed_ids: tuple[str, ...]
    routine_occurrence_id: str | None
    state: DrillState
    started_at: datetime
    completed_at: datetime | None
    abandoned_at: datetime | None
    origin_mode: Literal["LIVE", "SIM"]
    mission_id: str
    started_by: str
    day_end: datetime
    config_release_id: str


def needed_supplies(
    skills: Mapping[str, SkillDef], scenario_id: str, skill_ids: Sequence[str]
) -> tuple[str, ...]:
    """Every requested skill must list the scenario; supplies are the union over the credited skills."""
    if len(set(skill_ids)) != len(skill_ids):
        raise InvalidDrill("/skill_ids", "repeated skill")
    supplies: set[str] = set()
    for index, skill_id in enumerate(skill_ids):
        skill = skills.get(skill_id)
        if skill is None or scenario_id not in skill.drill_scenarios:
            raise InvalidDrill(f"/skill_ids/{index}", "skill does not list this scenario")
        supplies.update(skill.drill_scenarios[scenario_id].needs_supplies)
    return tuple(sorted(supplies))


def abandon_if_ended(drill: Drill, now: datetime) -> Drill | None:
    """A running drill is abandoned at the end of the mission day it started on; abandoned_at is that day end."""
    if drill.state != "running" or now < drill.day_end:
        return None
    return replace(drill, state="abandoned", abandoned_at=drill.day_end, revision=drill.revision + 1)


def complete(drill: Drill, participant_ids: Sequence[str] | None, now: datetime) -> Drill:
    if drill.state != "running":
        raise NotRunning({"drill_id": drill.drill_id, "state": drill.state})
    chosen = tuple(drill.participant_ids) if participant_ids is None else tuple(participant_ids)
    if not chosen or len(set(chosen)) != len(chosen) or not set(chosen) <= set(drill.participant_ids):
        raise BadParticipants(drill.drill_id)
    ordered = tuple(p for p in drill.participant_ids if p in chosen)
    return replace(
        drill, state="completed", completed_at=now, completed_ids=ordered, revision=drill.revision + 1
    )


@dataclass(frozen=True, slots=True)
class Participation:
    drill_id: str
    person_id: str
    completed_at: datetime
    core_fresh: bool


@dataclass(frozen=True, slots=True)
class PatchAward:
    award_id: str
    kind: str
    threshold: int | None
    patch_design_id: str
    reached_by_drill_id: str


def core_fresh_at(
    person_drill_practices: Sequence[Practice],
    core_skill_ids: Sequence[str],
    skills: Mapping[str, SkillDef],
    calendar: Calendar,
    at: datetime,
) -> bool:
    """Every core skill has a drill practice of this person at or before `at` whose record is fresh at `at`."""
    if not core_skill_ids:
        return False
    for skill_id in core_skill_ids:
        skill = skills.get(skill_id)
        dated = [
            p.practiced_at for p in person_drill_practices if p.skill_id == skill_id and p.source == "drill"
        ]
        dated = [d for d in dated if d <= at]
        if skill is None or not dated:
            return False
        if at >= max(dated) + period(skill.freshness, calendar) * skill.freshness.due_after_periods:
            return False
    return True


def patches(
    person_id: str,
    mission_id: str,
    origin: str,
    participations: Sequence[Participation],
    rules: PracticeRules,
) -> tuple[PatchAward, ...]:
    """Practice patches from completed drill participations only (I-D3); never from events or preflight."""
    ordered = sorted(
        (p for p in participations if p.person_id == person_id), key=lambda p: (p.completed_at, p.drill_id)
    )
    awards: list[PatchAward] = []
    for kind, threshold, design_id in rules.patch_designs:
        reached: Participation | None = None
        if kind == "first_drill" and ordered:
            reached = ordered[0]
        elif kind == "drill_count" and threshold is not None and len(ordered) >= threshold:
            reached = ordered[threshold - 1]
        elif kind == "skill_fresh":
            reached = next((p for p in ordered if p.core_fresh), None)
        if reached is None:
            continue
        key = "|".join((person_id, mission_id, origin, kind, "" if threshold is None else str(threshold)))
        award_id = "ppa_" + hashlib.sha256(key.encode()).hexdigest()[:16]
        awards.append(PatchAward(award_id, kind, threshold, design_id, reached.drill_id))
    return tuple(awards)
