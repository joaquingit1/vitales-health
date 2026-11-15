"""Supplies release checks S-L1 to S-L7 (E32 8.3), pure over decoded documents."""

from __future__ import annotations

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.document import (
    Issue,
    KitSupplies,
    read_kit_supplies,
    read_plans,
    read_threshold,
    read_words,
    resolve_needs,
)
from crew_health.supplies.domain.model import Need
from crew_health.supplies.domain.quantity import MAX_COVER, floor_ratio

_DRILLS = "drill_scenarios"


def _skills(skills_catalog: object) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
    """Skill IDs and every (supply_id, pointer) a drill scenario needs."""
    if not isinstance(skills_catalog, dict):
        return frozenset(), ()
    raw = skills_catalog.get("skills")
    skills = [s for s in (raw if isinstance(raw, list) else []) if isinstance(s, dict)]
    ids = frozenset(str(s.get("skill_id")) for s in skills)
    needs: list[tuple[str, str]] = []
    for s, skill in enumerate(skills):
        drills = skill.get(_DRILLS)
        for i, drill in enumerate(drills if isinstance(drills, list) else []):
            listed = drill.get("needs_supplies") if isinstance(drill, dict) else None
            for k, supply in enumerate(listed if isinstance(listed, list) else []):
                needs.append((str(supply), f"/skills/{s}/{_DRILLS}/{i}/needs_supplies/{k}"))
    return ids, tuple(needs)


def _cover_bounds(kit: KitSupplies, needs: list[Need]) -> list[Issue]:
    """A set packed quantity whose cover over the smallest set per-use amount of its supply passes 2^53 uses, the
    exact integer range of a JSON number (E32-T-22): refused here, so no cover read ever overflows."""
    smallest: dict[str, Quantity] = {}
    for need in needs:
        best = smallest.get(need.supply_id)
        if (
            need.per_use is not None
            and need.per_use.value > 0
            and (best is None or need.per_use.value < best.value)
        ):
            smallest[need.supply_id] = need.per_use
    issues: list[Issue] = []
    for environment_id in sorted(kit.stock):
        for supply_id, row in sorted(kit.stock[environment_id].items()):
            per_use = smallest.get(supply_id)
            if row.packed is None or per_use is None or per_use.unit != row.packed.unit:
                continue
            if floor_ratio(row.packed, per_use) > MAX_COVER:
                issues.append(Issue("S-DOC", "kit-map", row.pointer + "/quantity", "cover past 2^53 uses"))
    return issues


def release_findings(
    kit_map: object,
    onboard_plans: object | None,
    warning_rules: object | None,
    ui_copy: object | None,
    skills_catalog: object | None,
) -> tuple[Issue, ...]:
    """Every finding of one candidate release. Absent documents are only checked where kit-map needs them."""
    kit, issues = read_kit_supplies(kit_map)
    found = list(issues)
    supplies = {s.supply_id: s for s in kit.supplies}
    if onboard_plans is not None:
        plans, plan_issues = read_plans(onboard_plans)
        found.extend(plan_issues)
        steps, need_issues = resolve_needs(plans, supplies)
        found.extend(need_issues)
        found.extend(_cover_bounds(kit, [n for _, _, needs in steps for n in needs]))
    if warning_rules is not None:
        found.extend(read_threshold(warning_rules)[1])
    if kit.supplies:
        if ui_copy is None:
            found.append(Issue("S-L7", "ui-copy", "/supply_words", "supplies listed but no ui-copy"))
        else:
            found.extend(read_words(ui_copy)[1])
    if skills_catalog is not None:
        skill_ids, drill_needs = _skills(skills_catalog)
        for i, skill in enumerate(kit.candidate_skills):
            if skill not in skill_ids:
                found.append(
                    Issue("S-L2", "kit-map", f"/candidate_skills/{i}", "candidate skill not in skills")
                )
        for supply_id, pointer in drill_needs:
            if supply_id not in supplies:
                found.append(Issue("S-L5", "skills-catalog", pointer, "drill needs a supply not in kit-map"))
    elif kit.candidate_skills:
        found.append(Issue("S-L2", "kit-map", "/candidate_skills", "candidate skills but no skills catalog"))
    return tuple(found)
