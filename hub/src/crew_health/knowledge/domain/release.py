from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash
from crew_health.knowledge.domain.extraction import Extraction
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.policy import AllowedUse
from crew_health.knowledge.domain.registry import require_source_id


@dataclass(frozen=True, slots=True)
class UseStatus:
    use: AllowedUse
    status: Literal["approved", "superseded", "withdrawn"]
    approval_ref: str
    successor_ref: str | None

    def __post_init__(self) -> None:
        if self.use not in ("risk_context", "patient_instruction", "routine_basis", "limit_basis"):
            raise EvidenceInvalid("allowed_use")
        if self.status not in ("approved", "superseded", "withdrawn"):
            raise EvidenceInvalid("use_status")
        require_id(self.approval_ref)
        if self.successor_ref is not None:
            require_id(self.successor_ref)


@dataclass(frozen=True, slots=True)
class ApplicabilityFilter:
    environments: frozenset[str] | None
    allow_unknown: bool

    def admits(self, environments: frozenset[str] | None) -> bool:
        if environments is None:
            return self.allow_unknown is True
        return self.environments is None or bool(self.environments & environments)


@dataclass(frozen=True, slots=True)
class ReleaseMembership:
    release_id: str
    passage: PassageArtifact
    extraction: Extraction
    source_ids: tuple[str, ...]
    license: LicenseRecord
    uses: tuple[UseStatus, ...]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
    environments: frozenset[str] | None
    section: str
    withdrawn: bool
    simulation_only: bool

    def __post_init__(self) -> None:
        require_id(self.release_id)
        if type(self.withdrawn) is not bool or type(self.simulation_only) is not bool:
            raise EvidenceInvalid("membership_origin")
        if not self.source_ids or len(set(self.source_ids)) != len(self.source_ids):
            raise EvidenceInvalid("source_aliases")
        for source_id in self.source_ids:
            require_source_id(source_id)
        if not self.license.permits_text:
            raise EvidenceInvalid("text_license")
        if not self.uses or len({row.use for row in self.uses}) != len(self.uses):
            raise EvidenceInvalid("duplicate_purpose")
        if self.clinical_review not in ("clinically_reviewed", "not_clinically_reviewed"):
            raise EvidenceInvalid("clinical_review")
        passage, extraction = self.passage, self.extraction
        pages = tuple(
            page.page
            for page in extraction.pages
            if page.char_start < passage.char_end and page.char_end > passage.char_start
        )
        if (
            passage.extraction_id != extraction.extraction_id
            or extraction.text[passage.char_start : passage.char_end] != passage.text
            or not pages
            or (passage.page_start, passage.page_end) != (pages[0], pages[-1])
            or not self.section
        ):
            raise EvidenceInvalid("membership_span")

    def use_status(self, use: str) -> UseStatus | None:
        return next((row for row in self.uses if row.use == use), None)


@dataclass(frozen=True, slots=True)
class PathwayCard:
    condition_id: str
    indicative_assessment: tuple[tuple[str, str | None], ...]
    workup_source_ids: tuple[str, ...]
    supporting_passage_ids: tuple[str, ...]
    content_sha256: str
    authored_by: str
    authored: bool
    simulation_only: bool
    environments: frozenset[str] | None

    def __post_init__(self) -> None:
        if re.fullmatch(r"ICL[1-9][0-9]{0,2}[A-Z]?", self.condition_id) is None:
            raise EvidenceInvalid("condition")
        require_id(self.authored_by)
        require_hash(self.content_sha256)
        if type(self.authored) is not bool or type(self.simulation_only) is not bool:
            raise EvidenceInvalid("card_origin")
        for source_id in self.workup_source_ids:
            require_source_id(source_id)
        for passage_id in self.supporting_passage_ids:
            require_id(passage_id)
        if self.content_sha256 != self.body_hash():
            raise EvidenceInvalid("card_hash")
        if self.authored and (not self.workup_source_ids or not self.supporting_passage_ids):
            raise EvidenceInvalid("card_support")

    def body_hash(self) -> str:
        return sha256(
            canonical_json(
                {
                    "condition_id": self.condition_id,
                    "indicative_assessment": self.indicative_assessment,
                    "workup_source_ids": self.workup_source_ids,
                    "supporting_passage_ids": self.supporting_passage_ids,
                }
            )
        ).hexdigest()
