from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from crew_health.kernel.ids import IdFactory
from crew_health.knowledge.domain.catalog import Extraction
from crew_health.knowledge.domain.errors import KnowledgeInvalid

type AllowedUse = Literal["risk_context", "patient_instruction", "routine_basis", "limit_basis"]
type SearchMode = Literal["exact", "stemmed"]


@dataclass(frozen=True, slots=True)
class PassageArtifact:
    passage_id: str
    extraction_id: str
    start: int
    end: int
    text_sha256: str
    text: str
    section: str
    page: int | None


def make_passage(
    extraction: Extraction, start: int, end: int, *, ids: IdFactory, maximum: int, section: str = ""
) -> PassageArtifact:
    if not 0 <= start < end <= len(extraction.text) or end - start > maximum:
        raise KnowledgeInvalid("span_bounds")
    text = extraction.text[start:end]
    page = next((p.page for p in extraction.page_map if p.start <= start and end <= p.end), None)
    return PassageArtifact(
        ids.derived("psg", extraction.extraction_id, str(start), str(end)),
        extraction.extraction_id,
        start,
        end,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text,
        section,
        page,
    )


@dataclass(frozen=True, slots=True)
class ApplicabilityField:
    allowed: frozenset[str]
    allow_unknown: bool = False


@dataclass(frozen=True, slots=True)
class ApplicabilityFilter:
    fields: tuple[tuple[str, ApplicabilityField], ...] = ()

    def admits(self, values: tuple[tuple[str, tuple[str, ...] | None], ...]) -> bool:
        known = dict(values)
        return all(
            (rule.allow_unknown if known.get(name) is None else bool(set(known[name] or ()) & rule.allowed))
            for name, rule in self.fields
        )


@dataclass(frozen=True, slots=True)
class ReleaseMembership:
    release_id: str
    passage: PassageArtifact
    source_ids: tuple[str, ...]
    license_kind: str
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
    use_status: tuple[tuple[AllowedUse, str], ...]
    applicability: tuple[tuple[str, tuple[str, ...] | None], ...]
    content_sha256: str
    simulation_only: bool = False


@dataclass(frozen=True, slots=True)
class PathwayCard:
    condition_id: str
    content_sha256: str
    assessment: str
    source_ids: tuple[str, ...]
    passage_ids: tuple[str, ...]
    purposes: tuple[str, ...]
    environments: tuple[str, ...] | None
    authored: bool
    simulation_only: bool
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
