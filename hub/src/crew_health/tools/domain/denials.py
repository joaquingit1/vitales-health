from typing import Literal

type DenialReason = Literal[
    "unknown_tool",
    "out_of_stage",
    "malformed_schema",
    "scope",
    "tool_not_wired",
    "context_over_budget",
    "tool_timeout",
    "stale",
    "policy",
    "handler_rejected",
]
