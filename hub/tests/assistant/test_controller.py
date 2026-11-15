from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from crew_health.assistant.application.content import Policy
from crew_health.assistant.application.controller import Candidate, Completed, Controller, Plan, Stopped
from crew_health.assistant.application.json_data import JsonBounds
from crew_health.assistant.application.tools_copies import (
    ContextBudget,
    ContextSnapshot,
    Phase,
    ToolResult,
    ToolScope,
)
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.model.application.ports import (
    CancelToken,
    ControllerSettings,
    Deadline,
    DecodingSettings,
    JsonValue,
    ModelReleaseReader,
    PromptBundle,
    PromptPort,
    StepMalformed,
    StepOk,
    StepProgress,
    StepRequest,
    StepResult,
    StepTimedOut,
    ToolCall,
    ToolCatalog,
    TranscriptItem,
    Usage,
)
from crew_health.model.application.ports import ContextSnapshot as PromptContext

NOW = datetime(2026, 11, 15, tzinfo=UTC)


class Cancel:
    value = False

    def is_cancelled(self) -> bool:
        return self.value


class Releases:
    def __init__(self, iterations: int = 3, tool_calls: int = 3) -> None:
        self.settings = ControllerSettings({"max_iterations": iterations, "max_tool_calls": tool_calls})

    def controller_settings(self, release_id: str) -> ControllerSettings:
        assert release_id == "model_A"
        return self.settings

    def tool_catalog(self, release_id: str) -> ToolCatalog:
        assert release_id == "model_A"
        return ToolCatalog(
            (
                {"name": "get_observations", "effect": "read"},
                {"name": "propose_question", "effect": "proposal"},
            ),
            {"general": ("get_observations",), "intake": ("propose_question",)},
        )


class Prompts:
    seen: list[tuple[str, Sequence[TranscriptItem]]]

    def __init__(self) -> None:
        self.seen = []

    def build(
        self, release_id: str, phase: str, context: PromptContext, transcript: Sequence[TranscriptItem]
    ) -> PromptBundle:
        assert isinstance(context.sections, Mapping)
        self.seen.append((phase, tuple(transcript)))
        return PromptBundle(
            tuple(transcript), None, "schema", DecodingSettings(0, 1, 1, 0, 0, 64, "none"), "0" * 64
        )


class Inference:
    def __init__(self, values: tuple[StepResult, ...]) -> None:
        self.values = iter(values)
        self.calls = 0

    def run_step(
        self,
        request: StepRequest,
        *,
        deadline: Deadline,
        cancel: CancelToken,
        progress: Callable[[StepProgress], None],
    ) -> StepResult:
        self.calls += 1
        return next(self.values)


class Bound:
    def __init__(self) -> None:
        self.checks = 0
        self.changed = False
        self.status = "ok"
        self.calls: list[str] = []

    def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot:
        self.checks += 1
        return ContextSnapshot(
            b"[]",
            ("ob_seen",),
            (),
            scope.config_release_id,
            scope.evidence_release_id,
            scope.model_release_id,
            scope.source_revision,
            "changed" if self.changed and self.checks > 1 else "same",
        )

    def fingerprint(self, snapshot: ContextSnapshot) -> str:
        return snapshot.snapshot_fingerprint

    def tool(self, scope: ToolScope, call: ToolCall) -> ToolResult:
        self.calls.append(call.name)
        return ToolResult(call.id, cast("object", self.status), b"{}", (), None, NOW, (), None)  # type: ignore[arg-type]

    def terminal(
        self, scope: ToolScope, call: ToolCall, result: ToolResult, seen: frozenset[str]
    ) -> Candidate | None:
        return None

    def final(self, scope: ToolScope, output: Mapping[str, JsonValue], seen: frozenset[str]) -> Candidate:
        return Candidate("text", output)

    def incomplete(self, scope: ToolScope, seen: frozenset[str]) -> Candidate:
        return Candidate("action", {"reason": "budget_exhausted"})


