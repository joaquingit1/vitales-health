"""The facts of one configuration release that decide raising, clearing and rendering (E24 3.2, 8).

Built once per release id from the release's documents and cached for that one release only (3.8). A document that is
missing or invalid fails the build with a typed error, so the evaluation becomes pending (5.5); nothing is guessed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.config import ConfigName, ReleaseHandle
from crew_health.rules.domain.definitions import RulesDocument, WarningDefinition, parse_rules_document
from crew_health.rules.domain.errors import DefinitionInvalid
from crew_health.rules.domain.fingerprint import ReleaseDocuments, content_hash, limit_fingerprint, rule_fingerprint
from crew_health.rules.domain.inputs import AssistantPolicy, InsightTopic
from crew_health.rules.domain.views import LimitFacts
from crew_health.rules.domain.vocabulary import Basis, Level


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    release_id: str
    rules: RulesDocument
    docs: ReleaseDocuments
    definitions: Mapping[str, WarningDefinition]
    fingerprints: Mapping[str, str]
    content_hashes: Mapping[str, str]
    limits: Mapping[str, LimitFacts]
    what_to_do: Mapping[str, Mapping[str, str]]
    origin_words: Mapping[str, str]
    designed_states: Mapping[str, str]
    suppressed_overlays: frozenset[str]
    policy: AssistantPolicy


def _doc(release: ReleaseHandle, name: ConfigName, *, required: bool = True) -> dict[str, object]:
    try:
        raw = release.document(name).raw
    except Exception as error:  # noqa: BLE001 - a missing document is a typed finding at this boundary
        if required:
            raise DefinitionInvalid("document unavailable: " + name.value) from error
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise DefinitionInvalid("document is not an object: " + name.value)
    return loaded


def _entries(doc: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    items = doc.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _limits(docs: ReleaseDocuments) -> dict[str, LimitFacts]:
    stale = {str(t.get("id")): int(str(t.get("stale_after_seconds"))) for t in _entries(docs.sensor_catalog, "sensor_types")}
    found: dict[str, LimitFacts] = {}
    for e in _entries(docs.sensor_limits, "limits"):
        clear = e.get("clear")
        if not isinstance(clear, dict):
            raise DefinitionInvalid("limit without a clear rule: " + str(e.get("limit_id")))
        limit_id, sensor = str(e["limit_id"]), str(e["sensor_type_id"])
        value = e.get("value")
        found[limit_id] = LimitFacts(
            limit_id=limit_id,
            sensor_type_id=sensor,
            level=Level(str(e["level"])),
            role="warning" if e.get("role") == "warning" else "display_only",
            status="active" if e.get("status") == "active" else "threshold_pending",
            value=None if value is None else Decimal(str(value)),
            dwell_seconds=int(clear["dwell_seconds"]),
            stale_after_seconds=stale[sensor],
            limit_fingerprint=limit_fingerprint(limit_id, docs),
            basis=Basis(str(e["basis"])) if e.get("basis") else None,
            clear_basis=Basis(str(clear["basis"])) if clear.get("basis") else None,
        )
    return found


def _policy(doc: Mapping[str, object]) -> AssistantPolicy:
    levels = doc.get("allowed_warning_levels")
    topics = tuple(
        InsightTopic(str(t["id"]), None if t.get("warning_definition_id") is None else str(t["warning_definition_id"]),
                     None if t.get("body_region") is None else str(t["body_region"]))
        for t in _entries(doc, "insight_topics")
    )  # fmt: skip
    return AssistantPolicy(frozenset(Level(str(x)) for x in levels) if isinstance(levels, list) else frozenset(), topics)


def build(release: ReleaseHandle) -> ReleaseFacts:
    rules = parse_rules_document(_doc(release, ConfigName.WARNING_RULES))
    docs = ReleaseDocuments(
        sensor_limits=_doc(release, ConfigName.SENSOR_LIMITS, required=False),
        sensor_catalog=_doc(release, ConfigName.SENSOR_CATALOG, required=False),
        questionnaires=_doc(release, ConfigName.QUESTIONNAIRES, required=False),
        assistant_policy=_doc(release, ConfigName.ASSISTANT_POLICY, required=False),
    )
    hazards = {
        str(h["id"]): {str(k): str(v) for k, v in dict(h["what_to_do"]).items()}  # type: ignore[call-overload]
        for h in _entries(_doc(release, ConfigName.ENVIRONMENT_HAZARDS, required=False), "hazards")
        if isinstance(h.get("what_to_do"), dict)
    }
    copy = _doc(release, ConfigName.UI_COPY, required=False)
    words = copy.get("origin_words")
    states = copy.get("designed_states")
    suppressed = _doc(release, ConfigName.ROUTINE_RULES, required=False).get("suppressed_overlays")
    return ReleaseFacts(
        release_id=release.release_id,
        rules=rules,
        docs=docs,
        definitions={d.id: d for d in rules.definitions},
        fingerprints={d.id: rule_fingerprint(d, docs) for d in rules.definitions},
        content_hashes={d.id: content_hash(d) for d in rules.definitions},
        limits=_limits(docs),
        what_to_do=hazards,
        origin_words={str(k): str(v) for k, v in words.items()} if isinstance(words, dict) else {},
        designed_states={str(k): str(v) for k, v in states.items()} if isinstance(states, dict) else {},
        suppressed_overlays=frozenset(str(s) for s in suppressed) if isinstance(suppressed, list) else frozenset(),
        policy=_policy(docs.assistant_policy),
    )
