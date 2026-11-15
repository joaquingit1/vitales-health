from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_id: str
    url: str
    normalized_url: str
    title: str
    revision: int
    source_ids: tuple[str, ...]
    published_date_text: str | None = None


@dataclass(frozen=True, slots=True)
class LicenseRecord:
    license_id: str
    document_version_id: str
    content_sha256: str
    kind: Literal["nasa_public_domain", "permission_documented", "metadata_only"]
    basis: str
    actor_id: str
    at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    method: str
    complete: bool
    truncated: bool
    byte_count: int
    media_type: str


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    document_version_id: str
    item_id: str
    content_sha256: str
    artifact: ArtifactRef
    facts: CaptureFacts
    captured_by: str
    retrieved_at: datetime
    completeness: str | None
    stage: str
    revision: int
    license: LicenseRecord | None = None


@dataclass(frozen=True, slots=True)
class PageMap:
    page: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Extraction:
    extraction_id: str
    document_version_id: str
    text: str
    text_sha256: str
    page_map: tuple[PageMap, ...]
    page_map_sha256: str
    format_version: str
    extractor: str
    extractor_version: str
    at: datetime


@dataclass(frozen=True, slots=True)
class TextBlock:
    extraction_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class FormPage:
    page: int
    text: str
