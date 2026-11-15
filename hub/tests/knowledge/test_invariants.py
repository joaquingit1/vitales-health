from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.extraction import Extraction, PageRange, page_map_hash
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.policy import PurposeRule
from crew_health.knowledge.domain.registry import Condition, DeclaredSource, RegistrySnapshot
from crew_health.knowledge.domain.review import ReviewTarget, SourceReview, effective_reviews
from crew_health.knowledge.domain.segmentation import SegmentationPolicy, segment


def source_extraction() -> Extraction:
    text = Path("knowledge/inputs/package/NASA-SOURCES.md").read_text().splitlines()[0]
    return Extraction.create(
        ids=MemoryIds(identifiers=()),
        document_version_id="dv_source",
        format_version="1",
        text=text,
        pages=(PageRange(1, 0, len(text)),),
        extracted_by="editor_source",
        max_chars=len(text),
        max_pages=1,
    )


@pytest.mark.trace("T-E10-12", "T-E10-55", "I2", "I8")
@pytest.mark.parametrize("page,start,end", ((0, 0, 1), (1, -1, 1), (1, 0, 0), (True, 0, 1)))
def test_page_map_coordinates_must_be_positive_integral_half_open_ranges(
    page: int, start: int, end: int
) -> None:
    with pytest.raises(EvidenceInvalid, match="page_map"):
        PageRange(page, start, end)


@pytest.mark.trace("T-E10-12", "T-E10-46", "I2")
def test_extraction_requires_complete_coverage_and_matching_map_hash() -> None:
    source = source_extraction()
    for invalid in (
        {"format_version": ""},
        {"text": ""},
        {"pages": ()},
        {"page_map_sha256": "0" * 64},
    ):
        with pytest.raises(EvidenceInvalid):
            replace(source, **invalid)
    for pages in (
        (PageRange(1, 0, len(source.text) - 1),),
        (PageRange(1, 0, len(source.text) + 1),),
        (PageRange(1, 0, 1), PageRange(1, 1, len(source.text))),
    ):
        with pytest.raises(EvidenceInvalid, match="page_map"):
            replace(source, pages=pages, page_map_sha256=page_map_hash(pages))


@pytest.mark.trace("T-E10-12", "T-E10-55", "I2", "I8")
def test_direct_passage_construction_checks_span_length_and_pages() -> None:
    extraction = source_extraction()
    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=extraction,
        char_start=0,
        char_end=len(extraction.text),
        max_chars=len(extraction.text),
    )
    for fields in (
        {"char_start": -1},
        {"char_end": 0},
        {"text": ""},
        {"page_start": 0},
        {"page_end": 0},
        {"char_start": True, "char_end": len(extraction.text) + 1},
        {"page_start": True},
    ):
        with pytest.raises(EvidenceInvalid):
            replace(passage, **fields)
    with pytest.raises(EvidenceInvalid, match="span"):
        PassageArtifact.create(
            ids=MemoryIds(identifiers=()),
            extraction=extraction,
            char_start=True,
            char_end=2,
            max_chars=len(extraction.text),
        )


@pytest.mark.trace("T-E10-26", "T-E10-55", "I2", "I8")
def test_segmentation_refuses_invalid_policy_and_exhaustion_without_truncating() -> None:
    for minimum, maximum, overlap in ((0, 0, 0), (2, 1, 0), (1, 2, 1), (True, 2, 0)):
        with pytest.raises(EvidenceInvalid, match="segmentation_policy"):
            SegmentationPolicy(minimum, maximum, overlap)
    policy = SegmentationPolicy(1, 2, 0)
    assert segment("", policy, table=False, max_chars=10, max_segments=1) == ()
    text = source_extraction().text
    with pytest.raises(EvidenceInvalid, match="segment_bound"):
        segment(text, policy, table=False, max_chars=len(text), max_segments=1)


@pytest.mark.trace("T-E10-09", "T-E10-24", "I3")
def test_invalid_purpose_configuration_cannot_enable_unknown_or_partial_instruction() -> None:
    for use, kinds, completeness in (
        ("", ("clinical_guideline",), ("full",)),
        ("risk_context", (), ("full",)),
        ("risk_context", ("unclassified",), ("full",)),
        ("risk_context", ("clinical_guideline",), ()),
        ("risk_context", ("clinical_guideline",), ("summary_only",)),
        ("patient_instruction", ("modeling",), ("full",)),
    ):
        with pytest.raises(EvidenceInvalid):
            PurposeRule(use, kinds, completeness, False)


@pytest.mark.trace("T-E10-04", "T-E10-06", "T-E10-30", "I1", "I2")
def test_invalid_registry_identities_and_duplicate_conditions_reject() -> None:
    source = DeclaredSource("S01", "title", "https://example.com", "input", None, None, None)
    for fields in ({"source_id": "S1"}, {"title": ""}, {"url": ""}, {"declaring_input": ""}):
        with pytest.raises(EvidenceInvalid):
            replace(source, **fields)
    condition = Condition("ICL1", "title", "active", "https://example.com#page=1", None, (), ("S01",), True)
    for fields in ({"condition_id": "ICL0"}, {"title": ""}, {"nasa_page": ""}, {"status": "unknown"}):
        with pytest.raises(EvidenceInvalid):
            replace(condition, **fields)
    with pytest.raises(EvidenceInvalid, match="duplicate_condition"):
        RegistrySnapshot(sha256(b"input").hexdigest(), (condition, condition), (), (), ())


@pytest.mark.trace("T-E10-23", "T-E10-55", "T-E10-57", "I3", "I8")
def test_review_kind_verdict_attestation_and_chain_target_are_checked(at: datetime) -> None:
    target = ReviewTarget("dv_source", "a" * 64, "ex_source", "b" * 64, "c" * 64)
    review = SourceReview(
        "review_one", target, "technical_check", "checker", "pass", None, "receipt_one", None, at
    )
    for fields in ({"kind": ""}, {"verdict": ""}, {"kind": "clinical_review", "attestation": None}):
        with pytest.raises(EvidenceInvalid):
            effective_reviews(
                (replace(review, **fields),),
                target,
                "technical_check",
                "extractor",
                max_records=10,
                max_chain_depth=10,
            )
    different = replace(
        review,
        review_id="review_two",
        target=replace(target, extraction_id="ex_other"),
        supersedes_review_id=review.review_id,
    )
    with pytest.raises(EvidenceInvalid, match="review_chain"):
        effective_reviews(
            (review, different), target, "technical_check", "extractor", max_records=10, max_chain_depth=10
        )
