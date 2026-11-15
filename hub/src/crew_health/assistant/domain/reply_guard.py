"""Guards on what a conversation turn may store (AGENTS section 1 rules 4 and 5).

These are code, never prompt text or configuration: the prompt may ask the model to behave, but only these checks
decide what reaches the crew. Every function raises AssistantRefused, which the controller turns into an honest
`rejected` turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from crew_health.assistant.domain.errors import AssistantRefused

# The only action kinds a conversation turn can store (contracts model-action.schema.json, E13 3.1). None of them
# carries a warning level, an acknowledgement or a clear: the assistant has no way to change safety state.
TURN_ACTIONS = frozenset({"ask", "request_measurement", "stop_incomplete"})

# Field names that would touch safety state. An action that carries any of them is refused even if its kind is
# allowed, so a future schema widening cannot smuggle a level change through a proposal.
_SAFETY_FIELDS = frozenset(
    {
        "level",
        "warning_level",
        "warning_id",
        "warning_definition_id",
        "acknowledge",
        "acknowledged",
        "clear",
        "cleared",
        "lower",
        "raise",
        "urgency",
        "urgency_level",
        "emergency",
        "act_now",
    }
)

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")


def require_no_safety_change(action: Mapping[str, object]) -> None:
    """AGENTS rule 4: a stored action never raises Act now or Emergency and never lowers or clears a warning."""
    if action.get("action") not in TURN_ACTIONS or _SAFETY_FIELDS & set(action):
        raise AssistantRefused("safety_state_not_allowed")


def require_no_fabricated_value(texts: Iterable[str], supported: Iterable[str]) -> None:
    """AGENTS rule 5: every number the model wrote must appear in data the hub gave it this turn (its tool results
    or the person's own words). A value with no source is a fabrication and the whole output is refused."""
    allowed: set[str] = set()
    for source in supported:
        allowed.update(_NUMBER.findall(source))
    for text in texts:
        if any(number not in allowed for number in _NUMBER.findall(text)):
            raise AssistantRefused("fabricated_value")


@dataclass(frozen=True, slots=True)
class KitLine:
    """One capability as the hub's kit check reported it this turn (E05 registry through E12 get_capabilities)."""

    code: str
    label: str
    status: str
    missing_instrument_ids: tuple[str, ...]


def kit_lines(items: object) -> dict[str, KitLine]:
    """The kit check result's items, parsed strictly. Anything malformed is refused, never guessed."""
    if not isinstance(items, list):
        raise AssistantRefused("invalid_tool_result")
    lines: dict[str, KitLine] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise AssistantRefused("invalid_tool_result")
        code, label, status = item.get("code"), item.get("label"), item.get("status")
        missing = item.get("missing_instrument_ids")
        if (
            not isinstance(code, str)
            or not isinstance(label, str)
            or not isinstance(status, str)
            or not isinstance(missing, list)
            or not all(isinstance(value, str) for value in missing)
        ):
            raise AssistantRefused("invalid_tool_result")
        lines[code] = KitLine(code, label, status, tuple(str(value) for value in missing))
    return lines


def require_kit_can(capability_code: str, kit: Mapping[str, KitLine]) -> KitLine:
    """A measurement request reaches a person only for a capability this turn's kit check found available
    (LLM-ARCHITECTURE 4: the controller rejects any request the kit cannot perform)."""
    line = kit.get(capability_code)
    if line is None or line.status != "available":
        raise AssistantRefused("kit_cannot_perform")
    return line
