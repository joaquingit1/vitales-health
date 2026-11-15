from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import ValidationError

from crew_health.assistant.application.ports import CopyDocument as CopyDocument
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.config_assistant_policy_schema import AssistantPolicyJson
from crew_health.kernel.errors import ProblemError


@dataclass(frozen=True, slots=True)
class Policy:
    notice: str
    allowed_warning_levels: frozenset[str]
    max_iterations: int
    max_schema_repairs: int
    turn_timeout_seconds: int
    first_token_timeout_seconds: int
    tool_timeout_seconds: int
    insight_topics: tuple[Mapping[str, object], ...]

    @classmethod
    def parse(cls, raw: bytes, *, max_bytes: int, max_iterations: int, max_timeout_seconds: int) -> Policy:
        if len(raw) > max_bytes:
            raise _invalid()
        try:
            value = AssistantPolicyJson.model_validate_json(raw, strict=True)
        except ValidationError:
            raise _invalid() from None
        if (
            value.max_iterations > max_iterations
            or value.turn_timeout_seconds > max_timeout_seconds
            or value.first_token_timeout_seconds > value.turn_timeout_seconds
            or value.tool_timeout_seconds > value.turn_timeout_seconds
        ):
            raise _invalid()
        return cls(
            value.notice,
            frozenset(value.allowed_warning_levels),
            value.max_iterations,
            value.max_schema_repairs,
            value.turn_timeout_seconds,
            value.first_token_timeout_seconds,
            value.tool_timeout_seconds,
            tuple(topic.model_dump(mode="json") for topic in value.insight_topics),
        )


@dataclass(frozen=True, slots=True)
class Copy:
    notice: Mapping[str, str]
    labels: Mapping[str, str]
    states: Mapping[str, str]

    @classmethod
    def parse(cls, document: CopyDocument, *, release_id: str, max_bytes: int) -> Copy:
        if (
            document.config_release_id != release_id
            or len(document.raw) > max_bytes
            or hashlib.sha256(document.raw).hexdigest() != document.sha256
        ):
            raise _invalid()
        try:
            value = json.loads(document.raw, object_pairs_hook=_unique)
        except ValueError, UnicodeError, RecursionError:
            raise _invalid() from None
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "provenance", "notice", "labels", "states"}
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise _invalid()
        return cls(*(_words(value[name], keys) for name, keys in _COPY_KEYS.items()))


_COPY_KEYS = {
    "notice": frozenset(
        {
            "action_unavailable",
            "guidance_unavailable",
            "assessment_link_unavailable",
            "operator_only",
            "cancelled",
            "stale",
            "timed_out",
            "rejected",
            "unavailable",
            "assessment_started",
            "assessment_incomplete",
            "no_supported_passage",
            "repeat_request",
            "step_not_in_plan",
        }
    ),
    "labels": frozenset(
        {
            "conversation",
            "person",
            "notice",
            "assessment",
            "snapshot",
            "config_release",
            "evidence_release",
            "created_at",
            "capability",
            "body_region",
            "model_id",
            "revision",
            "sha256",
            "prompt_version",
            "tool_catalog_version",
            "mode",
            "policy_version",
            "habitat_facts",
            "origin",
            "answered",
            "reload",
            "message",
            "progress",
            "missing_value",
            "assistant",
            "send",
            "cancel",
            "retry",
            "why",
            "dismiss",
            "start",
            "stop",
            "draft",
            "stale",
            "reviewed",
            "incomplete",
            "not_clinically_reviewed",
            "help",
            "evidence",
            "limitations",
            "findings",
            "hypotheses",
            "contradictions",
            "missing_evidence",
            "next_step",
            "reason",
            "decision_affected",
            "queue_depth",
            "model_release",
            "load_more",
        }
    ),
    "states": frozenset(
        {
            "queued",
            "streaming",
            "complete",
            "cancelled",
            "timed_out",
            "rejected",
            "failed",
            "idle",
            "closed",
            "active",
            "dismissed",
            "expired",
            "succeeded",
            "stopped",
            "loading",
            "running",
            "unavailable",
            "waiting",
            "offline",
            "empty",
            "error",
            "stale",
            "withheld",
        }
    ),
}


def _invalid() -> ProblemError:
    return ProblemError(ErrorCode.CONFIG_INVALID, "assistant configuration invalid")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = item
    return result


def _words(value: object, keys: frozenset[str]) -> Mapping[str, str]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalid()
    words: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item.strip() or len(item) > 4000:
            raise _invalid()
        words[key] = item
    return MappingProxyType(words)
