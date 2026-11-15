from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash


@dataclass(frozen=True, slots=True)
class FormPage:
    page: int
    text: str


@dataclass(frozen=True, slots=True)
class PageRange:
    page: int
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.page, self.char_start, self.char_end)):
            raise EvidenceInvalid("page_map")
        if self.page < 1 or self.char_start < 0 or self.char_end <= self.char_start:
            raise EvidenceInvalid("page_map")


def page_map_hash(pages: tuple[PageRange, ...]) -> str:
    return sha256(canonical_json([[p.page, p.char_start, p.char_end] for p in pages])).hexdigest()


@dataclass(frozen=True, slots=True)
class Extraction:
    extraction_id: str
    document_version_id: str
    format_version: str
    text: str
    text_sha256: str
    pages: tuple[PageRange, ...]
    page_map_sha256: str
    extracted_by: str

    def __post_init__(self) -> None:
        for value in (self.extraction_id, self.document_version_id, self.extracted_by):
            require_id(value)
        if not self.format_version or not self.text or not self.pages:
            raise EvidenceInvalid("extraction")
        if sha256(self.text.encode()).hexdigest() != require_hash(self.text_sha256):
            raise EvidenceInvalid("text_sha256")
        if page_map_hash(self.pages) != require_hash(self.page_map_sha256):
            raise EvidenceInvalid("page_map_sha256")
        end, page = 0, 0
        for item in self.pages:
            if item.char_start != end or item.page <= page or item.char_end > len(self.text):
                raise EvidenceInvalid("page_map")
            end, page = item.char_end, item.page
        if end != len(self.text):
            raise EvidenceInvalid("page_map")

    @classmethod
    def create(
        cls,
        *,
        ids: IdFactory,
        document_version_id: str,
        format_version: str,
        text: str,
        pages: tuple[PageRange, ...],
        extracted_by: str,
        max_chars: int,
        max_pages: int,
    ) -> Extraction:
        require_bound(len(text), max_chars)
        require_bound(len(pages), max_pages)
        text_hash, map_hash = sha256(text.encode()).hexdigest(), page_map_hash(pages)
        identifier = ids.derived("ex", document_version_id, format_version, text_hash, map_hash)
        return cls(
            identifier, document_version_id, format_version, text, text_hash, pages, map_hash, extracted_by
        )

    def page_texts(self) -> tuple[FormPage, ...]:
        return tuple(FormPage(p.page, self.text[p.char_start : p.char_end]) for p in self.pages)
