from dataclasses import replace

import pytest

from crew_health.knowledge.domain.review import Approval, Review, eligible

pytestmark = pytest.mark.trace("R21", "V24", "T-E10-23", "T-E10-24", "T-E10-57")


def approval() -> Approval:
    return Approval(
        "approve_1",
        "extract_1",
        "a" * 64,
        "patient_instruction",
        "editor_b",
        "extractor_a",
        "full",
        "clinical_guideline",
        "nasa_public_domain",
        False,
        ("review_1",),
        ("receipt_1",),
    )


def test_technically_reviewed_full_guideline_is_eligible_without_clinician() -> None:
    review = Review(
        "review_1", "extract_1", "a" * 64, "technical_check", "pass", "editor_b", None, ("receipt_1",)
    )
    result = eligible(approval(), (review,))
    assert result.allowed
    assert result.clinical_review == "not_clinically_reviewed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "modeling"),
        ("completeness", "partial"),
        ("license_kind", "metadata_only"),
        ("approver", "extractor_a"),
        ("receipt_refs", ()),
    ],
)
def test_invalid_approval_cannot_enable_patient_instructions(field: str, value: object) -> None:
    review = Review(
        "review_1", "extract_1", "a" * 64, "technical_check", "pass", "editor_b", None, ("receipt_1",)
    )
    assert not eligible(replace(approval(), **{field: value}), (review,)).allowed


def test_omitted_or_failed_review_head_cannot_be_hidden() -> None:
    passed = Review(
        "review_1", "extract_1", "a" * 64, "technical_check", "pass", "editor_b", None, ("receipt_1",)
    )
    failed = replace(passed, review_id="review_2", verdict="fail")
    assert not eligible(approval(), (passed, failed)).allowed
    assert not eligible(approval(), ()).allowed


def test_reextracted_body_does_not_reuse_old_review() -> None:
    review = Review(
        "review_1", "extract_old", "a" * 64, "technical_check", "pass", "editor_b", None, ("receipt_1",)
    )
    assert not eligible(approval(), (review,)).allowed
