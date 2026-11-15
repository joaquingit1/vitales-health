"""E02 3.1 and 3.3 consent grants: explicit, owner-made, exact, finite by absolute instants, revocable.

A grant names one subject, one recipient, one mission and origin, one object reference, one purpose, an
explicit action set and an explicit field set. Nothing is a wildcard and nothing is a default. Pure
functions only: the caller supplies the snapshot time and the clock readings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import SessionContext

type GrantState = Literal["active", "revoked", "expired", "fenced"]
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]

GRANT_STATES: tuple[GrantState, ...] = ("active", "revoked", "expired", "fenced")
ORIGIN_MODES: tuple[OriginMode, ...] = ("LIVE", "REPLAY", "SIM")
POLICY_VERSION = "e02.consent.1"
_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_NS_PER_MICROSECOND = 1000


def invalid(field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.INVALID_VALUE,
        ErrorCode.INVALID_VALUE.value,
        field_errors=(FieldError(field, ErrorCode.INVALID_VALUE.value, ErrorCode.INVALID_VALUE.value),),
    )


def refused() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


@dataclass(frozen=True, slots=True)
class GrantRequest:
    recipient: str
    mission_id: str
    origin_mode: OriginMode
    object_ref: str
    purpose: str
    actions: frozenset[str]
    fields: frozenset[str]
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class Issue:
    """When and in which process the grant was made: anchors the elapsed check of a finite grant."""

    at: datetime
    process_epoch: str
    monotonic_ns: int


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    revision: int
    granted_by: str
    subject_person_id: str
    request: GrantRequest
    policy_version: str
    issue: Issue
    schedule_generation: int | None
    state: GrantState
    ended_at: datetime | None

    def __post_init__(self) -> None:
        if (self.state == "active") != (self.ended_at is None) or self.revision < 1:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


@dataclass(frozen=True, slots=True)
class Reading:
    """The caller's frozen time: snapshot wall time plus the process clock for the elapsed bound."""

    at: datetime
    process_epoch: str
    monotonic_ns: int


def _names(values: Iterable[str], field: str, *, maximum: int) -> frozenset[str]:
    names = frozenset(values)
    if not 1 <= len(names) <= maximum or any(_NAME.fullmatch(value) is None for value in names):
        raise invalid(field)
    return names


def validate_request(
    session: SessionContext, value: GrantRequest, *, now: datetime, fields_max: int
) -> GrantRequest:
    """Only the authenticated subject grants; a grant to oneself, an empty set or a wildcard is refused.

    Validity starts no earlier than the grant itself: consent is prospective.
    """
    subject = owner(session)
    require_id(value.recipient)
    require_id(value.mission_id)
    require_id(value.object_ref)
    if value.recipient == subject:
        raise invalid("recipient")
    if _NAME.fullmatch(value.purpose) is None:
        raise invalid("purpose")
    if value.origin_mode not in ORIGIN_MODES:
        raise invalid("origin_mode")
    actions = _names(value.actions, "actions", maximum=fields_max)
    fields = _names(value.fields, "fields", maximum=fields_max)
    require_utc(value.valid_from)
    starts = max(value.valid_from, now)
    if value.valid_until is not None:
        require_utc(value.valid_until)
        if value.valid_until <= starts:
            raise invalid("valid_until")
    return replace(value, actions=actions, fields=fields, valid_from=starts)


def owner(session: SessionContext) -> str:
    """The authenticated crew person behind a session; operators, helpers, officers and stations are not."""
    if (
        session.kind != "crew"
        or session.person_id is None
        or session.actor.kind != "person"
        or session.actor.id != session.person_id
    ):
        raise refused()
    return session.person_id


def grants_now(grant: Grant, reading: Reading) -> bool:
    """Whether a stored grant authorizes at this reading; any unknown or terminal fact denies.

    A finite grant also needs the issuing process epoch and an elapsed monotonic time below its span,
    so neither a backward clock step nor a restart can extend it (R-E02-21 interim, E02 3.1).
    """
    value = grant.request
    if grant.state != "active" or grant.policy_version != POLICY_VERSION:
        return False
    if reading.at < value.valid_from:
        return False
    if value.valid_until is None:
        return True
    if reading.at >= value.valid_until or reading.process_epoch != grant.issue.process_epoch:
        return False
    elapsed = reading.monotonic_ns - grant.issue.monotonic_ns
    span = value.valid_until - grant.issue.at
    span_ns = (
        span.days * 86_400_000_000 + span.seconds * 1_000_000 + span.microseconds
    ) * _NS_PER_MICROSECOND
    return 0 <= elapsed < span_ns


def allowed_fields(
    grant: Grant, reading: Reading, *, action: str, requested: frozenset[str]
) -> frozenset[str]:
    """The fields this grant releases for one action: the intersection, never more than was asked."""
    if not requested or action not in grant.request.actions or not grants_now(grant, reading):
        return frozenset()
    return grant.request.fields & requested


def ended(grant: Grant, state: GrantState, at: datetime) -> Grant:
    """A terminal transition; a terminal grant never changes again and a renewal is a new grant ID."""
    require_utc(at)
    if grant.state != "active" or state == "active":
        raise ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
    return replace(grant, state=state, ended_at=at, revision=grant.revision + 1)


def matches(grant: Grant, *, subject_person_id: str, recipient: str, purpose: str, object_ref: str) -> bool:
    value = grant.request
    return (
        grant.subject_person_id == subject_person_id
        and value.recipient == recipient
        and value.purpose == purpose
        and value.object_ref == object_ref
    )
