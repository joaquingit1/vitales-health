import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.assistant.application.http_ports import AssistantHttp
from crew_health.assistant.application.ports import AssistantStatusPort, SelectedAssessmentFacts
from crew_health.assistant.module import register
from crew_health.episodes.application.ports import AssessmentCommands, AssessmentFacts


@pytest.mark.trace("T-E13-075", "SYSTEM.5.12", "SYSTEM.8.1")
def test_real_feature_registers_canonical_class_keys_without_starting_model() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    register(registry)
    assert {
        AssistantHttp,
        AssistantStatusPort,
        SelectedAssessmentFacts,
        AssessmentFacts,
        AssessmentCommands,
    } <= registry.providers.keys()
    assert registry.declarations["startup"]
