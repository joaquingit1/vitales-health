from __future__ import annotations

from crew_health.tools.domain.errors import ToolsInvalid

REQUIRED_RULES = frozenset(
    {"dose", "level_above_watch", "replay_as_exposure", "invented_threshold", "help_flow"}
)


def require_policy_rules(rule_ids: tuple[str, ...]) -> None:
    if len(rule_ids) != len(REQUIRED_RULES) or frozenset(rule_ids) != REQUIRED_RULES:
        raise ToolsInvalid("CONFIG_INVALID")


def require_warning_authority(*, level: str, raised_by: str, action: str | None) -> None:
    if level not in {"info", "watch"} or raised_by != "assistant" or action is not None:
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
