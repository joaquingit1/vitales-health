from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from crew_health.kernel.ids import IdFactory, require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash
from crew_health.knowledge.domain.extraction import Extraction


@dataclass(frozen=True, slots=True)
class PassageArtifact:
    passage_id: str
    extraction_id: str
    char_start: int
    char_end: int
    text_sha256: str
    text: str
    page_start: int
    page_end: int

    def __post_init__(self) -> None:
        require_id(self.passage_id)
        require_id(self.extraction_id)
        if any(type(v) is not int for v in (self.char_start, self.char_end, self.page_start, self.page_end)):
            raise EvidenceInvalid("span_coordinates")
        if not 0 <= self.char_start < self.char_end or len(self.text) != self.char_end - self.char_start:
            raise EvidenceInvalid("span")
        if sha256(self.text.encode()).hexdigest() != require_hash(self.text_sha256):
            raise EvidenceInvalid("span_sha256")
        if not 1 <= self.page_start <= self.page_end:
            raise EvidenceInvalid("span_page")

    @classmethod
    def create(
        cls,
        *,
        ids: IdFactory,
        extraction: Extraction,
        char_start: int,
        char_end: int,
        max_chars: int,
    ) -> PassageArtifact:
        if type(char_start) is not int or type(char_end) is not int:
            raise EvidenceInvalid("span")
        if not 0 <= char_start < char_end <= len(extraction.text):
            raise EvidenceInvalid("span")
        require_bound(char_end - char_start, max_chars)
        pages = tuple(p.page for p in extraction.pages if p.char_start < char_end and p.char_end > char_start)
        text = extraction.text[char_start:char_end]
        return cls(
            ids.derived("psg", extraction.extraction_id, str(char_start), str(char_end)),
            extraction.extraction_id,
            char_start,
            char_end,
            sha256(text.encode()).hexdigest(),
            text,
            pages[0],
            pages[-1],
        )
