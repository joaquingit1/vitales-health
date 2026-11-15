from dataclasses import dataclass, replace

from crew_health.assistant.domain.errors import AssistantRefused


@dataclass(frozen=True, slots=True)
class Proposal:
    kind: str
    reason: str
    decision_affected: str | None = None
    capability_code: str | None = None
    body_region: str | None = None
    condition_ids: tuple[str, ...] = ()
    also_tell_ground: bool = False


def validate_form(context: str, phase: str, kind: str) -> None:
    allowed = {
        ("episode", "intake"): {"ask", "request_measurement", "ready", "stop_incomplete"},
        ("episode", "assessment"): {"assessment", "stop_incomplete"},
        ("episode", "disposition"): {"request_review", "stop_incomplete"},
        ("help_session", "help"): {"text", "request_measurement", "stop_incomplete"},
        **{
            (name, "general"): {"text", "stop_incomplete"}
            for name in ("general", "reading", "warning", "insight")
        },
    }
    if kind not in allowed.get((context, phase), set()):
        raise AssistantRefused("form_not_allowed")


def validate_proposal(
    proposal: Proposal,
    *,
    body_regions: frozenset[str],
    capabilities: frozenset[str],
    condition_ids: frozenset[str],
) -> Proposal:
    if not proposal.reason.strip():
        raise AssistantRefused("reason_required")
    if proposal.body_region is not None and proposal.body_region not in body_regions:
        raise AssistantRefused("invalid_body_region")
    if not set(proposal.condition_ids) <= condition_ids:
        raise AssistantRefused("invalid_condition")
    if proposal.kind == "request_measurement" and (
        proposal.capability_code not in capabilities or not (proposal.decision_affected or "").strip()
    ):
        raise AssistantRefused("invalid_measurement_proposal")
    return replace(proposal, also_tell_ground=False)
