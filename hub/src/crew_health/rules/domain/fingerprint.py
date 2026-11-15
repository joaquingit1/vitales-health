"""Content hash and rule fingerprint of a definition (E24 3.2).

`content_hash` covers the definition's `content`. `rule_fingerprint` covers everything that decides
raising or clearing: the definition without content plus the values of other documents it depends on.
Words, labels, `card_mark` and provenance fields (`basis`, `source_ids`, `to_be_set_by`) are excluded, so
a provenance edit never looks like a rule change. Both are sha256 of RFC 8785 canonical JSON.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from crew_health.kernel.commands import canonical_json
from crew_health.rules.domain.definitions import (
    AnswerCondition,
    InsightCondition,
    SensorLimitCondition,
    SensorQuietCondition,
    WarningDefinition,
    content_to_raw,
    definition_to_raw,
)

_LIMIT_FIELDS = ("comparator", "value", "unit", "level", "role", "status", "environments", "aggregation_id")
_CLEAR_FIELDS = ("comparator", "value", "dwell_seconds")
_PROVENANCE = ("basis", "source_ids", "to_be_set_by")


@dataclass(frozen=True, slots=True)
class ReleaseDocuments:
    """The `value` payloads of the documents a definition may depend on. Missing ones are empty."""

    sensor_limits: Mapping[str, object] = field(default_factory=dict)
    sensor_catalog: Mapping[str, object] = field(default_factory=dict)
    questionnaires: Mapping[str, object] = field(default_factory=dict)
    assistant_policy: Mapping[str, object] = field(default_factory=dict)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def content_hash(definition: WarningDefinition) -> str:
    return _digest(content_to_raw(definition.content))


def _entries(document: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    items = document.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _find(items: list[Mapping[str, object]], key: str, wanted: str) -> Mapping[str, object] | None:
    for item in items:
        if item.get(key) == wanted:
            return item
    return None


def _catalog_bounds(docs: ReleaseDocuments, sensor_type_id: str) -> dict[str, object]:
    entry = _find(_entries(docs.sensor_catalog, "sensor_types"), "id", sensor_type_id)
    if entry is None:
        return {"stale_after_seconds": None, "expected_interval_seconds": None}
    return {
        "stale_after_seconds": entry.get("stale_after_seconds"),
        "expected_interval_seconds": entry.get("expected_interval_seconds"),
    }


def _aggregation(
    docs: ReleaseDocuments, sensor_type_id: str, aggregation_id: object
) -> dict[str, object] | None:
    entry = _find(_entries(docs.sensor_catalog, "sensor_types"), "id", sensor_type_id)
    if entry is None:
        return None
    agg = _find(_entries(entry, "aggregations"), "id", str(aggregation_id))
    if agg is None:
        return None
    return {"kind": agg.get("kind"), "window_seconds": agg.get("window_seconds")}


def limit_dependencies(limit_id: str, docs: ReleaseDocuments) -> dict[str, object]:
    """Everything that decides raising and clearing of one sensor limit (E24 3.2 table row 1)."""
    entry = _find(_entries(docs.sensor_limits, "limits"), "limit_id", limit_id)
    if entry is None:
        return {"limit_id": limit_id, "present": False}
    clear = entry.get("clear")
    clear_part: dict[str, object] = {}
    if isinstance(clear, Mapping):
        clear_part = {k: clear.get(k) for k in _CLEAR_FIELDS}
    sensor_type_id = str(entry.get("sensor_type_id"))
    return {
        "limit_id": limit_id,
        "present": True,
        "limit": {k: entry.get(k) for k in _LIMIT_FIELDS},
        "min_coverage": entry.get("min_coverage"),
        "clear": clear_part,
        "aggregation": _aggregation(docs, sensor_type_id, entry.get("aggregation_id")),
        "catalog": _catalog_bounds(docs, sensor_type_id),
    }


def limit_fingerprint(limit_id: str, docs: ReleaseDocuments) -> str:
    """The pin of a limit: a result evaluated under another limit never clears this warning."""
    return _digest(limit_dependencies(limit_id, docs))


def _quiet_dependencies(c: SensorQuietCondition, docs: ReleaseDocuments) -> dict[str, object]:
    wanted = set(c.sensor_type_ids)
    out: dict[str, object] = {}
    for entry in _entries(docs.sensor_catalog, "sensor_types"):
        type_id = entry.get("id")
        if isinstance(type_id, str) and (not wanted or type_id in wanted):
            out[type_id] = entry.get("stale_after_seconds")
    return {"stale_after_seconds": out, "covers_all": not wanted}


def _answer_dependencies(c: AnswerCondition, docs: ReleaseDocuments) -> dict[str, object]:
    entry = _find(_entries(docs.questionnaires, "questions"), "id", c.question_id)
    if entry is None:
        return {"question_id": c.question_id, "present": False}
    choices = entry.get("choices")
    presence: list[str] = []
    if isinstance(choices, list):
        presence = sorted({str(ch.get("presence")) for ch in choices if isinstance(ch, Mapping)})
    return {"question_id": c.question_id, "present": True, "kind": entry.get("kind"), "presence": presence}


def _insight_dependencies(definition: WarningDefinition, docs: ReleaseDocuments) -> dict[str, object]:
    topics = []
    for topic in _entries(docs.assistant_policy, "insight_topics"):
        if topic.get("warning_definition_id") == definition.id:
            topics.append({"id": topic.get("id"), "evidence_codes": topic.get("evidence_codes")})
    topics.sort(key=lambda t: str(t["id"]))
    return {"allowed_warning_levels": docs.assistant_policy.get("allowed_warning_levels"), "topics": topics}


def _dependencies(definition: WarningDefinition, docs: ReleaseDocuments) -> dict[str, object]:
    c = definition.condition
    if isinstance(c, SensorLimitCondition):
        return limit_dependencies(c.limit_id, docs)
    if isinstance(c, SensorQuietCondition):
        return _quiet_dependencies(c, docs)
    if isinstance(c, AnswerCondition):
        return _answer_dependencies(c, docs)
    if isinstance(c, InsightCondition):
        return _insight_dependencies(definition, docs)
    return {}


def rule_basis(definition: WarningDefinition) -> dict[str, object]:
    """The definition without content and without provenance fields."""
    raw = definition_to_raw(definition)
    raw.pop("content")
    for container in (raw["clear"], raw["condition"]):
        if isinstance(container, dict):
            for key in _PROVENANCE:
                container.pop(key, None)
    condition = raw["condition"]
    if isinstance(condition, dict):
        within = condition.get("within_days_after_event")
        if isinstance(within, dict):
            within.pop("basis", None)
    return raw


def rule_fingerprint(definition: WarningDefinition, docs: ReleaseDocuments) -> str:
    return _digest({"definition": rule_basis(definition), "dependencies": _dependencies(definition, docs)})
