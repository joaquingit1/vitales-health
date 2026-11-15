"""Clinical authority (SYSTEM 8.4): assignment, competence and authorization stay separate; missing grants deny.

Practices, drills and patches are never inputs here (I-A1): authority follows only from the roster rank or from an
accepted medical_officer duty while every officer is certainly unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from crew_health.roles.domain.engine import Person
from crew_health.roles.domain.lifecycle import Assignment
from crew_health.roles.domain.rules import RoleRules

type AuthorityKind = Literal["crew_medical_officer", "acting_medical_officer", "none"]
type AuthorityAction = Literal[
    "urgency_disposition", "dose_confirmation", "record_review", "clinician_assessed"
]


@dataclass(frozen=True, slots=True)
class OfficerStatus:
    rank: int
    person_id: str | None
    unavailable: bool
    certain: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Authority:
    kind: AuthorityKind
    role_assignment_id: str | None
    reason: str


def officer_statuses(
    rules: RoleRules,
    people: Sequence[Person],
    subject_person_id: str | None,
    locations: Mapping[str, str | None],
) -> tuple[OfficerStatus, ...]:
    """Each officer_rank step: unavailable when the subject, away from the subject's location or on a walk.

    Conflicting facts (an active walk while the location says beside the subject) make the officer unavailable
    for on-site proposals but not certainly unavailable, so no acting grant follows from them (fail closed).
    Sleep never makes an officer unavailable for authority: role-rules lists no such overlay (SYSTEM 8.4 rule 2).
    """
    subject_location = locations.get(subject_person_id) if subject_person_id is not None else None
    statuses: list[OfficerStatus] = []
    for step in rules.cascade:
        if step.kind != "officer_rank" or step.rank is None:
            continue
        officer = next((p for p in people if p.officer_rank == step.rank), None)
        if officer is None:
            statuses.append(OfficerStatus(step.rank, None, True, True, "not_on_roster"))
            continue
        if officer.person_id == subject_person_id:
            statuses.append(OfficerStatus(step.rank, officer.person_id, True, True, "officer_is_subject"))
            continue
        where = locations.get(officer.person_id)
        both_known = where is not None and subject_location is not None
        if officer.on_eva:
            conflict = officer.location_conflict or (both_known and where == subject_location)
            reason = "location_conflict" if conflict else "officer_on_eva"
            statuses.append(OfficerStatus(step.rank, officer.person_id, True, not conflict, reason))
        elif both_known and where != subject_location:
            statuses.append(OfficerStatus(step.rank, officer.person_id, True, True, "officer_away"))
        else:
            statuses.append(OfficerStatus(step.rank, officer.person_id, False, True, "available"))
    return tuple(statuses)


def acting_condition(statuses: Sequence[OfficerStatus]) -> bool:
    """Design 3.8 (c) and (d): every officer_rank step certainly unavailable."""
    return bool(statuses) and all(s.unavailable and s.certain for s in statuses)


def decide_authority(
    *,
    person_id: str,
    subject_person_id: str,
    action: AuthorityAction,
    person_rank: int | None,
    acting: Assignment | None,
    context_open: bool,
    statuses: Sequence[OfficerStatus],
) -> Authority:
    if person_id == subject_person_id:
        return Authority("none", None, "own_case")
    if person_rank in (1, 2):
        return Authority("crew_medical_officer", None, "roster_rank")
    if action == "clinician_assessed":
        return Authority("none", None, "needs_crew_medical_officer")
    if (
        acting is None
        or acting.acting_since is None
        or acting.acting_ended_at is not None
        or not acting.current
    ):
        return Authority("none", None, "no_grant")
    if acting.person_id != person_id or acting.state != "accepted" or acting.role_id != "medical_officer":
        return Authority("none", None, "no_grant")
    if not context_open:
        return Authority("none", None, "context_closed")
    if not acting_condition(statuses):
        return Authority("none", None, "officer_available")
    return Authority("acting_medical_officer", acting.role_assignment_id, "accepted_medical_officer_duty")
