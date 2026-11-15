"""Help turn guard (E26 3.7): what an assistant turn may display, rendered only from configuration.

Model words are never shown in a help session. A turn is accepted only when every displayed field is a whole
sequence of eligible reference texts of the current flow; the display text is then those configured texts. Every
missing input fails closed: no flow, no text rules, no kit gate answer or no plan snapshot means the reference or the
turn is rejected and the fixed steps stay the fallback. Deterministic, no I/O, no recursion.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from crew_health.help.domain.docs import FlowDef, normalize_text

RefKind = Literal["flow_step", "flow_question", "flow_measurement", "plan_step"]
RuleId = Literal["help_flow", "dose"]

ALLOWED_ACTION_KINDS = frozenset(
    {"ask", "request_measurement", "request_review", "stop_incomplete", "retrieve_evidence"}
)
ALLOWED_TOOLS = frozenset({"get_plan", "choose_plan_step", "propose_measurement", "search_evidence"})
_DISPLAYED_ACTION_FIELDS = {"ask": "question", "request_review": "note", "stop_incomplete": "missing"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_TRAILING = ".!?"
_MAX_STRINGS = 256


@dataclass(frozen=True, slots=True)
class ToolCallView:
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AssistantCandidate:
    help_turn_id: str
    text: str | None
    action: Mapping[str, object] | None
    tool_calls: tuple[ToolCallView, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Citation:
    kind: RefKind
    ref: str

    def token(self) -> str:
        return f"{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class GuardResult:
    accepted: bool
    rule_ids: tuple[RuleId, ...]
    field_paths: tuple[str, ...]
    citations: tuple[Citation, ...]
    display_text: str | None


@dataclass(frozen=True, slots=True)
class TextRules:
    """Amount pattern and medicine terms from E01's TextRulesPort for the pinned release."""

    amount_pattern: re.Pattern[str]
    medicine_terms: frozenset[str]


@dataclass(frozen=True, slots=True)
class PlanStepRef:
    step_id: str
    text: str
    eligible: bool


@dataclass(frozen=True, slots=True)
class GuardContext:
    flow: FlowDef | None
    text_rules: TextRules | None
    measurement_eligible: frozenset[int]
    plan_steps: tuple[PlanStepRef, ...]
    max_chars: int


@dataclass(frozen=True, slots=True)
class _Ref:
    citation: Citation
    text: str
    tokens: tuple[str, ...]
    eligible: bool


def _references(ctx: GuardContext) -> tuple[_Ref, ...]:
    flow = ctx.flow
    if flow is None:
        return ()
    refs: list[_Ref] = []

    def add(kind: RefKind, ref: str, text: str, eligible: bool) -> None:
        refs.append(_Ref(Citation(kind, ref), text, tuple(normalize_text(text).split(" ")), eligible))

    for i, step in enumerate(flow.immediate_steps):
        add("flow_step", str(i), step, True)
    for i, question in enumerate(flow.questions):
        add("flow_question", str(i), question, True)
    for i, measurement in enumerate(flow.measurements):
        add("flow_measurement", str(i), measurement.what, i in ctx.measurement_eligible)
    for plan_step in ctx.plan_steps:
        add("plan_step", plan_step.step_id, plan_step.text, plan_step.eligible)
    # Longest first, then a stable order: the longest match wins at equal positions.
    return tuple(sorted(refs, key=lambda r: (-len(r.tokens), r.citation.kind, r.citation.ref)))


def _matches(tokens: Sequence[str], start: int, ref: _Ref) -> bool:
    end = start + len(ref.tokens)
    if end > len(tokens):
        return False
    for offset, expected in enumerate(ref.tokens):
        actual = tokens[start + offset]
        if offset == len(ref.tokens) - 1:
            actual = actual.rstrip(_TRAILING)
        if actual != expected:
            return False
    return True


def segment(value: str, refs: Sequence[_Ref]) -> tuple[_Ref, ...] | None:
    """Dynamic programming over token positions; None when the text is not whole reference texts."""
    normalized = normalize_text(value)
    if not normalized:
        return None
    tokens = normalized.split(" ")
    best: list[tuple[_Ref, ...] | None] = [None] * (len(tokens) + 1)
    best[0] = ()
    for position in range(len(tokens)):
        reached = best[position]
        if reached is None:
            continue
        for ref in refs:
            if _matches(tokens, position, ref) and best[position + len(ref.tokens)] is None:
                best[position + len(ref.tokens)] = (*reached, ref)
    return best[len(tokens)]


