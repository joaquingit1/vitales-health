from collections.abc import Mapping
from dataclasses import dataclass

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.config import _FrozenMapping
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.ids import require_id
from crew_health.platform.domain.errors import fail


@dataclass(frozen=True)
class AuditPolicy:
    event_type: str
    metadata: Mapping[str, frozenset[str | int | bool]]
    reference_fields: frozenset[str]
    reference_types: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _FrozenMapping(self.metadata))
        object.__setattr__(self, "reference_types", _FrozenMapping(self.reference_types))
        if set(self.reference_types) != set(self.reference_fields):
            raise fail(ErrorCode.CONFIG_INVALID)

    def validate(self, entry: AuditEntry) -> None:
        if entry.event_type != self.event_type or entry.event_version != 1:
            raise fail(ErrorCode.INVALID_VALUE)
        require_id(entry.event_id)
        require_id(entry.actor.id)
        require_id(entry.correlation_id)
        if entry.causation_id is not None:
            require_id(entry.causation_id)
        if set(entry.refs) != self.reference_fields or not set(entry.safe_metadata) <= self.metadata.keys():
            raise fail(ErrorCode.INVALID_VALUE)
        for value in entry.refs.values():
            require_id(value)
        for key, metadata_value in entry.safe_metadata.items():
            if metadata_value not in self.metadata[key]:
                raise fail(ErrorCode.INVALID_VALUE)


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