def plan(*, iterations: int = 3) -> Plan:
    actor = Actor("person", "person_own")
    session = SessionContext("session_own", "crew", actor, "person_own", frozenset())
    anchor = TimeInput(NOW, "clock_hub", "segment_hub", 1, "hub_corrected", "SIM")
    scope = ToolScope(
        session,
        "person_own",
        "mission_sim",
        "SIM",
        None,
        None,
        None,
        "turn_own",
        "conversation_own",
        "config_A",
        "evidence_A",
        "model_A",
        Phase.GENERAL,
        1,
        1,
        ElapsedDeadline(anchor, timedelta(seconds=90)),
    )
    return Plan(
        scope,
        Policy("not clinically reviewed", frozenset({"info", "watch"}), iterations, 1, 90, 20, 10, ()),
        ContextBudget(32768, 64, ("facts",)),
        (),
        json_bounds=JsonBounds(262144, 16, 10000, 200, 4000),
    )


def run(
    values: tuple[StepResult, ...],
    *,
    bound: Bound | None = None,
    cancellation: Cancel | None = None,
    iterations: int = 3,
    releases: Releases | None = None,
) -> tuple[Completed | Stopped, Inference, Prompts, Bound]:
    inference = Inference(values)
    prompts = Prompts()
    boundary = bound or Bound()
    controller = Controller(
        clock=MemoryClock(NOW, 0, "epoch_test"),
        releases=cast(ModelReleaseReader, releases or Releases()),
        prompts=cast(PromptPort, prompts),
        inference=inference,
        boundary=boundary,
    )
    result = controller.run(
        plan(iterations=iterations), cancel=cancellation or Cancel(), progress=lambda p: None
    )
    return result, inference, prompts, boundary


def ok(*, call: ToolCall | None = None) -> StepOk:
    return StepOk(
        {"kind": "text", "text": "verified fixture"} if call is None else None,
        () if call is None else (call,),
        "stop",
        Usage(1, 1, 7),
        "0" * 64,
    )


@pytest.mark.trace("T-E13-016", "V27")
def test_only_one_repair_and_repair_receives_safe_code_not_raw_content() -> None:
    result, inference, prompts, _ = run(
        (StepMalformed("schema", "private_hash"), StepMalformed("schema", "private_hash"))
    )
    assert isinstance(result, Stopped) and result.code == ErrorCode.MALFORMED_MODEL_OUTPUT
    assert inference.calls == 2 and len(prompts.seen) == 2
    assert "private_hash" not in str(prompts.seen)
    success, _, _, _ = run((StepMalformed("schema", "private_hash"), ok()))
    assert isinstance(success, Completed)


@pytest.mark.trace("T-E13-011", "R17")
def test_unknown_and_out_of_stage_tools_never_reach_gateway() -> None:
    for name in ("unknown", "propose_question"):
        result, _, _, bound = run((ok(call=ToolCall("call_1", name, {})),))
        assert isinstance(result, Stopped) and result.code == ErrorCode.MODEL_OUTPUT_REJECTED
        assert bound.calls == []


@pytest.mark.trace("T-E13-021", "V27")
def test_repeated_reads_cannot_exceed_turn_step_budget() -> None:
    read = ok(call=ToolCall("call_1", "get_observations", {}))
    result, inference, _, _ = run(
        tuple(replace(read, tool_calls=(ToolCall(f"call_{i}", "get_observations", {}),)) for i in range(3)),
        iterations=3,
    )
    assert isinstance(result, Completed) and result.candidate.payload["reason"] == "budget_exhausted"
    assert result.steps_used == 3 and inference.calls == 3


@pytest.mark.trace("T-E13-030", "T-E13-031", "V30")
def test_material_change_before_second_step_stops_without_new_inference() -> None:
    bound = Bound()
    bound.changed = True
    result, inference, _, _ = run((ok(call=ToolCall("call_1", "get_observations", {})),), bound=bound)
    assert isinstance(result, Stopped) and result.state == "stale"
    assert inference.calls == 1


