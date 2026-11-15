from __future__ import annotations

import re
from dataclasses import dataclass

from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound


@dataclass(frozen=True, slots=True)
class SegmentationPolicy:
    min_words: int
    max_words: int
    overlap_words: int

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.min_words, self.max_words, self.overlap_words)):
            raise EvidenceInvalid("segmentation_policy")
        if not 0 <= self.overlap_words < self.min_words <= self.max_words:
            raise EvidenceInvalid("segmentation_policy")


def segment(
    text: str,
    policy: SegmentationPolicy,
    *,
    table: bool,
    max_chars: int,
    max_segments: int,
) -> tuple[tuple[int, int], ...]:
    require_bound(len(text), max_chars)
    require_bound(1, max_segments)
    words = tuple(re.finditer(r"\S+", text))
    if not words:
        return ()
    if table:
        if len(words) > policy.max_words:
            raise EvidenceInvalid("oversized_table")
        return ((0, len(text)),)
    spans = []
    start = 0
    for _ in range(max_segments):
        end = min(start + policy.max_words, len(words))
        spans.append((words[start].start(), words[end - 1].end()))
        if end == len(words):
            return tuple(spans)
        start = end - policy.overlap_words
        if len(words) - start < policy.min_words:
            start = len(words) - policy.min_words
    raise EvidenceInvalid("segment_bound")
