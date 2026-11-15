from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash
from crew_health.knowledge.domain.registry import require_source_id


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_id: str
    normalized_url: str
    original_urls: tuple[str, ...]
    titles: tuple[str, ...]
    source_ids: tuple[str, ...]
    revision: int

    def __post_init__(self) -> None:
        require_id(self.item_id)
        if not self.normalized_url or not self.original_urls or not self.titles:
            raise EvidenceInvalid("catalog_item")
        if any(not title.strip() for title in self.titles) or any(not url for url in self.original_urls):
            raise EvidenceInvalid("catalog_item")
        if type(self.revision) is not int or self.revision < 1:
            raise EvidenceInvalid("revision")
        for source_id in self.source_ids:
            require_source_id(source_id)

    @classmethod
    def create(
        cls, *, ids: IdFactory, normalized_url: str, original_url: str, title: str, source_id: str
    ) -> CatalogItem:
        return cls(
            ids.derived("cat", normalized_url), normalized_url, (original_url,), (title,), (source_id,), 1
        )

    def register_source(
        self, source_id: str, *, title: str, original_url: str, expected_revision: int
    ) -> CatalogItem:
        if expected_revision != self.revision:
            raise EvidenceInvalid("stale_revision")
        require_source_id(source_id)
        sources = tuple(sorted(set((*self.source_ids, source_id))))
        titles = tuple(sorted(set((*self.titles, title))))
        urls = tuple(sorted(set((*self.original_urls, original_url))))
        if (sources, titles, urls) == (self.source_ids, self.titles, self.original_urls):
            return self
        return replace(
            self, source_ids=sources, titles=titles, original_urls=urls, revision=self.revision + 1
        )


type Completeness = Literal["full", "partial", "summary_only", "inaccessible"]


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    method: str
    media_type: str
    complete_transfer: bool
    truncated: bool
    page_count: int | None

    def __post_init__(self) -> None:
        if self.page_count is not None and (type(self.page_count) is not int or self.page_count < 1):
            raise EvidenceInvalid("page_count")
        if self.truncated and self.complete_transfer:
            raise EvidenceInvalid("transfer")


def assess_ceiling(facts: CaptureFacts) -> Completeness:
    if facts.method == "exa_contents":
        return "partial"
    if facts.method not in ("http_fetch", "file_import", "audit_pdf", "package_saved_text"):
        raise EvidenceInvalid("method")
    if facts.media_type not in ("application/pdf", "text/plain", "text/html"):
        raise EvidenceInvalid("media_type")
    if facts.truncated:
        return "partial"
    if not facts.complete_transfer or (facts.media_type == "application/pdf" and facts.page_count is None):
        return "inaccessible"
    return "full"


@dataclass(frozen=True, slots=True)
class CaptureAssessment:
    document_version_id: str
    content_sha256: str
    completeness: Completeness
    facts: CaptureFacts
    assessment_id: str
    assessed_by: str
    at: datetime

    def __post_init__(self) -> None:
        require_id(self.document_version_id)
        require_id(self.assessment_id)
        require_id(self.assessed_by)
        require_utc(self.at)
        require_hash(self.content_sha256)
        order = ("inaccessible", "summary_only", "partial", "full")
        if self.completeness not in order or order.index(self.completeness) > order.index(
            assess_ceiling(self.facts)
        ):
            raise EvidenceInvalid("completeness")

    def require_extractable(self, content_sha256: str) -> None:
        if self.content_sha256 != content_sha256 or self.completeness not in ("full", "partial"):
            raise EvidenceInvalid("capture_not_extractable")


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    document_version_id: str
    item_id: str
    content_sha256: str
    facts: CaptureFacts
    retrieved_at: datetime
    captured_by: str
    revision: int
    assessment: CaptureAssessment | None
    withdrawn: bool

    def __post_init__(self) -> None:
        for value in (self.document_version_id, self.item_id, self.captured_by):
            require_id(value)
        require_hash(self.content_sha256)
        require_utc(self.retrieved_at)
        if type(self.revision) is not int or self.revision < 1 or type(self.withdrawn) is not bool:
            raise EvidenceInvalid("revision")
        if self.assessment is not None:
            self._assessment_target(self.assessment)

    @classmethod
    def create(
        cls,
        *,
        ids: IdFactory,
        item_id: str,
        content_sha256: str,
        facts: CaptureFacts,
        retrieved_at: datetime,
        captured_by: str,
    ) -> DocumentVersion:
        assess_ceiling(facts)
        return cls(
            ids.derived("dv", item_id, content_sha256),
            item_id,
            content_sha256,
            facts,
            retrieved_at,
            captured_by,
            1,
            None,
            False,
        )

    def _assessment_target(self, assessment: CaptureAssessment) -> None:
        if (assessment.document_version_id, assessment.content_sha256, assessment.facts) != (
            self.document_version_id,
            self.content_sha256,
            self.facts,
        ):
            raise EvidenceInvalid("assessment_target")

    def assess(self, assessment: CaptureAssessment, *, expected_revision: int) -> DocumentVersion:
        if expected_revision != self.revision:
            raise EvidenceInvalid("stale_revision")
        self._assessment_target(assessment)
        return replace(self, assessment=assessment, revision=self.revision + 1)

    def require_extractable(self) -> None:
        if self.assessment is None:
            raise EvidenceInvalid("capture_not_assessed")
        if self.withdrawn:
            raise EvidenceInvalid("capture_not_extractable")
        self.assessment.require_extractable(self.content_sha256)
