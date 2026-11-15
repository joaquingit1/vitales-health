from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import cast

import jsonschema

from crew_health.model.application.ports import (
    CancelToken,
    Deadline,
    JsonValue,
    StepCancelled,
    StepOk,
    StepProgress,
    StepRequest,
    StepResult,
    StepTimedOut,
    StepUnavailable,
    ToolCall,
    Usage,
)
from crew_health.model.domain.release import ModelRefused, object_value, strict_json


def validate_script(raw: bytes, schema: dict[str, object], *, max_bytes: int) -> dict[str, object]:
    document = object_value(strict_json(raw, max_bytes=max_bytes))
    try:
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.ValidationError as error:
        raise ModelRefused("stub_invalid") from error
    return document


class StubResponder:
    def __init__(
        self, script: Mapping[str, object], *, profile: str, scenario: str, conversation_ordinal: int
    ) -> None:
        if profile not in ("demo", "eval"):
            raise ModelRefused("stub_in_flight")
        if script.get("scenario") != scenario:
            raise ModelRefused("stub_scenario_mismatch")
        values = script.get("entries")
        if not isinstance(values, list):
            raise ModelRefused("stub_invalid")
        self.entries = tuple(object_value(v) for v in values)
        self.ordinal = conversation_ordinal
        self.step = 0

    def run_step(
        self,
        request: StepRequest,
        *,
        deadline: Deadline,
        cancel: CancelToken,
        progress: Callable[[StepProgress], None],
    ) -> StepResult:
        if cancel.is_cancelled():
            return StepCancelled()
        if deadline.clock.monotonic_ns() >= deadline.total_ns:
            return StepTimedOut("total")
        rows = [
            x
            for x in self.entries
            if x.get("conversation_ordinal") == self.ordinal
            and x.get("phase") == request.phase
            and x.get("step_index") == self.step
        ]
        self.step += 1
        if len(rows) != 1:
            return StepUnavailable("stub_unmatched")
        row = rows[0]
        calls = row.get("tool_calls", [])
        if not isinstance(calls, list):
            raise ModelRefused("stub_invalid")
        tools = tuple(
            ToolCall(str(c["id"]), str(c["name"]), cast(Mapping[str, JsonValue], c["arguments"]))
            for c in (object_value(v) for v in calls)
        )
        final = row.get("final")
        if final is not None and not isinstance(final, dict):
            raise ModelRefused("stub_invalid")
        progress(StepProgress(True, 0, 0))
        # A script's thinking canary is intentionally ignored and cannot cross the result boundary.
        return StepOk(
            cast(Mapping[str, JsonValue] | None, final),
            tools,
            "tool_calls" if tools else "stop",
            Usage(0, 0, 0),
            request.bundle.prompt_sha256,
        )


def script_digest(script: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(script, sort_keys=True, allow_nan=False).encode()).hexdigest()
