"""Medicines domain events (E32 4.5; R-E32-11): IDs and states only, never a dose, product, diagnosis or note
(10.3). `medicine.assessment_changed` is a crew event (crew, medical officer, operator and station sessions see it);
the proposal and administration events are person events, delivered to the subject and to crew medical officer
sessions only, never to other crew or a station (as `skill.practiced`)."""

from __future__ import annotations

import json

from crew_health.contracts_gen.enums import EventType, Role
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    LocalProjectionRegistration,
    OutboxEvent,
    ReadSnapshot,
    SessionContext,
    UnitOfWork,
)

CREW_RULE = "medicines.crew"
PERSON_RULE = "medicines.person"
POLICY_VERSION = "1"
CREW_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.OPERATOR, Role.STATION})


class CrewAudience:
    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return audience.rule == CREW_RULE and bool(session.roles & CREW_ROLES)


class PersonAudience:
    """The subject (audience ref) or a crew medical officer session; a station session never."""

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        if audience.rule != PERSON_RULE or session.kind == "station":
            return False
        return session.person_id == audience.object_ref or Role.CREW_MEDICAL_OFFICER in session.roles


def _id() -> dict[str, object]:
    return {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}


_SCHEMAS: dict[EventType, tuple[dict[str, object], tuple[str, ...]]] = {
    EventType.MEDICINE_ASSESSMENT_CHANGED: (
        {
            "run_id": _id(),
            "status": {"enum": ["queued", "running", "completed", "failed", "timed_out", "interrupted"]},
            "error_code": {"type": ["string", "null"]},
        },
        ("/run_id", "/status"),
    ),
    EventType.MEDICINE_PROPOSAL_CHANGED: (
        {
            "proposal_id": _id(),
            "subject_person_id": _id(),
            "state": {"enum": ["proposed", "refused", "confirmed", "expired", "superseded"]},
        },
        ("/proposal_id", "/subject_person_id", "/state"),
    ),
    EventType.MEDICATION_ADMINISTERED: (
        {
            "administration_id": _id(),
            "subject_person_id": _id(),
            "proposal_id": {"type": ["string", "null"]},
            "outside_engine": {"type": "boolean"},
        },
        ("/administration_id", "/subject_person_id", "/outside_engine"),
    ),
    EventType.MEDICATION_ADMINISTRATION_CORRECTED: (
        {"administration_id": _id(), "subject_person_id": _id()},
        ("/administration_id", "/subject_person_id"),
    ),
}


def projection(event: EventType) -> AudienceProjectionKey:
    return AudienceProjectionKey("medicines", event.value, 1)


def projections() -> tuple[LocalProjectionRegistration, ...]:
    out = []
    for event, (properties, required) in _SCHEMAS.items():
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(properties),
            "properties": properties,
        }
        rule = CrewAudience() if event is EventType.MEDICINE_ASSESSMENT_CHANGED else PersonAudience()
        out.append(
            LocalProjectionRegistration(
                key=projection(event),
                payload_schema_json=json.dumps(schema, sort_keys=True, separators=(",", ":")).encode(),
                required_field_pointers=required,
                rule=rule,
            )
        )
    return tuple(out)


def _emit(
    uow: UnitOfWork, event: EventType, scope: EventScope, rule: str, ref: str, data: dict[str, object]
) -> None:
    uow.emit(
        OutboxEvent(
            type=event,
            occurred_at=uow.now,
            scope=scope,
            config_release_id=uow.release.release_id,
            correlation_id=uow.correlation_id,
            data_json=json.dumps(data, sort_keys=True, separators=(",", ":")).encode(),
            audience=Audience(rule, ref, projection(event), POLICY_VERSION),
        )
    )


def assessment_changed(uow: UnitOfWork, run_id: str, status: str, error_code: str | None) -> None:
    _emit(
        uow,
        EventType.MEDICINE_ASSESSMENT_CHANGED,
        EventScope("crew", None, None),
        CREW_RULE,
        run_id,
        {"run_id": run_id, "status": status, "error_code": error_code},
    )


def proposal_changed(uow: UnitOfWork, proposal_id: str, subject: str, state: str) -> None:
    _emit(
        uow,
        EventType.MEDICINE_PROPOSAL_CHANGED,
        EventScope("person", subject, None),
        PERSON_RULE,
        subject,
        {"proposal_id": proposal_id, "subject_person_id": subject, "state": state},
    )


def administered(
    uow: UnitOfWork, administration_id: str, subject: str, proposal_id: str | None, outside_engine: bool
) -> None:
    _emit(
        uow,
        EventType.MEDICATION_ADMINISTERED,
        EventScope("person", subject, None),
        PERSON_RULE,
        subject,
        {
            "administration_id": administration_id,
            "subject_person_id": subject,
            "proposal_id": proposal_id,
            "outside_engine": outside_engine,
        },
    )


def corrected(uow: UnitOfWork, administration_id: str, subject: str) -> None:
    _emit(
        uow,
        EventType.MEDICATION_ADMINISTRATION_CORRECTED,
        EventScope("person", subject, None),
        PERSON_RULE,
        subject,
        {"administration_id": administration_id, "subject_person_id": subject},
    )
