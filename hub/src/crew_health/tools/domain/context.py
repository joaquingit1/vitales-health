from __future__ import annotations

from dataclasses import dataclass

from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.support import SupportItem


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int
    output_reserve: int
    section_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.max_tokens) is not int
            or self.max_tokens < 1
            or type(self.output_reserve) is not int
            or self.output_reserve < 0
            or len(set(self.section_order)) != len(self.section_order)
            or not self.section_order
            or any(not name for name in self.section_order)
        ):
            raise ToolsInvalid("CONFIG_INVALID")


@dataclass(frozen=True, slots=True)
class ContextSection:
    name: str
    items: tuple[SupportItem, ...]
    required: bool


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    sections: bytes
    included_ids: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    config_release_id: str
    evidence_release_id: str
    model_release_id: str
    source_revision: int
    snapshot_fingerprint: str


def require_context_budget(
    *, prompt: bytes, prompt_tokens: int | None, output_reserve: int, max_bytes: int, max_tokens: int
) -> None:
    if type(max_bytes) is not int or type(max_tokens) is not int or min(max_bytes, max_tokens) < 1:
        raise ToolsInvalid("CONFIG_INVALID")
    if prompt_tokens is None:
        raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
    if (
        type(prompt_tokens) is not int
        or type(output_reserve) is not int
        or min(prompt_tokens, output_reserve) < 0
    ):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    if len(prompt) > max_bytes or prompt_tokens + output_reserve > max_tokens:
        raise ToolsInvalid("PAYLOAD_TOO_LARGE")
