"""Loader checks for `warning-rules.json` (E24 3.2, 8, R-E24-10, CONTRACTS 11 checks 5 and 13).

Pure functions over the parsed candidate and the documents it refers to. They only reject: a finding
makes the candidate invalid and the last good release stays. They never downgrade a failure and never
invent content. The prohibited-phrase rule takes its phrase list from configuration; with none declared
the check does not run (a coordinator prerequisite), it never falls back to a built-in list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Final

from crew_health.kernel.config import Finding
from crew_health.rules.domain.definitions import (
    InsightCondition,
    RulesDocument,
    SensorLimitCondition,
    WarningDefinition,
    parse_rules_document,
)
from crew_health.rules.domain.errors import DefinitionInvalid
from crew_health.rules.domain.vocabulary import ConditionKind, StepsFrom

# CONTRACTS section 11 "Templates": the placeholders a warning-rules content field may use.
CONTENT_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {
        "aggregation_label", "value", "unit", "unit_display", "limit_value", "location", "sensor_name",
        "source_label", "last_received_time", "answer_day_label", "answer_time", "day_short", "eva_word",
        "insight_title", "insight_text", "insight_why", "insight_fragment", "silence_text",
        "next_contact_time", "supply_name", "uses", "quantity", "per_use",
    }
)  # fmt: skip
VALUE_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {"aggregation_label", "value", "unit", "unit_display", "limit_value"}
)
_PLACEHOLDER: Final = re.compile(r"\{([a-z_]+)\}")

_HIGH_RAISE = frozenset({"above", "at_or_above"})
_STRICT_PAIRS: Final[Mapping[str, tuple[str, ...]]] = {
    "above": ("at_or_below", "below"),
    "at_or_above": ("below",),
    "below": ("at_or_above", "above"),
    "at_or_below": ("above",),
}


def _finding(check: str, pointer: str, message: str) -> Finding:
    # The application stamps `document=ConfigName.WARNING_RULES`; the domain imports no generated enum.
    return Finding(check, None, pointer, message)


def _templates(d: WarningDefinition) -> tuple[tuple[str, str], ...]:
    c = d.content
    return (
        ("title", c.title),
        ("what", c.what),
        ("why", c.why),
        ("headline.fragment", c.headline.fragment),
        ("citation_template", c.citation_template),
    )


def check_placeholders(doc: RulesDocument) -> tuple[Finding, ...]:
    """Check 13, plus: a definition that is not a sensor limit uses no value placeholder."""
    found: list[Finding] = []
    for i, d in enumerate(doc.definitions):
        for name, text in _templates(d):
            for ph in sorted(set(_PLACEHOLDER.findall(text))):
                pointer = f"/definitions/{i}/content/{name.replace('.', '/')}"
                if ph not in CONTENT_PLACEHOLDERS:
                    found.append(
                        _finding("13", pointer, f"placeholder {{{ph}}} is not listed for this field")
                    )
                elif ph in VALUE_PLACEHOLDERS and d.condition.kind is not ConditionKind.SENSOR_LIMIT:
                    found.append(
                        _finding("rules.value_placeholder", pointer, f"{{{ph}}} needs a sensor limit")
                    )
    return tuple(found)


def check_expiry_scope(doc: RulesDocument) -> tuple[Finding, ...]:
    """Non-null `expires_after_seconds` only on `assistant_insight` (rules.expiry_scope)."""
    return tuple(
        _finding(
            "rules.expiry_scope", f"/definitions/{i}/clear/expires_after_seconds", "expiry is not evidence"
        )
        for i, d in enumerate(doc.definitions)
        if d.clear.expires_after_seconds is not None
        and d.condition.kind is not ConditionKind.ASSISTANT_INSIGHT
    )


def check_max_definitions(doc: RulesDocument, maximum: int) -> tuple[Finding, ...]:
    if len(doc.definitions) > maximum:
        return (_finding("rules.max_definitions", "/definitions", f"more than {maximum} definitions"),)
    return ()


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def clear_is_safe(raise_cmp: str, raise_value: Decimal, clear_cmp: str, clear_value: Decimal) -> bool:
    """A limit's clear is the complement of its raise or stricter, never weaker."""
    if clear_cmp not in _STRICT_PAIRS.get(raise_cmp, ()):
        return False
    high = raise_cmp in _HIGH_RAISE
    if clear_value == raise_value:
        # Equal values: only the complement or a stricter comparator is safe.
        return clear_cmp in _STRICT_PAIRS[raise_cmp]
    return clear_value < raise_value if high else clear_value > raise_value


def _limit_for(
    d: WarningDefinition, limits: Mapping[str, Mapping[str, object]]
) -> tuple[str, Mapping[str, object] | None]:
    cond = d.condition
    if isinstance(cond, SensorLimitCondition):
        return cond.limit_id, limits.get(cond.limit_id)
    return "", None


