"""CONTRACTS 11 checks 5 and 13 and the E24 loader checks (R-E24-10) as a `ReleaseCheck` (E24 8).

It only rejects: a finding makes the candidate invalid and the last good release stays active. A document the check
needs but cannot read is a finding, never a pass.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from crew_health.kernel.config import CandidateRelease, ConfigName, Finding
from crew_health.rules.domain.loader_checks import check_document

CHECK_ID = "rules.warning_rules"


def _load(candidate: CandidateRelease, name: ConfigName) -> Mapping[str, object] | None:
    view = candidate.documents.get(name)
    if view is None:
        return None
    try:
        loaded = json.loads(view.raw)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


@dataclass(frozen=True, slots=True)
class WarningRulesCheck:
    max_definitions: int

    @property
    def check_id(self) -> str:
        return CHECK_ID

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        rules = _load(candidate, ConfigName.WARNING_RULES)
        if rules is None:
            return (
                Finding(CHECK_ID, ConfigName.WARNING_RULES, "", "warning-rules is missing or not an object"),
            )
        limits_doc = _load(candidate, ConfigName.SENSOR_LIMITS)
        hazards_doc = _load(candidate, ConfigName.ENVIRONMENT_HAZARDS)
        policy = _load(candidate, ConfigName.ASSISTANT_POLICY)
        if limits_doc is None or hazards_doc is None or policy is None:
            return (Finding(CHECK_ID, ConfigName.WARNING_RULES, "", "a referenced document is missing"),)
        limits = {str(e["limit_id"]): e for e in _entries(limits_doc, "limits") if "limit_id" in e}
        hazards = {
            str(h["id"]): {str(k): str(v) for k, v in h["what_to_do"].items()}  # type: ignore[attr-defined]
            for h in _entries(hazards_doc, "hazards")
            if isinstance(h.get("what_to_do"), dict)
        }
        found = check_document(
            rules,
            max_definitions=self.max_definitions,
            limits=limits,
            what_to_do=hazards,
            topics=_entries(policy, "insight_topics"),
            prohibited_phrases=None,
        )
        return tuple(replace(f, document=ConfigName.WARNING_RULES) for f in found)


def _entries(doc: Mapping[str, object], key: str) -> Sequence[Mapping[str, object]]:
    items = doc.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
