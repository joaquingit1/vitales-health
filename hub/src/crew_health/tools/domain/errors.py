from __future__ import annotations

from typing import Literal

from crew_health.kernel.errors import ErrorCode, ProblemError

type ToolsErrorCode = Literal[
    "MALFORMED_MODEL_OUTPUT",
    "MODEL_OUTPUT_REJECTED",
    "CONFIG_INVALID",
    "CAPABILITY_UNAVAILABLE",
    "PAYLOAD_TOO_LARGE",
]


class ToolsInvalid(ProblemError):
    def __init__(self, code: ToolsErrorCode) -> None:
        super().__init__(ErrorCode(code), code)
