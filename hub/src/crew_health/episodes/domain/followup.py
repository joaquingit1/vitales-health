from crew_health.episodes.domain.errors import refused
from crew_health.kernel.errors import ErrorCode


def officer(session_person: str | None, patient: str, roles: frozenset[str]) -> bool:
    return session_person is not None and session_person != patient and "crew_medical_officer" in roles


def task_action(
    action: str,
    *,
    kind: str,
    owner: str,
    creator: str,
    actor: str,
    patient: str,
    is_officer: bool,
    reason: str | None,
) -> None:
    permitted = False
    if action == "complete_followup":
        permitted = kind != "review" and (actor in (owner, patient) or is_officer)
    elif action == "cancel_followup":
        permitted = is_officer or (kind == "custom" and actor in (creator, owner))
        if not reason or not reason.strip():
            raise refused(ErrorCode.INVALID_VALUE, "cancellation_reason", "required")
    elif action == "reassign_followup":
        permitted = is_officer or (kind == "custom" and actor == owner)
    if not permitted:
        raise refused(ErrorCode.ACTION_NOT_ALLOWED)
