from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from string import Formatter
from types import MappingProxyType

from crew_health.episodes.application.codec import array, obj, text
from crew_health.episodes.domain.due_policy import DuePolicy, duration_seconds
from crew_health.kernel.errors import ErrorCode, ProblemError


@dataclass(frozen=True, slots=True)
class EpisodeRules:
    version: str
    task_policies: Mapping[str, DuePolicy]
    templates: Mapping[str, str]
    notice: str
    late_seconds: Decimal
    selected_field_labels: Mapping[str, str]


def _seconds(raw: object) -> Decimal:
    item = obj(raw)
    value = item.get("value")
    if type(value) not in (int, float, str):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP2 duration")
    return duration_seconds(Decimal(str(value)), text(item.get("unit")))


def parse_rules(raw: bytes) -> EpisodeRules:
    if len(raw) > 1048576:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP1 document bound")
    value = obj(json.loads(raw))
    if set(value) != {
        "version",
        "example",
        "notice",
        "task_policies",
        "due_policies",
        "task_templates",
        "late_entry_after",
        "review_state",
        "written_by",
        "checked_by",
        "selected_field_labels",
    }:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP1 document fields")
    policies: dict[str, DuePolicy] = {}
    for item in array(value.get("due_policies")):
        p = obj(item)
        key, kind = text(p.get("policy_id")), text(p.get("kind"))
        if key in policies:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "EP1 duplicate policy")
        seconds = None
        if kind == "after":
            duration = obj(p.get("duration"))
            if duration.get("value") is None:
                if p.get("status") != "threshold_pending" or not p.get("to_be_set_by"):
                    raise ProblemError(ErrorCode.CONFIG_INVALID, "EP2 pending")
            else:
                seconds = _seconds(duration)
        elif kind not in ("none", "end_of_mission_day", "chosen_by_person"):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "EP2 kind")
        minimum = _seconds(p.get("min")) if kind == "chosen_by_person" else None
        maximum = _seconds(p.get("max")) if kind == "chosen_by_person" else None
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "EP2 bounds")
        if not p.get("basis") or not isinstance(p.get("source_ids"), list):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "EP2 provenance")
        policies[key] = DuePolicy(key, kind, seconds, minimum, maximum)
    assignment = obj(value.get("task_policies"))
    if (
        set(assignment) != {"review", "review_item", "custom"}
        or any(v not in policies for v in assignment.values() if isinstance(v, str))
        or any(not isinstance(v, str) for v in assignment.values())
    ):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP1 task_policies")
    templates = obj(value.get("task_templates"))
    if set(templates) != {"review_assessment", "review_correction", "review_requested"}:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP3 template keys")
    result: dict[str, str] = {}
    for key, template in templates.items():
        t = text(template)
        if not t.strip():
            raise ProblemError(ErrorCode.CONFIG_INVALID, "EP3 empty template")
        for _, field, spec, conversion in Formatter().parse(t):
            if field is not None and (field not in ("time", "day_label") or spec or conversion):
                raise ProblemError(ErrorCode.CONFIG_INVALID, "EP3 placeholder")
        result[key] = t
    if value.get("review_state") == "reviewed" and value.get("checked_by") is None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP5 checked_by")
    labels = obj(value.get("selected_field_labels"))
    if set(labels) != {"concern_text", "onset", "answer_refs"} or any(
        not isinstance(v, str) or not v.strip() for v in labels.values()
    ):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "EP6 selected field labels")
    late = _seconds(value.get("late_entry_after"))
    return EpisodeRules(
        hashlib.sha256(raw).hexdigest(),
        MappingProxyType({key: policies[text(ref)] for key, ref in assignment.items()}),
        MappingProxyType(result),
        text(value.get("notice")),
        late,
        MappingProxyType({key: text(label) for key, label in labels.items()}),
    )


class EpisodeRulesSource:
    """Pinned by immutable content hash; callers inject loaded releases, never an unvalidated default."""

    def __init__(self, rules: EpisodeRules, release_id: str) -> None:
        self.active_release_id = release_id
        self._releases = {release_id: rules}

    def rules(self, release_id: str) -> EpisodeRules:
        result = self._releases.get(release_id)
        if result is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "episode rules pin missing", retryable=False)
        return result

    def activate(self, release_id: str, raw: bytes) -> None:
        candidate = parse_rules(raw)
        if release_id in self._releases and self._releases[release_id].version != candidate.version:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "immutable rules release")
        if release_id not in self._releases and len(self._releases) >= 2:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "rules cache full: release remains pinned")
        self._releases[release_id] = candidate
        self.active_release_id = release_id

    def render(self, release_id: str, key: str, at: datetime, day_label: str) -> str:
        return self.rules(release_id).templates[key].format(time=at.strftime("%H:%M"), day_label=day_label)
