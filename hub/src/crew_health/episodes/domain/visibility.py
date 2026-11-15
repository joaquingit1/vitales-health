from crew_health.kernel.uow import SessionContext


def visibility(session: SessionContext, patient_id: str, *, ground_consent: bool = False) -> str:
    if session.actor.kind == "assistant":
        return "none"
    if session.kind == "crew" and session.actor.kind == "person" and session.person_id == session.actor.id:
        if session.person_id == patient_id:
            return "full"
        if "crew_medical_officer" in session.roles:
            return "filtered"
    if (
        session.kind == "ground"
        and session.actor.kind == "ground"
        and "ground" in session.roles
        and ground_consent
    ):
        return "filtered"
    return "none"
