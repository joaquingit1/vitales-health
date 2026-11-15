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
    """PLAN row 30, AGENTS 6 privacy: per object. A person reads an override that names them (its subject, or a
    member of a group override). A crew medical officer needs a current grant from Visibility for this override; a
    ground session needs that grant and to have started it. The design's role reads (E25 3.14: every officer and
    ground session for a group override, ground for any override it started) are narrowed to those grants: role
    status and having started the override grant nothing on their own."""
    if session.person_id is not None and (
        session.person_id == override.person_id or session.person_id in override.member_ids
    ):
        return True
    if Role.GROUND in session.roles:
        started = override.source_kind == "person" and override.source_ref == session.actor.id
        return started and visibility is not None and visibility.can_read(session, override.override_id)
    return care_granted(session, override.override_id, visibility)


def care_granted(session: SessionContext, ref: str, visibility: Visibility | None) -> bool:
    """A care read: the crew medical officer with a current object grant from Visibility (E02). Fails closed
    without Visibility. The grant is per object only, so a care read never carries free text (`project_notes`)."""
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
