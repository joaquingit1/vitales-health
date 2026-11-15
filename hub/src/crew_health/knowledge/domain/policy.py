from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash
from crew_health.knowledge.domain.segmentation import SegmentationPolicy

type AllowedUse = Literal["risk_context", "patient_instruction", "routine_basis", "limit_basis"]


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    purposes: tuple[PurposeRule, ...]
    segmentation: SegmentationPolicy
    tracking_parameters: tuple[str, ...]
    source_hierarchy: tuple[str, ...]
    sha256: str

    def __post_init__(self) -> None:
        require_hash(self.sha256)
        uses = tuple(rule.use for rule in self.purposes)
        if len(uses) != 4 or len(set(uses)) != 4:
            raise EvidenceInvalid("purpose_inventory")
        if len(set(self.source_hierarchy)) != len(self.source_hierarchy):
            raise EvidenceInvalid("source_hierarchy")
        if len(set(self.tracking_parameters)) != len(self.tracking_parameters):
            raise EvidenceInvalid("tracking_parameters")
        if any(kind not in self.source_hierarchy for rule in self.purposes for kind in rule.source_kinds):
            raise EvidenceInvalid("source_hierarchy")


@dataclass(frozen=True, slots=True)
class PurposeRule:
    use: AllowedUse
    source_kinds: tuple[str, ...]
    completeness: tuple[str, ...]
    partial_acknowledgment_required: bool

    def __post_init__(self) -> None:
        if self.use not in ("risk_context", "patient_instruction", "routine_basis", "limit_basis"):
            raise EvidenceInvalid("allowed_use")
        if not self.source_kinds or not self.completeness or "unclassified" in self.source_kinds:
            raise EvidenceInvalid("purpose_rule")
        if len(set(self.source_kinds)) != len(self.source_kinds) or len(set(self.completeness)) != len(
            self.completeness
        ):
            raise EvidenceInvalid("purpose_rule")
        if any(c not in ("full", "partial") for c in self.completeness):
            raise EvidenceInvalid("purpose_completeness")
        if "partial" in self.completeness and (
            self.use != "risk_context" or not self.partial_acknowledgment_required
        ):
            raise EvidenceInvalid("partial_permission")
        if self.use == "patient_instruction" and any(
            kind not in ("clinical_guideline", "first_aid_guidance", "device_manual")
            for kind in self.source_kinds
        ):
            raise EvidenceInvalid("patient_instruction_kind")


def source_eligible(
    rule: PurposeRule,
    *,
    use: str,
    source_kind: str,
    completeness: str,
    partial_acknowledged: bool,
    technical_verdict: str,
    permits_text: bool,
) -> bool:
    if technical_verdict != "pass" or permits_text is not True or use != rule.use:
        return False
    if source_kind not in rule.source_kinds or completeness not in rule.completeness:
        return False
    if completeness == "partial":
        return rule.use == "risk_context" and partial_acknowledged is True
    return completeness == "full"
