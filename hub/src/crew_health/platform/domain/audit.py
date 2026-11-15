from crew_health.kernel.audit import AuditPolicy

__all__ = ["AuditPolicy", "core_policies"]


def core_policies() -> tuple[AuditPolicy, ...]:
    definitions = (
        ("observation.recorded", "record", "observation"),
        ("answer.recorded", "record", "answer"),
        ("config.release_proposed", "release_id", "config.release"),
        ("config.release_activated", "release_id", "config.release"),
        ("clock.segment", "clock_record", "clock.segment"),
        ("clock.correction", "clock_record", "clock.correction"),
        ("clock.regression", "clock_record", "clock.regression"),
        ("clock.sweep_completed", "clock_record", "clock.sweep_completed"),
        ("clock.mission_version", "clock_record", "clock.mission_version"),
        ("private.accepted", "entry", "private_reference"),
        ("private.deleted", "revocation", "private_revocation"),
    )
    policies = [
        AuditPolicy(event, {}, frozenset({field}), {field: frozenset({kind})})
        for event, field, kind in definitions
    ]
    for event in ("submitted", "cancelled", "claimed", "progressed", "succeeded", "retry_queued", "failed"):
        policies.append(AuditPolicy("task." + event, {}, frozenset({"task"}), {"task": frozenset({"task"})}))
    for phase in ("prepared", "fenced", "accepted", "completed", "cancelled"):
        policies.append(
            AuditPolicy(
                "ownership." + phase, {}, frozenset({"transfer"}), {"transfer": frozenset({"ownership"})}
            )
        )
    for result in ("pending", "completed"):
        policies.append(
            AuditPolicy(
                "evaluation." + result,
                {},
                frozenset({"input", "completion"}),
                {"input": frozenset({"observation", "answer"}), "completion": frozenset({"evaluation"})},
            )
        )
    return tuple(policies)
