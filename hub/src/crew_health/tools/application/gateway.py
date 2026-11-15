from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Lock

from crew_health.contracts_gen.schemas.config_assistant_policy_schema import AssistantPolicyJson
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock, ClockCorrections, TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork
from crew_health.tools.application.ports import (
    ArgumentValidator,
    HandlerNoResult,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    HandlerUnavailable,
    ScopeAuthority,
    ToolHandler,
    ToolRegistry,
)
from crew_health.tools.domain.arguments import JsonBounds, parse_json
from crew_health.tools.domain.denials import DenialReason
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.scope import ToolScope, require_own_subject
from crew_health.tools.domain.tool_result import ToolCall, ToolResult
from crew_health.tools.settings import argument_bounds


class Gateway:
    """One controller turn, no queue, response cache, transaction ownership or external worker call."""

    def __init__(
        self,
        registry: ToolRegistry,
        catalog: ArgumentValidator,
        settings: HubSettings,
        authority: ScopeAuthority,
        clock: Clock,
        ids: IdFactory,
        *,
        policy: AssistantPolicyJson | None = None,
        corrections: ClockCorrections | None = None,
        capture: Callable[[datetime], TimeInput] | None = None,
    ) -> None:
        self._bounds = argument_bounds(settings)
        self._result_bounds = JsonBounds(
            int(settings.quantity("tools.max_result_bytes").value),
            self._bounds.max_depth,
            self._bounds.max_array_items,
            self._bounds.max_string_chars,
        )
        self._maximum = (
            0 if policy is None else min(settings.integer("tools.max_calls_per_turn"), policy.max_iterations)
        )
        self._timeout_ns = int(settings.quantity("tools.timeout_ms").value * 1000000)
        self._registry, self._catalog, self._authority = registry, catalog, authority
        self._policy, self._corrections, self._capture = policy, corrections, capture
        self._turn_ns = int(settings.quantity("tools.turn_timeout_ms").value * 1000000)
        self._clock, self._ids = clock, ids
        self._epoch = clock.process_epoch()
        self._calls = 0
        self._turn: tuple[str, int] | None = None
        self._lock = Lock()

    def _audit(
        self, uow: UnitOfWork, scope: ToolScope, call: ToolCall, status: str, reason: DenialReason | None
    ) -> None:
        metadata: dict[str, str | int | bool] = {"status": status}
        if self._catalog.effect(call.name) is not None:
            metadata["tool"] = call.name
        if reason is not None:
            metadata["reason"] = reason
        uow.audit(
            AuditEntry(
                self._ids.new("aud"),
                "tools.denied" if status == "denied" else "tools.executed",
                1,
                uow.actor,
                {"turn_id": scope.turn_id, "tool_call_id": call.tool_call_id},
                uow.correlation_id,
                None,
                metadata,
            )
        )

    def _deny(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall, reason: DenialReason) -> ToolResult:
        self._audit(uow, scope, call, "denied", reason)
        return ToolResult(call.tool_call_id, "denied", None, (), None, uow.now, (), "MODEL_OUTPUT_REJECTED")

    def _preflight(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> DenialReason | None:
        if self._turn is None:
            self._turn = (scope.turn_id, scope.generation)
        if self._turn != (scope.turn_id, scope.generation):
            return "stale"
        self._calls = min(self._calls + 1, self._maximum + 1)
        if self._calls > self._maximum:
            return "context_over_budget"
        if self._catalog.effect(call.name) is None:
            return "unknown_tool"
        if not self._catalog.exposed(call.name, scope.phase):
            return "out_of_stage"
        try:
            require_own_subject(scope.session, scope.subject_person_id)
        except ToolsInvalid:
            return "scope"
        if (
            scope.config_release_id != uow.release.release_id
            or scope.model_release_id != self._catalog.model_release_id
            or uow.actor != scope.session.actor
            or not self._authority.require_current(uow, scope)
        ):
            return "scope"
        return None

    def execute(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> ToolResult:
        if not self._lock.acquire(blocking=False):
            return self._deny(uow, scope, call, "context_over_budget")
        try:
            if self._policy is None or self._corrections is None or self._capture is None:
                self._audit(uow, scope, call, "unavailable", "tool_not_wired")
                return self._result(uow, call, HandlerUnavailable(()))
            reason = self._preflight(uow, scope, call)
            if reason is not None:
                return self._deny(uow, scope, call, reason)
            try:
                arguments = self._catalog.parse(call.name, call.arguments)
            except ToolsInvalid:
                return self._deny(uow, scope, call, "malformed_schema")
            if not self._authority.authorize_arguments(uow, scope, call.name, arguments):
                return self._deny(uow, scope, call, "scope")
            if self._remaining(uow, scope) is None:
                return self._deny(uow, scope, call, "tool_timeout")
            handler = self._registry.handler(call.name)
            if handler is None:
                self._audit(uow, scope, call, "unavailable", "tool_not_wired")
                return ToolResult(
                    call.tool_call_id, "unavailable", None, (), None, uow.now, (), "CAPABILITY_UNAVAILABLE"
                )
            if handler.effect != self._catalog.effect(call.name):
                return self._deny(uow, scope, call, "handler_rejected")
            return self._invoke(uow, scope, call, arguments, handler)
        finally:
            self._lock.release()

    def _invoke(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        call: ToolCall,
        arguments: Mapping[str, object],
        handler: ToolHandler,
    ) -> ToolResult:
        epoch, started = self._clock.process_epoch(), self._clock.monotonic_ns()
        remaining = self._remaining(uow, scope)
        if remaining is None or self._capture is None:
            return self._deny(uow, scope, call, "tool_timeout")
        deadline = replace(scope.deadline, anchor=self._capture(self._clock.now()), duration=remaining)
        outcome = handler.handle(uow, scope, arguments, deadline)
        elapsed = self._clock.monotonic_ns() - started
        remaining_ns = int(remaining.total_seconds() * 1000000000)
        if (
            epoch != self._clock.process_epoch()
            or elapsed < 0
            or elapsed >= remaining_ns
            or self._remaining(uow, scope) is None
        ):
            return self._deny(uow, scope, call, "tool_timeout")
        if not self._authority.require_current(uow, scope):
            return self._deny(uow, scope, call, "stale")
        if isinstance(outcome, HandlerRejected):
            return self._deny(uow, scope, call, outcome.reason)
        try:
            result = self._result(uow, call, outcome)
            if isinstance(outcome, HandlerOk):
                data = self._catalog.validate_result(call.name, outcome.data)
                if not self._authority.authorize_result(uow, scope, call.name, data):
                    return self._deny(uow, scope, call, "scope")
        except ToolsInvalid as error:
            if error.code == "CONFIG_INVALID":
                raise
            if error.code == "CAPABILITY_UNAVAILABLE":
                self._audit(uow, scope, call, "unavailable", "tool_not_wired")
                return self._result(uow, call, HandlerUnavailable(()))
            return self._deny(uow, scope, call, "handler_rejected")
        self._audit(uow, scope, call, result.status, None)
        return result

    def _remaining(self, uow: UnitOfWork, scope: ToolScope) -> timedelta | None:
        if self._policy is None or self._corrections is None or self._capture is None:
            return None
        if self._clock.process_epoch() != self._epoch:
            return None
        elapsed = self._corrections.elapsed(uow, scope.deadline.anchor, self._capture(self._clock.now()))
        if elapsed is None or elapsed < timedelta(0):
            return None
        turn_limit = min(
            scope.deadline.duration,
            timedelta(microseconds=self._turn_ns // 1000),
            timedelta(seconds=self._policy.turn_timeout_seconds),
        )
        remaining = turn_limit - elapsed
        tool_limit = min(
            timedelta(microseconds=self._timeout_ns // 1000),
            timedelta(seconds=self._policy.tool_timeout_seconds),
        )
        return min(remaining, tool_limit) if remaining > timedelta(0) else None

    def _result(self, uow: UnitOfWork, call: ToolCall, outcome: HandlerOutcome) -> ToolResult:
        if isinstance(outcome, HandlerOk):
            parse_json(outcome.data, self._result_bounds)
            result = ToolResult(
                call.tool_call_id,
                "ok",
                outcome.data,
                outcome.provenance_ids,
                outcome.observed_at,
                uow.now,
                outcome.limitations,
                None,
            )
        elif isinstance(outcome, HandlerNoResult):
            result = ToolResult(
                call.tool_call_id, "no_result", None, (), None, uow.now, outcome.limitations, None
            )
        elif isinstance(outcome, HandlerUnavailable):
            result = ToolResult(
                call.tool_call_id,
                "unavailable",
                None,
                (),
                None,
                uow.now,
                outcome.limitations,
                "CAPABILITY_UNAVAILABLE",
            )
        else:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        parse_json(
            canonical_json(
                {
                    "data": None if result.data is None else parse_json(result.data, self._result_bounds),
                    "provenance_ids": result.provenance_ids,
                    "limitations": result.limitations,
                    "tool_call_id": result.tool_call_id,
                    "status": result.status,
                    "observed_at": None if result.observed_at is None else result.observed_at.isoformat(),
                    "generated_at": result.generated_at.isoformat(),
                    "error_code": result.error_code,
                }
            ),
            self._result_bounds,
        )
        return result
