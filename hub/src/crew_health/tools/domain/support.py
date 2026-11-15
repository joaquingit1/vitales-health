from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from crew_health.kernel.clock import NormalizedTime, TimeInput, require_utc
from crew_health.kernel.ids import require_id
from crew_health.kernel.units import Quantity
from crew_health.tools.domain.errors import ToolsInvalid


@dataclass(frozen=True, slots=True)
class SupportItem:
    ref_id: str
    kind: Literal[
        "observation", "answer", "daily_check", "routine_occurrence", "attempt", "reading", "passage"
    ]
    person_id: str | None
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    quality: str
    freshness: Literal["current", "stale", "unknown", "future"]
    acquisition_method: str | None
    observed_at: datetime | None
    corrected_at: datetime | None
    quantity: Quantity | None
    presence: Literal["present", "absent", "unknown", "declined"] | None
    release_id: str | None
    content_sha256: str | None
    exclusion_reason: str | None
    code: str | None = None
    question_id: str | None = None
    questionnaire_version: str | None = None
    question_text: str | None = None
    value: Quantity | str | bool | None = None
    source_revision: int | None = None
    observed: TimeInput | None = None
    normalized: NormalizedTime | None = None
    freshness_policy_ref: str | None = None
    supersedes: str | None = None
    conflicting_refs: tuple[str, ...] = ()
    red_flag_ids: tuple[str, ...] = ()
    required: bool = False

    def __post_init__(self) -> None:
        for value in (self.ref_id, self.person_id, self.release_id):
            if value is not None:
                require_id(value)
        for at in (self.observed_at, self.corrected_at):
            if at is not None:
                require_utc(at)
        if (
            self.kind
            not in {
                "observation",
                "answer",
                "daily_check",
                "routine_occurrence",
                "attempt",
                "reading",
                "passage",
            }
            or self.origin_mode not in {"LIVE", "REPLAY", "SIM"}
            or self.freshness not in {"current", "stale", "unknown", "future"}
            or self.presence not in {None, "present", "absent", "unknown", "declined"}
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")

    def for_context(self, *, person_id: str, origin_mode: str, at: datetime) -> SupportItem:
        if self.kind in {"reading", "passage"}:
            if self.person_id is not None:
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        elif self.person_id != person_id or self.origin_mode != origin_mode:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if self.corrected_at is not None and self.corrected_at > at:
            return replace(self, freshness="future", quantity=None, value=None)
        if self.kind not in {"reading", "passage"} and not self._lineage_known():
            return replace(self, freshness="unknown", quantity=None, value=None)
        if self.normalized is not None and self.normalized.corrected_at is not None:
            uncertainty = self.normalized.uncertainty_ms
            if uncertainty is None or uncertainty < 0:
                return replace(self, freshness="unknown", quantity=None, value=None)
            if self.normalized.corrected_at + timedelta(milliseconds=uncertainty) > at:
                return replace(self, freshness="future", quantity=None, value=None)
        if (
            self.quality != "accepted"
            or self.freshness != "current"
            or self.kind == "attempt"
            or self.observed_at is None
            or self.corrected_at is None
            or self.presence in {"unknown", "declined"}
        ):
            return replace(self, quantity=None, value=None)
        return self

    def _lineage_known(self) -> bool:
        if self.observed is None or self.normalized is None or not self.freshness_policy_ref:
            return False
        if type(self.source_revision) is not int or self.source_revision < 1:
            return False
        if self.observed.original_at != self.observed_at or self.normalized.corrected_at != self.corrected_at:
            return False
        if (
            self.normalized.state not in {"known", "replay"}
            or self.normalized.original_at != self.observed.original_at
            or self.observed.origin_mode != self.origin_mode
            or not self.observed.source_clock_id
            or not self.observed.segment_id
            or self.observed.source_clock_version < 1
            or self.normalized.correction_id is None
            or self.normalized.uncertainty_ms is None
            or self.normalized.uncertainty_ms < 0
        ):
            return False
        if self.kind == "observation" and not self.code:
            return False
        return self.kind != "answer" or bool(
            self.question_id and self.questionnaire_version and self.question_text
        )

    def permitted_uses(self, *, mission_origin: str) -> frozenset[str]:
        if not self._lineage_known():
            return frozenset()
        if self.exclusion_reason is not None or self.observed_at is None or self.corrected_at is None:
            return frozenset()
        if self.kind in {"answer", "daily_check", "routine_occurrence"}:
            if (
                self.quality == "accepted"
                and self.freshness == "current"
                and self.origin_mode in {"LIVE", "SIM"}
                and self.origin_mode == mission_origin
                and self.presence in {"present", "absent"}
            ):
                return frozenset({"finding", "hypothesis", "gap_closure"})
            return frozenset()
        if self.quantity is None:
            return frozenset()
        return support_uses(
            quality=self.quality,
            origin=self.origin_mode,
            mission_origin=mission_origin,
            freshness=self.freshness,
            method=self.acquisition_method or "",
            kind=self.kind,
            authorized=True,
        )


def support_uses(
    *,
    quality: str,
    origin: str,
    mission_origin: str,
    freshness: str,
    method: str,
    kind: str,
    authorized: bool,
) -> frozenset[str]:
    if (
        quality != "accepted"
        or origin not in {"LIVE", "SIM"}
        or origin != mission_origin
        or freshness != "current"
        or not authorized
        or kind != "observation"
    ):
        return frozenset()
    if method == "crewmate_palpation":
        return frozenset({"finding"})
    if method not in {
        "self_report",
        "manual_instrument",
        "device",
        "camera_estimate",
        "imported_report",
        "clinician_entry",
        "derived",
    }:
        return frozenset()
    return frozenset({"finding", "hypothesis", "gap_closure"})
