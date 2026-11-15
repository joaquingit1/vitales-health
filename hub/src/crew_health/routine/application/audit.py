from crew_health.kernel.audit import AuditEntry, AuditPolicy
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.deps import RoutineDeps


def audit_event_types() -> tuple[str, ...]:
    return tuple(
        "routine." + code
        for code in (
            "care_read",
            "binding_unit_mismatch",
            "input_rejected",
            "capacity",
            "mission_unknown",
            "assistant_input_ignored",
            "origin_rejected",
            "not_on_roster",
            "roster_unavailable",
            "late_input",
            "question_set_unknown",
            "unsupported_binding",
            "evaluation_failed",
            "input_unreadable",
            "assistant_rejected",
            "foreign_mission",
            "occurrence_not_created",
            "overlay_capacity",
            "config_invalid",
            "invalid_body",
            "invalid_transition",
            "stale_version",
            "config_version_mismatch",
            "action_not_allowed",
            "unauthorized",
            "not_found",
            "duplicate_override",
            "mission_unavailable",
        )
    )


SOURCE_KINDS = frozenset({"warning", "mission_event", "overlay", "observation", "answer", "person"})


def audit_policies() -> tuple[AuditPolicy, ...]:
    """SYSTEM 5.8: one declared policy per routine audit event type. The platform audit log refuses any entry
    whose type has no registered policy (INVALID_VALUE), so module.register declares every one of them."""
    return tuple(
        AuditPolicy(
            event_type,
            {"code": frozenset({event_type.removeprefix("routine.")}), "source_kind": SOURCE_KINDS},
            frozenset(),
            {},
        )
        for event_type in audit_event_types()
    )


def audit(deps: RoutineDeps, uow: UnitOfWork, code: str, source_kind: str = "person") -> None:
    event_type = code if code.startswith("routine.") else "routine." + code
    uow.audit(
        AuditEntry(
            deps.ids.new("aud"),
            event_type,
            1,
            uow.actor,
            {},
            uow.correlation_id,
            None,
            {"code": event_type.removeprefix("routine."), "source_kind": source_kind},
        )
    )
