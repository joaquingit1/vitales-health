import pytest

from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.forms import Proposal, validate_form, validate_proposal


@pytest.mark.trace("T-E13-014", "T-E13-015", "INV-E13-13")
@pytest.mark.parametrize("kind", ["text", "retrieve_evidence", "raise_warning", "dose", "unknown"])
def test_episode_never_persists_free_text_retrieval_or_unknown_action(kind: str) -> None:
    with pytest.raises(AssistantRefused, match="form_not_allowed"):
        validate_form("episode", "intake", kind)


@pytest.mark.trace("T-E13-014", "T-E13-015", "T-E13-012")
@pytest.mark.parametrize(
    "context,phase,kind",
    [
        ("episode", "intake", "ask"),
        ("episode", "intake", "request_measurement"),
        ("episode", "intake", "ready"),
        ("episode", "disposition", "request_review"),
        ("episode", "assessment", "assessment"),
        ("help_session", "help", "request_measurement"),
        ("help_session", "help", "text"),
        ("general", "general", "text"),
        ("episode", "intake", "stop_incomplete"),
    ],
)
def test_owned_terminal_forms_allow_the_complete_context_phase_set(
    context: str, phase: str, kind: str
) -> None:
    validate_form(context, phase, kind)


@pytest.mark.trace("T-E13-012", "T-E13-015")
@pytest.mark.parametrize(
    "context,phase,kind",
    [
        ("help_session", "help", "ask"),
        ("general", "general", "ready"),
        ("episode", "intake", "request_review"),
        ("episode", "assessment", "text"),
        ("general", "general", "request_measurement"),
    ],
)
def test_terminal_proposal_cannot_cross_context_or_phase(context: str, phase: str, kind: str) -> None:
    with pytest.raises(AssistantRefused, match="form_not_allowed"):
        validate_form(context, phase, kind)


def validate(proposal: Proposal) -> Proposal:
    return validate_proposal(
        proposal,
        body_regions=frozenset({"heart"}),
        capabilities=frozenset({"pulse_rate"}),
        condition_ids=frozenset({"ICL12"}),
    )


@pytest.mark.trace("T-E13-019", "R28")
def test_model_cannot_silently_contact_ground() -> None:
    assert (
        validate(Proposal("request_review", "review reason", also_tell_ground=True)).also_tell_ground is False
    )


@pytest.mark.trace("D06", "T-E13-094", "VR9")
@pytest.mark.parametrize(
    "proposal",
    [
        Proposal("request_measurement", "reason"),
        Proposal("request_measurement", "", "decision", "pulse_rate"),
        Proposal("request_measurement", "reason", "", "pulse_rate"),
        Proposal("request_measurement", "reason", "decision", "unknown"),
        Proposal("request_measurement", "reason", "decision", "pulse_rate", "unknown"),
        Proposal("request_measurement", "reason", "decision", "pulse_rate", "heart", ("unknown",)),
    ],
)
def test_measurement_requires_resolved_identity_and_both_explanations(proposal: Proposal) -> None:
    with pytest.raises(AssistantRefused):
        validate(proposal)


@pytest.mark.trace("D06", "T-E13-094")
def test_valid_measurement_keeps_requested_capability_and_condition() -> None:
    proposal = Proposal("request_measurement", "reason", "decision", "pulse_rate", "heart", ("ICL12",))
    assert validate(proposal) == proposal
