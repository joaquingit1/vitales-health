"""Object rules of a guided check (E30 section 10, narrowed by SYSTEM 8.3 and D9). Client supplied IDs are never
authorization. Officer or station status alone is no chart grant: a person reads and acts on a check only as its
helper, its subject or the person whose record received the count; a station only on checks its own session
created for an open plan run. Missing facts deny (fail closed)."""

from __future__ import annotations

from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.teach.domain.guided_check import GuidedCheck

type Operation = Literal["read", "beat", "act", "cancel"]  # internal enum
TEACH_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.STATION})


def forbidden() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def require_actor(session: SessionContext) -> None:
    """The assistant and adapters never create, act on, finish or cancel a check (H12, AGENTS rule 4)."""
    if session.actor.kind in ("assistant", "adapter", "engine"):
        raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, ErrorCode.ACTION_NOT_ALLOWED.value)
    if not session.roles & TEACH_ROLES or session.kind not in ("crew", "station"):
        raise forbidden()


def is_station(session: SessionContext) -> bool:
    return session.kind == "station"


def parties(check: GuidedCheck) -> frozenset[str]:
    return frozenset({check.helper_id, check.subject_id, check.measured_record_id})


def allows(session: SessionContext, check: GuidedCheck, operation: Operation) -> bool:
    if session.actor.kind in ("assistant", "adapter", "engine"):
        return False
    if is_station(session):
        return check.created_by_session == session.session_id and check.plan_run_id is not None
    person = session.person_id
    if person is None:
        return False
    if operation == "read":
        return person in parties(check)
    if operation == "cancel":
        return person in (check.helper_id, check.subject_id)
    return person == check.helper_id


def authorize(session: SessionContext, check: GuidedCheck, operation: Operation) -> None:
    if operation != "read":
        require_actor(session)
    if not allows(session, check, operation):
        raise forbidden()
