"""Supplies release checks S-L1 to S-L7 (E32 8.3), pure over decoded documents."""

from __future__ import annotations

from crew_health.supplies.domain.document import (
    Issue,
    read_kit_supplies,
    read_plans,
    read_threshold,
    read_words,
    resolve_needs,
)

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
        found.extend(resolve_needs(plans, supplies)[1])
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
