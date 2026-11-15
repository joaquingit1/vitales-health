"""The closed PlanRun wire projection, validated at its boundary."""

from collections.abc import Callable, Sequence
from datetime import datetime

from crew_health.contracts_gen.schemas.plan_run_schema import PlanRun as WirePlanRun
from crew_health.kernel.uow import Actor
from crew_health.plans.domain.run import PlanRun, RunRecheck


def instant(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def actor_wire(actor: Actor | None) -> dict[str, str] | None:
    return None if actor is None else {"kind": actor.kind, "id": actor.id}


def run_wire(
    run: PlanRun,
    *,
    subject_display_name: str,
    allowed_actions: Sequence[str],
    due_text_for: Callable[[RunRecheck], str],
) -> dict[str, object]:
    close = run.close
    body = {
        "plan_run_id": run.plan_run_id,
        "revision": run.revision,
        "plan_id": run.plan_id,
        "config_pins": [{"name": name, "version": version} for name, version in run.pins],
        "subject": {"person_id": run.subject_person_id, "display_name": subject_display_name},
        "episode_id": run.episode_id,
        "help_session_id": run.help_session_id,
        "environment_id": run.environment_id,
        "state": run.state,
        "opened_by": actor_wire(run.opened_by),
        "opened_at": instant(run.opened_at),
        "steps": [
            {
                "step_id": s.step_id,
                "stage_id": s.stage_id,
                "state": s.state,
                "by": actor_wire(s.by),
                "at": instant(s.at),
                "substitute_step_id": s.substitute_step_id,
                "guided_check_id": s.guided_check_id,
                "observation_ids": list(s.observation_ids),
                "supply_use_ids": list(s.supply_use_ids),
            }
            for s in run.steps
        ],
        "rechecks": [
            {
                "step_id": r.step_id,
                "due_at": instant(r.due_at),
                "due_text": due_text_for(r),
                "done_at": instant(r.done_at),
                "observation_ids": list(r.observation_ids),
            }
            for r in run.rechecks
        ],
        "escalations": [
            {"step_id": e.step_id, "help_session_id": e.help_session_id, "at": instant(e.at)}
            for e in run.escalations
        ],
        "handoff_id": run.handoff_id,
        "link_state_at_open": {
            "status": run.link_state_at_open.status,
            "gap_kind": run.link_state_at_open.gap_kind,
            "next_contact_at": instant(run.link_state_at_open.next_contact_at),
        },
        "close": None
        if close is None
        else {
            "disposition": close.disposition,
            "note": close.note,
            "by": actor_wire(close.by),
            "at": instant(close.at),
        },
        "allowed_actions": list(allowed_actions),
    }
    return WirePlanRun.model_validate(body).model_dump(mode="json", exclude_unset=True)
