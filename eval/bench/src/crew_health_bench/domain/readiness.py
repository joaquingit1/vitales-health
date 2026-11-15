"""Whether the product suites can run against an evaluation hub (design 4.5, 6.1 and the fallback table of 9).

Pure standard library on purpose: the hub's scenario suite imports it to check the real composition.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Hub operations the product-suite driver calls (design 6.1). Conversation and help cases add their own.
DRIVER_OPERATIONS = (
    "actOnMeasurementRequest",
    "answerEpisode",
    "createSession",
    "getAssessment",
    "getAssistantStatus",
    "getConfigIndex",
    "getContext",
    "getEpisode",
    "getEvents",
    "getEvidencePassage",
    "getJob",
    "getMeasurementRequest",
    "getObservation",
    "recordAttempt",
    "setAssistantRuntime",
    "startAssessment",
    "startEpisode",
)


@dataclass(frozen=True, slots=True)
class SuiteReadiness:
    ready: bool
    reason: str | None
    missing_operations: tuple[str, ...]


def product_suite_readiness(mounted_operations: Iterable[str], *, harness_available: bool) -> SuiteReadiness:
    """`not_run` with a named reason unless E21's harness exists and every driver operation is mounted."""
    mounted = frozenset(mounted_operations)
    missing = tuple(name for name in DRIVER_OPERATIONS if name not in mounted)
    if not harness_available:
        return SuiteReadiness(False, "harness_unavailable", missing)
    if missing:
        return SuiteReadiness(False, "provider_missing", missing)
    return SuiteReadiness(True, None, ())
