"""One policy for reads, command replays and event audiences (SYSTEM 8.3)."""

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.ports import Visibility
from crew_health.routine.domain.override import Override


def override_visible(
    override: Override, session: SessionContext, visibility: Visibility | None = None
) -> bool:
    if override.audience != "person":
        return session.person_id in override.member_ids or bool(
            session.roles & {Role.CREW_MEDICAL_OFFICER, Role.GROUND}
        )
    if session.person_id is not None and session.person_id == override.person_id:
        return True
    if override.source_kind == "person" and override.source_ref == session.actor.id:
        return True
    return care_granted(session, override.override_id, visibility)


def care_granted(session: SessionContext, ref: str, visibility: Visibility | None) -> bool:
    return (
        Role.GROUND not in session.roles
        and Role.CREW_MEDICAL_OFFICER in session.roles
        and visibility is not None
        and visibility.can_read(session, ref)
    )


def audit_care(deps: RoutineDeps, session: SessionContext, uow: UnitOfWork | None = None) -> None:
    if uow is not None:
        audit(deps, uow, "care_read")
    else:
        with deps.uow.begin(actor=session.actor, correlation_id=deps.ids.new("cor")) as write:
            audit(deps, write, "care_read")


def read_override(
    deps: RoutineDeps, override: Override, session: SessionContext, uow: UnitOfWork | None = None
) -> bool:
    if not override_visible(override, session, deps.visibility):
        return False
    if not override_visible(override, session):
        audit_care(deps, session, uow)
    return True