def _strings(candidate: AssistantCandidate) -> tuple[list[tuple[str, str]], bool]:
    """Every string anywhere in the candidate with its JSON pointer, walked iteratively and bounded."""
    found: list[tuple[str, str]] = []
    stack: list[tuple[str, object]] = [("/text", candidate.text)]
    stack.extend((f"/limitations/{i}", v) for i, v in enumerate(candidate.limitations))
    stack.append(("/action", candidate.action))
    stack.extend(
        (f"/tool_calls/{i}/arguments", call.arguments) for i, call in enumerate(candidate.tool_calls)
    )
    while stack and len(found) < _MAX_STRINGS:
        pointer, value = stack.pop()
        if isinstance(value, str):
            found.append((pointer, value))
        elif isinstance(value, Mapping):
            stack.extend(
                (f"{pointer}/{key}", item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
            )
        elif isinstance(value, list | tuple):
            stack.extend((f"{pointer}/{i}", item) for i, item in enumerate(value))
    return sorted(found), bool(stack)


def _displayed(candidate: AssistantCandidate) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if candidate.text is not None:
        fields.append(("/text", candidate.text))
    action = candidate.action
    if action is not None:
        kind = action.get("kind")
        key = _DISPLAYED_ACTION_FIELDS.get(kind) if isinstance(kind, str) else None
        value = action.get(key) if key is not None else None
        if isinstance(value, str):
            fields.append((f"/action/{key}", value))
    fields.extend((f"/limitations/{i}", v) for i, v in enumerate(candidate.limitations))
    return fields


def _shape_failures(candidate: AssistantCandidate, ctx: GuardContext) -> list[str]:
    failures: list[str] = []
    if ctx.flow is None:
        failures.append("/")
    action = candidate.action
    if action is not None:
        kind = action.get("kind")
        if not isinstance(kind, str) or kind not in ALLOWED_ACTION_KINDS:
            failures.append("/action/kind")
        choices = action.get("choices")
        if kind == "ask" and choices not in (None, [], ()):
            failures.append("/action/choices")
    for i, call in enumerate(candidate.tool_calls):
        if call.name not in ALLOWED_TOOLS:
            failures.append(f"/tool_calls/{i}/name")
    return failures


def _dose_failures(candidate: AssistantCandidate, ctx: GuardContext) -> list[str]:
    rules = ctx.text_rules
    strings, overflow = _strings(candidate)
    if rules is None or overflow:
        # No amount pattern or medicine list, or more strings than the bound: nothing can be checked, nothing passes.
        return ["/"]
    allowed = {normalize_text(t.step) for t in (ctx.flow.treatment_steps if ctx.flow is not None else ())}
    allowed |= {normalize_text(p.text) for p in ctx.plan_steps if p.eligible}
    failures: list[str] = []
    for pointer, value in strings:
        if len(value) > ctx.max_chars or rules.amount_pattern.search(value) is not None:
            failures.append(pointer)
            continue
        for sentence in _SENTENCE.split(value):
            padded = f" {normalize_text(sentence)} "
            if any(f" {term} " in padded for term in rules.medicine_terms) and padded.strip() not in allowed:
                failures.append(pointer)
                break
    return failures


def check(candidate: AssistantCandidate, ctx: GuardContext) -> GuardResult:
    """Evaluate G0 to G6; a rejection lists every failed rule ID and field."""
    help_flow: list[str] = _shape_failures(candidate, ctx)
    dose = _dose_failures(candidate, ctx)
    refs = _references(ctx)
    resolved: list[_Ref] = []
    displayed = _displayed(candidate)
    for pointer, value in displayed:
        matched = segment(value, refs)
        if matched is None:
            help_flow.append(pointer)
        else:
            resolved.extend(matched)
    if displayed and not resolved:
        help_flow.append("/")
    eligible_plan = {p.step_id for p in ctx.plan_steps if p.eligible}
    for i, call in enumerate(candidate.tool_calls):
        if call.name == "choose_plan_step" and call.arguments.get("step_id") not in eligible_plan:
            help_flow.append(f"/tool_calls/{i}/arguments/step_id")
    help_flow.extend(f"/citations/{r.citation.token()}" for r in resolved if not r.eligible)
    rule_ids: list[RuleId] = []
    if help_flow:
        rule_ids.append("help_flow")
    if dose:
        rule_ids.append("dose")
    if rule_ids:
        return GuardResult(False, tuple(rule_ids), tuple(sorted(set(help_flow + dose))), (), None)
    citations = tuple(r.citation for r in resolved)
    text = " ".join(r.text for r in resolved) if resolved else None
    return GuardResult(True, (), (), citations, text)
