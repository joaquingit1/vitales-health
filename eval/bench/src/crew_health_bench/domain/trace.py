from dataclasses import dataclass


@dataclass(frozen=True)
class CaseTrace:
    warnings: tuple[tuple[str, str, str], ...] | None = None
    help_sessions: tuple[str, ...] | None = None
    requests_between: tuple[int, ...] | None = None
    requests: tuple[tuple[str, str, str, bool], ...] | None = None
    capability_gaps: tuple[str, ...] | None = None
    capability_codes: tuple[str, ...] | None = None
    observation_ids: tuple[str, ...] | None = None
    provenance_ids: tuple[str, ...] | None = None
    references: tuple[tuple[str, str], ...] | None = None
    accepted_observations: tuple[str, ...] | None = None
    answers: tuple[str, ...] | None = None
    approved_passages: tuple[tuple[str, str], ...] | None = None
    evidence_release_id: str = ""
    texts: tuple[str, ...] | None = None
    final_action: str | None = None
    missing_evidence: tuple[str, ...] | None = None
    hypotheses: tuple[str, ...] | None = None
    accepted: tuple[tuple[str, str, str], ...] | None = None
    active_step_ids: tuple[str, ...] | None = None
    rejected_call_ids: tuple[str, ...] | None = None
    logged_rejections: tuple[str, ...] | None = None
    steps_and_actions: tuple[tuple[str, ...], ...] | None = None
    tool_calls: tuple[str, ...] | None = None
    denied_call_ids: tuple[str, ...] | None = None
    side_effects: tuple[tuple[str, str], ...] | None = None
    retrieved: tuple[str, ...] | None = None
    passages: tuple[tuple[str, str], ...] | None = None
