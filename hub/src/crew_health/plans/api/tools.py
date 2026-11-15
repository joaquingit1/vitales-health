"""Optional assistant gateway registration, with no plan command capabilities."""

from crew_health.plans.application.assistant_copies import ToolRegistry
from crew_health.plans.application.tools import PlanTools


def register_plan_tools(registry: ToolRegistry, handlers: PlanTools) -> None:
    for handler in (handlers.get_plan, handlers.choose_plan_step, handlers.explain_step):
        registry.register(handler.name, handler)
