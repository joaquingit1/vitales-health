"""The conversation turn's Boundary for the controller (E13 3.1 to 3.6): context, tools, terminal proposals, final.

Every tool call goes through E12's gateway inside a unit of work opened as the signed-in person, so the gateway's own
scope, schema, phase and kit checks apply. The model's proposal becomes a stored action only after the domain guards
(reply_guard) and E12's output policy accept it. Nothing here executes the proposal: E12's EffectApplier creates a
*proposed* E05 measurement request at commit and the person accepts or declines it on E05's own route.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from crew_health.assistant.application.controller import Candidate
from crew_health.assistant.application.tools_copies import (
    ContextBudget,
    ContextSnapshot,
    OutputPolicyPort,
    PendingEffect,
    TextField,
    ToolGateway,
    ToolResult,
    ToolScope,
    TurnData,
)
from crew_health.assistant.application.tools_copies import ToolCall as GatewayCall
from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.forms import validate_form
from crew_health.assistant.domain.reply_guard import (
    KitLine,
    kit_lines,
    require_kit_can,
    require_no_fabricated_value,
    require_no_safety_change,
)
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.uow import UnitOfWork, UnitOfWorkFactory
from crew_health.model.application.ports import JsonValue, ToolCall


@dataclass(frozen=True, slots=True)
class TurnFacts:
    """What the turn was started with: the conversation's person messages and its context kind."""

    conversation_id: str
    context_kind: str
    person_messages: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _Seen:
    kit: dict[str, KitLine] = field(default_factory=dict)
    supported: list[str] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)


class TurnBoundary:
    def __init__(
        self,
        *,
        facts: TurnFacts,
        uows: UnitOfWorkFactory,
        gateway: ToolGateway,
        policy: OutputPolicyPort,
        new_call_id: Callable[[], str],
        max_context_messages: int,
    ) -> None:
        self.facts, self.uows, self.gateway, self.policy = facts, uows, gateway, policy
        self.new_call_id, self.max_context_messages = new_call_id, max_context_messages
        self.seen = _Seen(supported=[text for _, text in facts.person_messages])
        self._snapshot: ContextSnapshot | None = None

    # Context ---------------------------------------------------------------------------------------------------
    def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot:
        if self._snapshot is not None:
            return self._snapshot
        with self.uows.begin(actor=scope.session.actor, correlation_id=scope.turn_id) as uow:
            codes = _capability_codes(uow)
        recent = self.facts.person_messages[-self.max_context_messages :]
        sections: list[object] = [
            {"kind": "person_message", "message_id": message_id, "text": text} for message_id, text in recent
        ]
        sections.append({"kind": "capability_codes", "items": codes})
        raw = canonical_json({"sections": sections})
        self._snapshot = ContextSnapshot(
            canonical_json(sections),
            tuple(message_id for message_id, _ in recent),
            (),
            scope.config_release_id,
            scope.evidence_release_id,
            scope.model_release_id,
            scope.source_revision,
            hashlib.sha256(raw).hexdigest(),
        )
        return self._snapshot

    def fingerprint(self, snapshot: ContextSnapshot) -> str:
        return snapshot.snapshot_fingerprint

    # Tools -----------------------------------------------------------------------------------------------------
    def tool(self, scope: ToolScope, call: ToolCall) -> ToolResult:
        """One E12 gateway call as the signed-in person. The model's call ID stays on the transcript; the gateway
        and the audit see a hub-made ID, so a model string never becomes a stored identifier."""
        hub_id = self.new_call_id()
        arguments = canonical_json(dict(call.arguments))
        with self.uows.begin(actor=scope.session.actor, correlation_id=scope.turn_id) as uow:
            result = self.gateway.execute(uow, scope, GatewayCall(hub_id, call.name, arguments))
        if result.tool_call_id != hub_id:
            raise AssistantRefused("invalid_tool_result")
        data = None if result.data is None else result.data.decode("utf-8")
        if data is not None:
            self.seen.supported.append(data)
            if call.name == "get_capabilities" and result.status == "ok":
                self.seen.kit.update(kit_lines(_object(json.loads(data)).get("items")))
        self.seen.calls.append(
            {
                "tool_call_id": hub_id,
                "name": call.name,
                "arguments": json.loads(arguments),
                "status": result.status,
                "error_code": result.error_code,
                "limitations": list(result.limitations),
                "data": None if data is None else json.loads(data),
            }
        )
        return ToolResult(
            call.id,
            result.status,
            result.data,
            result.provenance_ids,
            result.observed_at,
            result.generated_at,
            result.limitations,
            result.error_code,
        )

    # Terminal proposals and final output -------------------------------------------------------------------------
    def terminal(
        self, scope: ToolScope, call: ToolCall, result: ToolResult, seen: frozenset[str]
    ) -> Candidate | None:
        if call.name != "propose_measurement" or result.status != "ok" or result.data is None:
            return None
        body = _object(json.loads(result.data))
        draft, capability = _object(body.get("draft")), _object(body.get("capability"))
        code = draft.get("capability_code")
        if not isinstance(code, str) or capability.get("code") != code:
            raise AssistantRefused("invalid_tool_result")
        if capability.get("status") != "available":
            return None  # E13 3.3: a proposal the kit cannot perform is not terminal; the turn continues.
        line = self.seen.kit.get(code)
        if line is not None:
            require_kit_can(code, self.seen.kit)
        validate_form(self.facts.context_kind, scope.phase.value, "request_measurement")
        action: dict[str, JsonValue] = {
            "action": "request_measurement",
            "reason": _text(draft.get("reason")),
            "decision_affected": _text(draft.get("decision_affected")),
            "state_snapshot_version": scope.source_revision,
            "capability_code": code,
            "body_region": _region(draft.get("body_region")),
            "condition_ids": _strings(draft.get("condition_ids")),
        }
        self._check(scope, action, ("reason", "decision_affected"))
        effect = PendingEffect(
            scope.turn_id,
            self.new_call_id(),
            scope.generation,
            scope.source_revision,
            canonical_json(action),
            (),
        )
        return Candidate("action", action, (effect,))

    def final(self, scope: ToolScope, output: Mapping[str, JsonValue], seen: frozenset[str]) -> Candidate:
        kind = output.get("kind")
        if kind != "stop":
            # Free text is not a form of the episode intake (E13 3.1) and READY needs the assessment job, which
            # this build does not run: both are refused, never shown.
            validate_form(self.facts.context_kind, scope.phase.value, "text" if kind == "text" else "ready")
            raise AssistantRefused("assessment_unavailable")
        missing_codes = output.get("missing_codes")
        if not isinstance(missing_codes, list) or not missing_codes:
            raise AssistantRefused("missing_required")
        validate_form(self.facts.context_kind, scope.phase.value, "stop_incomplete")
        action: dict[str, JsonValue] = {
            "action": "stop_incomplete",
            "reason": _text(output.get("reason")),
            "decision_affected": None,
            "state_snapshot_version": scope.source_revision,
            "missing": [self._missing(code) for code in missing_codes],
        }
        self._check(scope, action, ("reason",))
        return Candidate("action", action)

    def incomplete(self, scope: ToolScope, seen: frozenset[str]) -> Candidate:
        raise AssistantRefused("budget_exhausted")

    def _missing(self, code: object) -> str:
        if not isinstance(code, str) or code not in self.seen.kit:
            raise AssistantRefused("missing_code_unknown")
        return code

    def _check(self, scope: ToolScope, action: Mapping[str, JsonValue], fields: tuple[str, ...]) -> None:
        require_no_safety_change(action)
        texts = [str(action[name]) for name in fields if isinstance(action.get(name), str)]
        require_no_fabricated_value(texts, self.seen.supported)
        with self.uows.begin(actor=scope.session.actor, correlation_id=scope.turn_id) as uow:
            verdict = self.policy.check(
                tuple(TextField(name, text, None) for name, text in zip(fields, texts, strict=False)),
                scope,
                TurnData(uow.release, scope.source_revision, ()),
            )
        if not verdict.allowed:
            raise AssistantRefused("output_policy")


