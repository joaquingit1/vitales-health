"""Bounds on assistant insights (E24 3.13, DE24-5, R40, V10, V65, D17).

A pure policy over the draft, the facts about the records it cites and the assistant policy. An empty
result means valid. Any violation stores nothing and E24 audits the codes only. The level check also
holds the hard invariant that an assistant never raises above watch, whatever the policy lists.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from crew_health.rules.domain.inputs import AssistantPolicy, EvidenceKey, InsightDraft, RecordFacts
from crew_health.rules.domain.vocabulary import ASSISTANT_LEVELS, OriginMode

type ViolationCode = Literal[
    "level_not_allowed",
    "topic_unknown",
    "evidence_empty",
    "evidence_kind",
    "evidence_not_found",
    "evidence_other_person",
    "evidence_replay",
    "evidence_not_accepted",
    "evidence_origin_mixed",
    "habitat_fact_text",
    "action_count",
]  # internal enum

ALLOWED_EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"observation", "answer", "daily_check", "routine_occurrence"}
)
# The accepted state of each kind of record, as E01 records report it in `RecordFacts.state`.
ACCEPTED_STATE: Final[Mapping[str, str | None]] = MappingProxyType(
    {"observation": "accepted", "daily_check": "submitted", "routine_occurrence": "done", "answer": None}
)
MAX_ACTIONS: Final[int] = 1


def _evidence_codes(draft: InsightDraft, facts: Mapping[EvidenceKey, RecordFacts]) -> set[ViolationCode]:
    found: set[ViolationCode] = set()
    if not draft.evidence:
        found.add("evidence_empty")
    origins: set[OriginMode] = set()
    for key in draft.evidence:
        if key.kind not in ALLOWED_EVIDENCE_KINDS:
            found.add("evidence_kind")
            continue
        record = facts.get(key)
        if record is None or not record.exists:
            found.add("evidence_not_found")
            continue
        if record.owner_person_id != draft.person_id:
            found.add("evidence_other_person")
        if record.is_sensor or record.origin_mode is OriginMode.REPLAY:
            found.add("evidence_replay")
        wanted = ACCEPTED_STATE[key.kind]
        if wanted is not None and record.state != wanted:
            found.add("evidence_not_accepted")
        if record.origin_mode is not None:
            origins.add(record.origin_mode)
    if origins - {draft.origin_mode}:
        found.add("evidence_origin_mixed")
    return found


def validate(
    draft: InsightDraft, facts: Mapping[EvidenceKey, RecordFacts], policy: AssistantPolicy
) -> tuple[ViolationCode, ...]:
    found = _evidence_codes(draft, facts)
    if draft.level not in policy.allowed_warning_levels or draft.level not in ASSISTANT_LEVELS:
        found.add("level_not_allowed")
    if policy.topic(draft.topic_id) is None:
        found.add("topic_unknown")
    if draft.habitat_fact_text_present:
        found.add("habitat_fact_text")
    if len(draft.routine_occurrence_ids) > MAX_ACTIONS:
        found.add("action_count")
    order: tuple[ViolationCode, ...] = (
        "level_not_allowed",
        "topic_unknown",
        "evidence_empty",
        "evidence_kind",
        "evidence_not_found",
        "evidence_other_person",
        "evidence_replay",
        "evidence_not_accepted",
        "evidence_origin_mixed",
        "habitat_fact_text",
        "action_count",
    )
    return tuple(code for code in order if code in found)
