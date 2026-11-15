from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from crew_health.kernel.ids import require_id
from crew_health.tools.domain.errors import ToolsInvalid


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    k_lexical: int
    k_dense: int
    final_max: int
    budget_tokens: int
    max_per_parent: int
    k_rrf: Decimal
    rerank: bool


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    passage_id: str
    release_id: str
    use: str
    content_sha256: str
    text: str
    page_start: int
    page_end: int
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
    use_status: Literal["approved", "superseded", "withdrawn"] | None = None
    successor_ref: str | None = None
    source_ids: tuple[str, ...] = ()
    document_version_id: str | None = None
    usable_as_support: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    status: Literal["ok", "no_result", "unavailable"]
    passages: tuple[RetrievedPassage, ...]
    limitations: tuple[str, ...]
    history: tuple[RetrievedPassage, ...]
    history_complete: bool = True


def fuse_ranks(
    rankings: tuple[tuple[str, ...], ...], *, k_rrf: Decimal, k_max: int, candidates_max: int
) -> tuple[str, ...]:
    if not k_rrf.is_finite() or k_rrf <= 0 or k_max < 1 or candidates_max < 1:
        raise ToolsInvalid("CONFIG_INVALID")
    if len(rankings) > 3 or sum(len(ranking) for ranking in rankings) > candidates_max:
        raise ToolsInvalid("PAYLOAD_TOO_LARGE")
    scores: dict[str, Fraction] = {}
    constant = Fraction(k_rrf)
    for ranking in rankings:
        if len(ranking) > k_max or len(set(ranking)) != len(ranking):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        for rank, passage_id in enumerate(ranking, 1):
            require_id(passage_id)
            scores[passage_id] = scores.get(passage_id, Fraction(0)) + 1 / (constant + rank)
    return tuple(sorted(scores, key=lambda passage_id: (-scores[passage_id], passage_id)))