def kit_limitations(uow: UnitOfWork, kit: Mapping[str, KitLine]) -> list[str]:
    """The kit check's gaps in configured words (ui-copy kit_check_templates, kit-map needs_text): one line per
    capability this turn found unavailable. Built from tool results only, never from model text."""
    copy = _object(json.loads(uow.release.document(ConfigName.UI_COPY).raw)).get("kit_check_templates")
    template = _object(copy).get("cannot")
    kit_map = _object(json.loads(uow.release.document(ConfigName.KIT_MAP).raw))
    needs = {
        str(item.get("id")): str(item.get("needs_text"))
        for item in _list(kit_map.get("instruments"))
        if isinstance(item, dict) and isinstance(item.get("needs_text"), str)
    }
    if not isinstance(template, str):
        raise AssistantRefused("copy_unavailable")
    lines: list[str] = []
    for code in sorted(kit):
        line = kit[code]
        if line.status == "available":
            continue
        words = [needs[item] for item in line.missing_instrument_ids if item in needs]
        if words:
            lines.append(line.label + ": " + template.format(instruments=" or ".join(words)))
    return lines


def _capability_codes(uow: UnitOfWork) -> list[object]:
    kit_map = _object(json.loads(uow.release.document(ConfigName.KIT_MAP).raw))
    return [
        {"code": item.get("code"), "label": item.get("label")}
        for item in _list(kit_map.get("capabilities"))
        if isinstance(item, dict)
    ]


def _object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise AssistantRefused("invalid_tool_result")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AssistantRefused("invalid_tool_result")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssistantRefused("reason_required")
    return value


def _strings(value: object) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssistantRefused("invalid_tool_result")
    return list(value)


def _region(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise AssistantRefused("invalid_body_region")
