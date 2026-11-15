from crew_health.episodes.domain.errors import refused
from crew_health.episodes.domain.types import (
    ACTIVE,
    Episode,
    ResolutionCommand,
    ResolutionFacts,
    ResolutionReason,
)
from crew_health.kernel.errors import ErrorCode


def check_resolution(ep: Episode, cmd: ResolutionCommand, facts: ResolutionFacts) -> None:
    if ep.state not in ACTIVE:
        raise refused(ErrorCode.INVALID_TRANSITION)
    for field, blocked in (
        ("urgency", facts.blocks_resolution),
        ("followup_tasks", facts.open_tasks),
        ("plan_run", facts.open_plan_runs),
        ("measurement_request", facts.pending_requests),
    ):
        if blocked:
            raise refused(ErrorCode.INVALID_TRANSITION, field, "unresolved" if field == "urgency" else "open")
    patient = cmd.authority == "patient" and cmd.actor_id == ep.patient_id
    officer = cmd.authority == "crew_medical_officer" and cmd.actor_id != ep.patient_id
    if not (patient or officer):
        raise refused(ErrorCode.ACTION_NOT_ALLOWED)
    if cmd.reason == ResolutionReason.SYMPTOMS_REPORTED_RESOLVED:
        if not patient:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        if cmd.observation_ids or cmd.review_id is not None:
            raise refused(ErrorCode.INVALID_VALUE, "evidence")
        return
    if cmd.rationale is None or not cmd.rationale.strip():
        raise refused(ErrorCode.INVALID_VALUE, "rationale", "required")
    if cmd.reason == ResolutionReason.CLINICIAN_ASSESSED:
        if not officer:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        if (
            cmd.review_id is None
            or cmd.review_id != facts.review_id
            or facts.review_by != cmd.actor_id
            or facts.review_role != "crew_medical_officer"
            or facts.review_at is None
            or (facts.latest_correction_at is not None and facts.review_at < facts.latest_correction_at)
        ):
            raise refused(ErrorCode.INVALID_VALUE, "review_id", "onboard_review_required")
        return
    if facts.present_answer:
        raise refused(ErrorCode.INVALID_VALUE, "reason", "symptoms_present")
    if not cmd.observation_ids or len(set(cmd.observation_ids)) != len(cmd.observation_ids):
        raise refused(ErrorCode.INVALID_VALUE, "observation_ids", "required")
    indexed = {o.observation_id: o for o in facts.observations}
    for ref in cmd.observation_ids:
        obs = indexed.get(ref)
        if (
            obs is None
            or obs.patient_id != ep.patient_id
            or obs.episode_id != ep.episode_id
            or obs.quality_status != "accepted"
            or obs.observed_at <= ep.created_at
        ):
            raise refused(ErrorCode.INVALID_VALUE, "observation_ids", "invalid_evidence")
