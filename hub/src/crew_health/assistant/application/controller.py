"""Bounded speculative inference. Publication belongs to shared TaskHandler.apply."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from crew_health.assistant.application.content import Policy
from crew_health.assistant.application.json_data import JsonBounds, parse
from crew_health.assistant.application.tools_copies import (
    ContextBudget,
    ContextSnapshot,
    PendingEffect,
    ToolResult,
    ToolScope,
)
from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.model.application.ports import (
    CancelToken,
    Deadline,
    InferencePort,
    JsonValue,
    ModelReleaseReader,
    PromptPort,
    StepCancelled,
    StepMalformed,
    StepOk,
    StepProgress,
    StepRequest,
    StepTimedOut,
    StepUnavailable,
    ToolCall,
    TranscriptItem,
)
from crew_health.model.application.ports import (
    Phase as ModelPhase,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: Literal["text", "action", "assessment", "insight", "ready", "none"]
    payload: Mapping[str, JsonValue]
    effects: tuple[PendingEffect, ...] = ()


@dataclass(frozen=True, slots=True)
class Completed:
    candidate: Candidate
    scope: ToolScope
    context_checks: tuple[tuple[str, str], ...]
    calls: tuple[tuple[ToolCall, ToolResult], ...]
    steps_used: int


@dataclass(frozen=True, slots=True)
class Stopped:
    state: Literal["cancelled", "timed_out", "rejected", "failed", "stale"]
    code: ErrorCode
    reason: str


type Outcome = Completed | Stopped


class Boundary(Protocol):
    def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot: ...
    def fingerprint(self, snapshot: ContextSnapshot) -> str: ...
    def tool(self, scope: ToolScope, call: ToolCall) -> ToolResult: ...
    def terminal(
        self, scope: ToolScope, call: ToolCall, result: ToolResult, seen: frozenset[str]
    ) -> Candidate | None: ...
    def final(self, scope: ToolScope, output: Mapping[str, JsonValue], seen: frozenset[str]) -> Candidate: ...
    def incomplete(self, scope: ToolScope, seen: frozenset[str]) -> Candidate: ...


@dataclass(frozen=True, slots=True)
class Plan:
    scope: ToolScope
    policy: Policy
    budget: ContextBudget
    transcript: tuple[TranscriptItem, ...]
    json_bounds: JsonBounds


class Controller:
    def __init__(
        self,
        *,
        clock: Clock,
        releases: ModelReleaseReader,
        prompts: PromptPort,
        inference: InferencePort,
        boundary: Boundary,
    ) -> None:
        self.clock, self.releases, self.prompts, self.inference, self.boundary = (
            clock,
            releases,
            prompts,
            inference,
            boundary,
        )

    def run(self, plan: Plan, *, cancel: CancelToken, progress: Callable[[StepProgress], None]) -> Outcome:
        epoch, started = self.clock.process_epoch(), self.clock.monotonic_ns()
        total = started + plan.policy.turn_timeout_seconds * 1_000_000_000
        checks: dict[str, str] = {}
        seen: set[str] = set()
        calls: list[tuple[ToolCall, ToolResult]] = []
        transcript = list(plan.transcript)
        scope, repairs = plan.scope, 0
        stop = self._stop(cancel, epoch, started, total)
        if stop is not None:
            return stop
        try:
            settings = self.releases.controller_settings(scope.model_release_id).values
            max_iterations = min(plan.policy.max_iterations, _positive_limit(settings, "max_iterations"))
            max_tool_calls = min(plan.policy.max_iterations, _positive_limit(settings, "max_tool_calls"))
            catalog = self.releases.tool_catalog(scope.model_release_id)
        except ProblemError as error:
            return Stopped("failed", error.code, "model_configuration_invalid")
        known = {tool.get("name") for tool in catalog.tools}
        exposed = set(catalog.exposure.get(scope.phase.value, ()))
        for used in range(1, max_iterations + 1):
            stop = self._stop(cancel, epoch, started, total)
            if stop is not None:
                return stop
            try:
                context = self.boundary.context(scope, plan.budget)
                if not _pins(context, scope):
                    return Stopped("stale", ErrorCode.STALE_VERSION, "release_changed")
                scope = replace(scope, source_revision=context.source_revision)
                fingerprint = self.boundary.fingerprint(context)
                previous = checks.setdefault(scope.phase.value, fingerprint)
                if previous != fingerprint:
                    return Stopped("stale", ErrorCode.STALE_VERSION, "snapshot_changed")
                seen.update(context.included_ids)
                prompt_context = _prompt_context(context, plan.json_bounds)
                stop = self._stop(cancel, epoch, started, total)
                if stop is not None:
                    return stop
                bundle = self.prompts.build(
                    scope.model_release_id,
                    cast(ModelPhase, scope.phase.value),
                    prompt_context,
                    transcript,
                )
                stop = self._stop(cancel, epoch, started, total)
                if stop is not None:
                    return stop
                now = self.clock.monotonic_ns()
                deadline = Deadline(
                    total,
                    min(total, now + plan.policy.first_token_timeout_seconds * 1_000_000_000),
                    self.clock,
                )
                result = self.inference.run_step(
                    StepRequest(scope.model_release_id, cast(ModelPhase, scope.phase.value), bundle),
                    deadline=deadline,
                    cancel=cancel,
                    progress=progress,
                )
                stop = self._stop(cancel, epoch, started, total)
                if stop is not None:
                    return stop
                if isinstance(result, StepCancelled):
                    return Stopped("cancelled", ErrorCode.INVALID_TRANSITION, "cancelled")
                if isinstance(result, StepTimedOut):
                    return Stopped("timed_out", ErrorCode.MODEL_TIMEOUT, result.kind)
                if isinstance(result, StepUnavailable):
                    return Stopped("failed", ErrorCode.MODEL_UNAVAILABLE, "runtime_unavailable")
                malformed = (
                    isinstance(result, StepMalformed)
                    or isinstance(result, StepOk)
                    and result.finish_reason == "length"
                )
                if malformed:
                    if repairs >= plan.policy.max_schema_repairs:
                        return Stopped(
                            "rejected", ErrorCode.MALFORMED_MODEL_OUTPUT, "schema_repair_exhausted"
                        )
                    repairs += 1
                    transcript.append(
                        TranscriptItem(
                            "user", canonical_json({"schema_error_code": "malformed_output"}).decode("utf-8")
                        )
                    )
                    continue
                if not isinstance(result, StepOk) or bool(result.tool_calls) == (result.final is not None):
                    return Stopped("rejected", ErrorCode.MALFORMED_MODEL_OUTPUT, "invalid_step_form")
                candidate: Candidate | None = None
                if result.tool_calls:
                    if len(calls) + len(result.tool_calls) > max_tool_calls:
                        return self._completed(
                            self.boundary.incomplete(scope, frozenset(seen)), scope, checks, calls, used
                        )
                    transcript.append(TranscriptItem("assistant", "", tool_calls=result.tool_calls))
                    for call in result.tool_calls:
                        if (
                            call.name not in known
                            or call.name not in exposed
                            or any(old.id == call.id for old, _ in calls)
                        ):
                            return Stopped("rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "tool_not_allowed")
                        tool_result = self.boundary.tool(scope, call)
                        if tool_result.tool_call_id != call.id or tool_result.status not in _TOOL_STATUSES:
                            return Stopped("rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "tool_result_invalid")
                        if tool_result.status in {"denied", "error"}:
                            return Stopped("rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "tool_denied")
                        calls.append((call, tool_result))
                        seen.update(tool_result.provenance_ids)
                        transcript.append(
                            TranscriptItem("tool", _tool_payload(tool_result, plan.json_bounds), call.id)
                        )
                        terminal = self.boundary.terminal(scope, call, tool_result, frozenset(seen))
                        if terminal is not None:
                            if candidate is not None:
                                return Stopped(
                                    "rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "multiple_actions"
                                )
                            candidate = terminal
                else:
                    assert result.final is not None
                    candidate = self.boundary.final(scope, result.final, frozenset(seen))
                if candidate is not None:
                    fresh = self.boundary.context(scope, plan.budget)
                    if not _pins(fresh, scope) or self.boundary.fingerprint(fresh) != fingerprint:
                        return Stopped("stale", ErrorCode.STALE_VERSION, "snapshot_changed")
                    return self._completed(candidate, scope, checks, calls, used)
            except AssistantRefused:
                return Stopped("rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "validation_rejected")
            except ProblemError as error:
                if error.code in (ErrorCode.MODEL_OUTPUT_REJECTED, ErrorCode.MALFORMED_MODEL_OUTPUT):
                    return Stopped("rejected", error.code, "validation_rejected")
                if error.code == ErrorCode.STALE_VERSION:
                    return Stopped("stale", error.code, "snapshot_changed")
                return Stopped("failed", error.code, "dependency_failed")
        return self._completed(
            self.boundary.incomplete(scope, frozenset(seen)), scope, checks, calls, max_iterations
        )

    def _stop(self, cancel: CancelToken, epoch: str, started: int, total: int) -> Stopped | None:
        if cancel.is_cancelled():
            return Stopped("cancelled", ErrorCode.INVALID_TRANSITION, "cancelled")
        now = self.clock.monotonic_ns()
        if self.clock.process_epoch() != epoch or now < started or now >= total:
            return Stopped("timed_out", ErrorCode.MODEL_TIMEOUT, "total")
        return None

    @staticmethod
    def _completed(
        candidate: Candidate,
        scope: ToolScope,
        checks: dict[str, str],
        calls: list[tuple[ToolCall, ToolResult]],
        used: int,
    ) -> Completed:
        return Completed(candidate, scope, tuple(checks.items()), tuple(calls), used)


_TOOL_STATUSES = frozenset(
    {"ok", "no_result", "unavailable", "declined", "stale", "conflicting", "not_aboard", "denied", "error"}
)


@dataclass(frozen=True, slots=True)
class _PromptContext:
    sections: Mapping[str, JsonValue]


def _pins(context: ContextSnapshot, scope: ToolScope) -> bool:
    return (context.config_release_id, context.evidence_release_id, context.model_release_id) == (
        scope.config_release_id,
        scope.evidence_release_id,
        scope.model_release_id,
    )


def _prompt_context(context: ContextSnapshot, bounds: JsonBounds) -> _PromptContext:
    sections = parse(context.sections, bounds)
    if not isinstance(sections, list):
        raise AssistantRefused("invalid_context")
    return _PromptContext({"authorized_sections": sections})


def _tool_payload(result: ToolResult, bounds: JsonBounds) -> str:
    data = None if result.data is None else parse(result.data, bounds)
    if (
        result.status in {"unavailable", "denied", "error", "no_result", "declined", "stale"}
        and data is not None
    ):
        raise AssistantRefused("invalid_tool_result")
    return canonical_json(
        {
            "tool_call_id": result.tool_call_id,
            "status": result.status,
            "data": data,
            "provenance_ids": result.provenance_ids,
            "limitations": result.limitations,
            "error_code": result.error_code,
        }
    ).decode("utf-8")


def _positive_limit(values: Mapping[str, JsonValue], key: str) -> int:
    value = values.get(key)
    if type(value) is not int or value < 1:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "assistant model limits invalid")
    return value
