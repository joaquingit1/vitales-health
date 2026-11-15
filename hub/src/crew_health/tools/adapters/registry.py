from __future__ import annotations

from crew_health.kernel.settings import HubSettings
from crew_health.tools.application.ports import ToolHandler
from crew_health.tools.domain.errors import ToolsInvalid


def tool_names() -> frozenset[str]:
    """Capability ceiling from LLM-ARCHITECTURE 4; configuration can only narrow it."""
    return frozenset(
        {
            "get_episode_context",
            "get_observations",
            "get_medication_allergy_context",
            "get_capabilities",
            "get_pathway",
            "search_evidence",
            "read_source_passage",
            "compare_observations",
            "calculate_validated_metric",
            "get_environment_context",
            "get_report_findings",
            "propose_question",
            "propose_measurement",
            "propose_review",
            "draft_assessment",
            "draft_handoff",
            "record_unresolved_gap",
            "get_forecast",
            "get_plan",
            "choose_plan_step",
            "explain_step",
            "propose_guided_check",
            "get_crew_readiness",
        }
    )


class StaticToolRegistry:
    def __init__(self, settings: HubSettings) -> None:
        self._maximum = settings.integer("tools.max_handlers")
        if not 1 <= self._maximum <= 64:
            raise ToolsInvalid("CONFIG_INVALID")
        self._handlers: dict[str, ToolHandler] = {}
        self._frozen = False

    def register(self, name: str, handler: ToolHandler) -> None:
        if (
            self._frozen
            or name not in tool_names()
            or name in self._handlers
            or len(self._handlers) >= self._maximum
            or handler.name != name
            or handler.effect not in {"read", "compute", "proposal"}
        ):
            raise ToolsInvalid("CONFIG_INVALID")
        self._handlers[name] = handler

    def freeze(self) -> None:
        self._frozen = True

    def handler(self, name: str) -> ToolHandler | None:
        if not self._frozen:
            raise ToolsInvalid("CONFIG_INVALID")
        return self._handlers.get(name)