def check_limits(doc: RulesDocument, limits: Mapping[str, Mapping[str, object]]) -> tuple[Finding, ...]:
    """Check 5 and R-E24-10: level and sensor type equal the limit's; the clear is safe."""
    found: list[Finding] = []
    named: dict[str, int] = {}
    for i, d in enumerate(doc.definitions):
        limit_id, entry = _limit_for(d, limits)
        if not limit_id:
            continue
        named[limit_id] = named.get(limit_id, 0) + 1
        pointer = f"/definitions/{i}/condition/limit_id"
        if entry is None:
            found.append(_finding("5", pointer, f"limit {limit_id} does not exist"))
            continue
        if entry.get("level") != d.level.value:
            found.append(_finding("rules.limit_level", pointer, "definition level differs from its limit"))
        if entry.get("sensor_type_id") != d.sensor_type_id:
            found.append(
                _finding("rules.limit_sensor", pointer, "definition sensor type differs from its limit")
            )
        found.extend(_clear_finding(pointer, entry))
    for limit_id, entry in sorted(limits.items()):
        if entry.get("status") == "active" and entry.get("role") == "warning" and named.get(limit_id, 0) != 1:
            found.append(
                _finding("5", "/definitions", f"active limit {limit_id} needs exactly one definition")
            )
    return tuple(found)


def _clear_finding(pointer: str, entry: Mapping[str, object]) -> list[Finding]:
    clear = entry.get("clear")
    raise_value = _decimal(entry.get("value"))
    if raise_value is None or not isinstance(clear, Mapping):
        return []
    clear_value = _decimal(clear.get("value"))
    raise_cmp, clear_cmp = entry.get("comparator"), clear.get("comparator")
    if clear_value is None or not isinstance(raise_cmp, str) or not isinstance(clear_cmp, str):
        return [_finding("rules.limit_clear", pointer, "a limit with a threshold needs a complete clear")]
    if not clear_is_safe(raise_cmp, raise_value, clear_cmp, clear_value):
        return [
            _finding("rules.limit_clear", pointer, "a clear must be the complement of its raise or stricter")
        ]
    return []


def check_hazard_steps(
    doc: RulesDocument, what_to_do: Mapping[str, Mapping[str, str]]
) -> tuple[Finding, ...]:
    """`hazard_what_to_do` needs `what_to_do[level]` on the definition's hazard."""
    found: list[Finding] = []
    for i, d in enumerate(doc.definitions):
        if d.content.steps.source is not StepsFrom.HAZARD_WHAT_TO_DO or d.hazard_id is None:
            continue
        text = what_to_do.get(d.hazard_id, {}).get(d.level.value)
        if not text:
            found.append(
                _finding(
                    "rules.hazard_steps", f"/definitions/{i}/content/steps", "no what_to_do at this level"
                )
            )
    return tuple(found)


def check_topics(doc: RulesDocument, topics: Sequence[Mapping[str, object]]) -> tuple[Finding, ...]:
    """Every topic's `warning_definition_id` names an `assistant_insight` definition listing the topic."""
    by_id = doc.by_id()
    found: list[Finding] = []
    for topic in topics:
        target = topic.get("warning_definition_id")
        if target is None:
            continue
        d = by_id.get(str(target))
        cond = d.condition if d is not None else None
        if not isinstance(cond, InsightCondition) or str(topic.get("id")) not in cond.topic_ids:
            found.append(
                _finding(
                    "rules.topic_definition", "/definitions", f"topic {topic.get('id')} has no definition"
                )
            )
    return tuple(found)


def check_phrases(doc: RulesDocument, phrases: Sequence[str] | None) -> tuple[Finding, ...]:
    """No text states the vehicle or its air is safe. Runs only with a configured phrase list."""
    if not phrases:
        return ()
    lowered = [p.lower() for p in phrases if p]
    found: list[Finding] = []
    for i, d in enumerate(doc.definitions):
        for name, text in _templates(d) + tuple(("steps", s) for s in d.content.steps.items):
            if any(p in text.lower() for p in lowered):
                found.append(
                    _finding(
                        "rules.prohibited_phrase", f"/definitions/{i}/content/{name}", "prohibited statement"
                    )
                )
    return tuple(found)


def check_document(
    raw: object,
    *,
    max_definitions: int,
    limits: Mapping[str, Mapping[str, object]],
    what_to_do: Mapping[str, Mapping[str, str]],
    topics: Sequence[Mapping[str, object]],
    prohibited_phrases: Sequence[str] | None,
) -> tuple[Finding, ...]:
    """Every check over one candidate. An unparsable document is one finding and nothing else runs."""
    try:
        doc = parse_rules_document(raw)
    except DefinitionInvalid as error:
        return (_finding("1", "", error.detail),)
    return (
        *check_max_definitions(doc, max_definitions),
        *check_expiry_scope(doc),
        *check_placeholders(doc),
        *check_limits(doc, limits),
        *check_hazard_steps(doc, what_to_do),
        *check_topics(doc, topics),
        *check_phrases(doc, prohibited_phrases),
    )
