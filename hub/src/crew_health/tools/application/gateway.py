from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from threading import Lock

from crew_health.contracts_gen.schemas.config_assistant_policy_schema import AssistantPolicyJson
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock, ClockCorrections, TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import ElapsedDeadline
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

type PolicySource = Callable[[UnitOfWork], AssistantPolicyJson | None]
type CatalogSource = Callable[[str], ArgumentValidator | None]
type TimeCapture = Callable[[UnitOfWork, TimeInput], TimeInput | None]


@dataclass(slots=True)
class _Turn:
    generation: int
    calls: int
    deadline: ElapsedDeadline


@dataclass(frozen=True, slots=True)
class _Pins:
    catalog: ArgumentValidator
    policy: AssistantPolicyJson


class Gateway:
    """Tool calls of controller turns: no queue, response cache, transaction ownership or external worker call.

    Each turn keeps its own call count, bounded by the pinned assistant policy and the hub ceiling; the set of open
    turns is bounded by `tools.max_open_turns` and only an expired turn is ever forgotten. Time is the shared
    correction-aware elapsed time from the turn's anchor, checked before dispatch and again after the handler."""

    def __init__(
        self,
        registry: ToolRegistry,
        catalog: CatalogSource,
        settings: HubSettings,
        authority: ScopeAuthority,
        clock: Clock,
        ids: IdFactory,
        *,
        policy: PolicySource | None = None,
        corrections: ClockCorrections | None = None,
        capture: TimeCapture | None = None,
    ) -> None:
        self._bounds = argument_bounds(settings)
        self._result_bounds = JsonBounds(
            settings.integer("tools.max_result_bytes"),
            self._bounds.max_depth,
            self._bounds.max_array_items,
            self._bounds.max_string_chars,
        )
        self._ceiling = settings.integer("tools.max_calls_per_turn")
        self._max_turns = settings.integer("tools.max_open_turns")
        self._timeout_ns = settings.integer("tools.timeout_ms") * 1_000_000
        self._turn_ns = settings.integer("tools.turn_timeout_ms") * 1_000_000
        self._registry, self._catalog, self._authority = registry, catalog, authority
        self._policy, self._corrections, self._capture = policy, corrections, capture
        self._clock, self._ids = clock, ids
        self._epoch = clock.process_epoch()
        self._turns: dict[str, _Turn] = {}
        self._lock = Lock()

    def _audit(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        call: ToolCall,
        status: str,
        reason: DenialReason | None,
        catalog: ArgumentValidator | None,
    ) -> None:
        """Allowlisted metadata only; the session is the stored reference (an unknown tool name never logs)."""
        metadata: dict[str, str | int | bool] = {"status": status}
        if catalog is not None and catalog.effect(call.name) is not None:
            metadata["tool"] = call.name
        if reason is not None:
            metadata["reason"] = reason
        uow.audit(
            AuditEntry(
                self._ids.new("aud"),
                "tools.denied" if status == "denied" else "tools.executed",
                1,
                uow.actor,
                {"session": scope.session.session_id},
                uow.correlation_id,
                None,
                metadata,
            )
        )

    def _deny(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        call: ToolCall,
        reason: DenialReason,
        catalog: ArgumentValidator | None,
    ) -> ToolResult:
        self._audit(uow, scope, call, "denied", reason, catalog)
        return ToolResult(call.tool_call_id, "denied", None, (), None, uow.now, (), "MODEL_OUTPUT_REJECTED")

    def _unavailable(
        self, uow: UnitOfWork, scope: ToolScope, call: ToolCall, catalog: ArgumentValidator | None
    ) -> ToolResult:
        self._audit(uow, scope, call, "unavailable", "tool_not_wired", catalog)
        return self._result(uow, call, HandlerUnavailable(()))

    def _pins(self, uow: UnitOfWork, scope: ToolScope) -> _Pins | None:
        if self._policy is None or self._corrections is None or self._capture is None:
            return None
        catalog = self._catalog(scope.model_release_id)
        policy = self._policy(uow)
        if catalog is None or policy is None or catalog.model_release_id != scope.model_release_id:
            return None
        return _Pins(catalog, policy)

    def _admit(self, uow: UnitOfWork, scope: ToolScope, pins: _Pins) -> DenialReason | None:
        """Per-turn generation fence and call budget. A full turn table forgets only turns whose time is over."""
        turn = self._turns.get(scope.turn_id)
        if turn is None:
            if len(self._turns) >= self._max_turns:
                for turn_id, open_turn in tuple(self._turns.items()):
                    if self._left(uow, open_turn.deadline, pins.policy) is None:
                        del self._turns[turn_id]
            if len(self._turns) >= self._max_turns:
                return "context_over_budget"
            turn = _Turn(scope.generation, 0, scope.deadline)
            self._turns[scope.turn_id] = turn
        if turn.generation != scope.generation or turn.deadline != scope.deadline:
            return "stale"
        maximum = min(self._ceiling, pins.policy.max_iterations)
        turn.calls = min(turn.calls + 1, maximum + 1)
        return "context_over_budget" if turn.calls > maximum else None

    def _preflight(
        self, uow: UnitOfWork, scope: ToolScope, call: ToolCall, pins: _Pins
    ) -> DenialReason | None:
        reason = self._admit(uow, scope, pins)
        if reason is not None:
            return reason
        if pins.catalog.effect(call.name) is None:
            return "unknown_tool"
        if not pins.catalog.exposed(call.name, scope.phase):
            return "out_of_stage"
        try:
            require_own_subject(scope.session, scope.subject_person_id)
        except ToolsInvalid:
            return "scope"
        if (
            scope.config_release_id != uow.release.release_id
            or uow.actor != scope.session.actor
            or not self._authority.require_current(uow, scope)
        ):
            return "scope"
        return None

    def execute(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> ToolResult:
        if not self._lock.acquire(blocking=False):
            return self._deny(uow, scope, call, "context_over_budget", None)
        try:
            pins = self._pins(uow, scope)
            if pins is None:
                return self._unavailable(uow, scope, call, None)
            catalog = pins.catalog
            reason = self._preflight(uow, scope, call, pins)
            if reason is not None:
                return self._deny(uow, scope, call, reason, catalog)
            try:
                arguments = catalog.parse(call.name, call.arguments)
            except ToolsInvalid:
                return self._deny(uow, scope, call, "malformed_schema", catalog)
            if not self._authority.authorize_arguments(uow, scope, call.name, arguments):
                return self._deny(uow, scope, call, "scope", catalog)
            if self._remaining(uow, scope.deadline, pins.policy) is None:
                return self._deny(uow, scope, call, "tool_timeout", catalog)
            handler = self._registry.handler(call.name)
            if handler is None:
                return self._unavailable(uow, scope, call, catalog)
            if handler.effect != catalog.effect(call.name):
                return self._deny(uow, scope, call, "handler_rejected", catalog)
            return self._invoke(uow, scope, call, arguments, handler, pins)
        finally:
            self._lock.release()

    def _invoke(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        call: ToolCall,
        arguments: Mapping[str, object],
        handler: ToolHandler,
        pins: _Pins,
    ) -> ToolResult:
        catalog = pins.catalog
        epoch, started = self._clock.process_epoch(), self._clock.monotonic_ns()
        remaining = self._remaining(uow, scope.deadline, pins.policy)
        anchor = self._now(uow, scope.deadline.anchor)
        if remaining is None or anchor is None:
            return self._deny(uow, scope, call, "tool_timeout", catalog)
        outcome = handler.handle(
            uow, scope, arguments, replace(scope.deadline, anchor=anchor, duration=remaining)
        )
        elapsed = self._clock.monotonic_ns() - started
        if (
            epoch != self._clock.process_epoch()
            or elapsed < 0
            or elapsed >= int(remaining.total_seconds() * 1_000_000_000)
            or self._remaining(uow, scope.deadline, pins.policy) is None
        ):
            return self._deny(uow, scope, call, "tool_timeout", catalog)
        if not self._authority.require_current(uow, scope):
            return self._deny(uow, scope, call, "stale", catalog)
        if isinstance(outcome, HandlerRejected):
            return self._deny(uow, scope, call, outcome.reason, catalog)
        try:
            result = self._result(uow, call, outcome)
            if isinstance(outcome, HandlerOk):
                data = catalog.validate_result(call.name, outcome.data)
                if not self._authority.authorize_result(uow, scope, call.name, data):
                    return self._deny(uow, scope, call, "scope", catalog)
        except ToolsInvalid as error:
            if error.code == "CONFIG_INVALID":
                raise
            if error.code == "CAPABILITY_UNAVAILABLE":
                return self._unavailable(uow, scope, call, catalog)
            return self._deny(uow, scope, call, "handler_rejected", catalog)
        self._audit(uow, scope, call, result.status, None, catalog)
        return result

    def _now(self, uow: UnitOfWork, anchor: TimeInput) -> TimeInput | None:
        if self._capture is None:
            return None
        return self._capture(uow, anchor)

    def _left(
        self, uow: UnitOfWork, deadline: ElapsedDeadline, policy: AssistantPolicyJson
    ) -> timedelta | None:
        """Turn time left: the stricter of the turn's own allowance, the hub ceiling and the pinned policy, less
        the correction-aware time since the anchor. Unknown time, a negative span or a new process means none."""
        if self._corrections is None or self._clock.process_epoch() != self._epoch:
            return None
        now = self._now(uow, deadline.anchor)
        if now is None:
            return None
        elapsed = self._corrections.elapsed(uow, deadline.anchor, now)
        if elapsed is None or elapsed < timedelta(0):
            return None
        limit = min(
            deadline.duration,
            timedelta(microseconds=self._turn_ns // 1000),
            timedelta(seconds=policy.turn_timeout_seconds),
        )
        left = limit - elapsed
        return left if left > timedelta(0) else None

    def _remaining(
        self, uow: UnitOfWork, deadline: ElapsedDeadline, policy: AssistantPolicyJson
    ) -> timedelta | None:
        left = self._left(uow, deadline, policy)
        if left is None:
            return None
        tool_limit = min(
            timedelta(microseconds=self._timeout_ns // 1000),
            timedelta(seconds=policy.tool_timeout_seconds),
        )
        return min(left, tool_limit)

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
