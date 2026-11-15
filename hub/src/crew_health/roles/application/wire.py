"""Contract shapes (role-assignment, skill-record, drill schemas), built from an allow list of fields only."""

from __future__ import annotations

from datetime import UTC, datetime

from crew_health.roles.domain.drill import STORES_MEASUREMENTS, Drill
from crew_health.roles.domain.freshness import SkillRecord
from crew_health.roles.domain.lifecycle import Assignment
from crew_health.roles.domain.rules import RolesConfig


def instant(value: datetime) -> str:
    value = value.astimezone(UTC)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def opt_instant(value: datetime | None) -> str | None:
    return None if value is None else instant(value)


def assignment(a: Assignment, allowed: list[str]) -> dict[str, object]:
    return {
        "role_assignment_id": a.role_assignment_id,
        "revision": a.revision,
        "help_session_id": a.context_id if a.context_kind == "help_session" else None,
        "plan_run_id": a.context_id if a.context_kind == "plan_run" else None,
        "role_id": a.role_id,
        "label": a.label,
        "person": {"person_id": a.person_id, "display_name": a.person_display_name},
        "skill_id": a.skill_id,
        "task_text": a.task_text,
        "reason_code": a.reason_code,
        "reason_text": a.reason_text,
        "context_reasons": [{"reason_code": r.reason_code, "text": r.text} for r in a.context_reasons],
        "state": a.state,
        "proposed_at": instant(a.proposed_at),
        "accepted_at": opt_instant(a.accepted_at),
        "changed_by": None
        if a.changed_by is None
        else {"kind": a.changed_by.kind, "id": a.changed_by.id, "display_name": a.changed_by.display_name},
        "changed_at": opt_instant(a.changed_at),
        "replaced_by_id": a.replaced_by_id,
        "allowed_actions": allowed,
    }


def skill_record(
    record: SkillRecord, cfg: RolesConfig, display_name: str, origin: str, label: str | None
) -> dict[str, object]:
    skill = cfg.skills[record.skill_id]
    last = record.last
    return {
        "person": {"person_id": record.person_id, "display_name": display_name},
        "skill_id": record.skill_id,
        "skill_name": skill.name,
        "trained_at": opt_instant(record.trained_at),
        "last_practiced_at": None if last is None else instant(last.practiced_at),
        "last_practiced_label": None if last is None else (label if label is not None else cfg.words.unknown),
        "source": None if last is None else last.source,
        "source_ref_id": None if last is None else last.source_ref_id,
        "origin_mode": origin,
        "freshness": record.freshness,
        "freshness_text": cfg.words.freshness_words[record.freshness],
        "freshness_basis": skill.freshness.basis,
        "freshness_to_be_set_by": skill.freshness.to_be_set_by,
        "due_at": opt_instant(record.due_at),
        "stale_at": opt_instant(record.stale_at),
    }


def drill(d: Drill, names: dict[str, str]) -> dict[str, object]:
    return {
        "drill_id": d.drill_id,
        "revision": d.revision,
        "scenario_id": d.scenario_id,
        "skill_ids": list(d.skill_ids),
        "participants": [{"person_id": p, "display_name": names[p]} for p in d.participant_ids],
        "routine_occurrence_id": d.routine_occurrence_id,
        "state": d.state,
        "started_at": instant(d.started_at),
        "completed_at": opt_instant(d.completed_at),
        "abandoned_at": opt_instant(d.abandoned_at),
        "stores_measurements": STORES_MEASUREMENTS,
        "origin_mode": d.origin_mode,
        "allowed_actions": ["complete"] if d.state == "running" else [],
    }