@pytest.mark.trace("T-E13-024", "V27")
def test_precancel_never_builds_context_or_invokes_model() -> None:
    cancel = Cancel()
    cancel.value = True
    result, inference, _, bound = run((), cancellation=cancel)
    assert isinstance(result, Stopped) and result.state == "cancelled"
    assert inference.calls == 0 and bound.checks == 0


@pytest.mark.trace("T-E13-023", "V27")
def test_model_timeout_remains_distinct_from_unavailable() -> None:
    result, _, _, _ = run((StepTimedOut("first_token"),))
    assert (
        isinstance(result, Stopped) and result.state == "timed_out" and result.code == ErrorCode.MODEL_TIMEOUT
    )


@pytest.mark.trace("T-E13-050", "T-E13-051", "INV-E13-05")
def test_success_is_speculative_and_never_exposes_thinking_or_commits_effects() -> None:
    result, _, _, _ = run((ok(),))
    assert isinstance(result, Completed) and result.candidate.kind == "text"
    assert result.context_checks == (("general", "same"),)
    assert "thinking" not in str(result.candidate.payload)


@pytest.mark.trace("T-E13-011", "INV-E13-11", "R17")
@pytest.mark.parametrize("raw", [b'[{"x":1,"x":2}]', b'[{"x":NaN}]', b'[{"x":1e999}]'])
def test_invalid_context_is_rejected_before_any_inference(raw: bytes) -> None:
    class InvalidContext(Bound):
        def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot:
            return replace(super().context(scope, budget), sections=raw)

    result, inference, _, _ = run((ok(),), bound=InvalidContext())
    assert isinstance(result, Stopped) and result.code == ErrorCode.MODEL_OUTPUT_REJECTED
    assert inference.calls == 0


@pytest.mark.trace("T-E13-011", "R17")
def test_malformed_tool_success_cannot_escape_as_an_exception_or_continue() -> None:
    class InvalidTool(Bound):
        def tool(self, scope: ToolScope, call: ToolCall) -> ToolResult:
            return replace(super().tool(scope, call), data=b"{bad")

    result, inference, _, _ = run(
        (ok(call=ToolCall("call_one", "get_observations", {})),), bound=InvalidTool()
    )
    assert isinstance(result, Stopped) and result.code == ErrorCode.MODEL_OUTPUT_REJECTED
    assert inference.calls == 1


@pytest.mark.trace("T-E13-024", "V27")
def test_cancel_after_context_preflight_never_invokes_inference() -> None:
    cancel = Cancel()

    class CancellingContext(Bound):
        def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot:
            snapshot = super().context(scope, budget)
            cancel.value = True
            return snapshot

    result, inference, _, _ = run((ok(),), bound=CancellingContext(), cancellation=cancel)
    assert isinstance(result, Stopped) and result.state == "cancelled"
    assert inference.calls == 0


@pytest.mark.trace("T-E13-021", "V27", "INV-E13-11")
def test_pinned_model_iteration_cap_can_only_reduce_policy_budget() -> None:
    reads = tuple(ok(call=ToolCall(f"call_{i}", "get_observations", {})) for i in range(3))
    result, inference, _, _ = run(reads, releases=Releases(iterations=1))
    assert isinstance(result, Completed) and result.candidate.payload["reason"] == "budget_exhausted"
    assert inference.calls == 1 and result.steps_used == 1


@pytest.mark.trace("T-E13-021", "V27", "INV-E13-11")
def test_pinned_tool_cap_refuses_a_multi_call_step_before_gateway_effects() -> None:
    two = replace(
        ok(call=ToolCall("call_a", "get_observations", {})),
        tool_calls=(ToolCall("call_a", "get_observations", {}), ToolCall("call_b", "get_observations", {})),
    )
    result, inference, _, bound = run((two, ok()), releases=Releases(tool_calls=1))
    assert isinstance(result, Completed) and result.candidate.payload["reason"] == "budget_exhausted"
    assert bound.calls == [] and inference.calls == 1
