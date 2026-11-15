from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.testing import MemoryIds
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.extraction import Extraction, FormPage, PageRange
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.segmentation import SegmentationPolicy, segment


def extraction(text: str) -> Extraction:
    return Extraction.create(
        ids=MemoryIds(identifiers=()),
        document_version_id="dv_source",
        format_version="1",
        text=text,
        pages=(PageRange(1, 0, len(text)),),
        extracted_by="editor_extract",
        max_chars=len(text),
        max_pages=1,
    )


@pytest.mark.trace("T-E10-12", "T-E10-22", "T-E10-27", "E10-03", "V22", "I2")
@given(st.text(min_size=1, max_size=200).filter(lambda value: value.isprintable()))
def test_unicode_span_preserves_exact_text(text: str) -> None:
    source = extraction(text)
    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=source,
        char_start=0,
        char_end=len(text),
        max_chars=len(text),
    )
    assert passage.text == text
    assert passage.text_sha256 == sha256(text.encode()).hexdigest()
    assert source.page_texts() == (FormPage(1, text),)


@pytest.mark.trace("T-E10-46", "T-E10-12", "I2")
def test_changed_text_or_page_map_changes_identity() -> None:
    text = Path("docs/product/NASA-SOURCES.md").read_text().splitlines()[0]
    first = extraction(text)
    second = extraction(text + " ")
    third = Extraction.create(
        ids=MemoryIds(identifiers=()),
        document_version_id=first.document_version_id,
        format_version=first.format_version,
        text=text,
        pages=(PageRange(2, 0, len(text)),),
        extracted_by=first.extracted_by,
        max_chars=len(text),
        max_pages=1,
    )
    assert len({first.extraction_id, second.extraction_id, third.extraction_id}) == 3
    assert first.text == text


@pytest.mark.trace("T-E10-12", "T-E10-55", "I8")
@pytest.mark.parametrize("start,end", [(-1, 2), (0, 0), (2, 1), (0, 999)])
def test_invalid_span_is_rejected(start: int, end: int) -> None:
    with pytest.raises(EvidenceInvalid):
        PassageArtifact.create(
            ids=MemoryIds(identifiers=()),
            extraction=extraction("α ≤ β"),
            char_start=start,
            char_end=end,
            max_chars=10,
        )


@pytest.mark.trace("T-E10-12", "I2")
def test_tampered_extraction_and_page_gaps_are_rejected() -> None:
    source = extraction("α ≤ β")
    with pytest.raises(EvidenceInvalid):
        replace(source, text="α < β")
    with pytest.raises(EvidenceInvalid):
        Extraction.create(
            ids=MemoryIds(identifiers=()),
            document_version_id="dv_source",
            format_version="1",
            text=source.text,
            pages=(PageRange(1, 1, len(source.text)),),
            extracted_by="editor_extract",
            max_chars=10,
            max_pages=1,
        )


@pytest.mark.trace("T-E10-12", "E10-03", "I2")
def test_passage_cannot_be_replaced_with_unhashed_text() -> None:
    source = extraction("α ≤ β")
    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=source,
        char_start=0,
        char_end=len(source.text),
        max_chars=10,
    )
    with pytest.raises(EvidenceInvalid):
        replace(passage, text="α < β")


@pytest.mark.trace("T-E10-26", "T-E10-27", "E10-03", "I2")
def test_table_headers_preserved_and_oversize_table_refused() -> None:
    table = next(
        line
        for line in Path("knowledge/inputs/package/NASA-SOURCES.md").read_text().splitlines()
        if line.startswith("| ID |")
    )
    words = len(table.split())
    policy = SegmentationPolicy(1, words, 0)
    assert segment(table, policy, table=True, max_chars=len(table), max_segments=10) == ((0, len(table)),)
    with pytest.raises(EvidenceInvalid, match="oversized_table"):
        segment(
            table, replace(policy, max_words=words - 1), table=True, max_chars=len(table), max_segments=10
        )


@pytest.mark.trace("T-E10-26", "T-E10-55", "E10-03", "I8")
@given(st.integers(min_value=1, max_value=80), st.integers(min_value=0, max_value=6))
def test_segments_cover_every_original_word_with_bounded_progress(word_count: int, overlap: int) -> None:
    text = " ".join(str(i) for i in range(word_count))
    policy = SegmentationPolicy(7, 10, overlap)
    spans = segment(text, policy, table=False, max_chars=len(text), max_segments=100)
    assert {word for start, end in spans for word in text[start:end].split()} == set(text.split())
    assert all(0 <= start < end <= len(text) for start, end in spans)
    assert all(len(text[start:end].split()) <= policy.max_words for start, end in spans)
    assert all(len(text[start:end].split()) >= min(word_count, policy.min_words) for start, end in spans)
    assert all(left[0] < right[0] for left, right in zip(spans[:-1], spans[1:], strict=True))
