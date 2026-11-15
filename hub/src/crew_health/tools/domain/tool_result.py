from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id
from crew_health.tools.domain.errors import ToolsErrorCode, ToolsInvalid

type ToolStatus = Literal[
    "ok", "no_result", "unavailable", "declined", "stale", "conflicting", "not_aboard", "denied", "error"
]


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_call_id: str
    name: str
    arguments: bytes

    def __post_init__(self) -> None:
        require_id(self.tool_call_id)


@dataclass(frozen=True, slots=True)
class PendingEffect:
    turn_id: str
    tool_call_id: str
    generation: int
    source_revision: int
    arguments: bytes
    references: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.turn_id, self.tool_call_id, *self.references):
            require_id(value)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    status: ToolStatus
    data: bytes | None
    provenance_ids: tuple[str, ...]
    observed_at: datetime | None
    generated_at: datetime
    limitations: tuple[str, ...]
    error_code: ToolsErrorCode | None

    def __post_init__(self) -> None:
        require_id(self.tool_call_id)
        require_utc(self.generated_at)
        if self.observed_at is not None:
            require_utc(self.observed_at)
        for ref in self.provenance_ids:
            require_id(ref)
        if self.status not in {
            "ok",
            "no_result",
            "unavailable",
            "declined",
            "stale",
            "conflicting",
            "not_aboard",
            "denied",
            "error",
        } or (self.status in {"denied", "error", "unavailable"} and self.data is not None):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
