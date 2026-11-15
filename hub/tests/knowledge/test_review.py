from dataclasses import replace
from datetime import datetime

import pytest

from crew_health.knowledge.domain.catalog import CaptureAssessment, CaptureFacts, assess_ceiling
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.policy import PurposeRule, source_eligible
from crew_health.knowledge.domain.review import (
    ReviewTarget,
    SourceReview,
    clinical_review_label,
    effective_reviews,
)


@pytest.mark.trace("T-E10-08", "T-E10-34", "E10-02", "I3")
@pytest.mark.parametrize(
    "method,complete,truncated,pages,expected",
    [
        ("file_import", True, False, 1, "full"),
        ("http_fetch", False, True, 1, "partial"),
        ("exa_contents", True, False, None, "partial"),
        ("search_result", True, False, None, None),
        ("http_fetch", False, False, None, "inaccessible"),
    ],
)
def test_capture_ceiling(
    method: str, complete: bool, truncated: bool, pages: int | None, expected: str | None
) -> None:
    if expected is None:
        with pytest.raises(EvidenceInvalid, match="method"):
            assess_ceiling(CaptureFacts(method, "application/pdf", complete, truncated, pages))
    else:
        assert assess_ceiling(CaptureFacts(method, "application/pdf", complete, truncated, pages)) == expected


@pytest.mark.trace("T-E10-08", "T-E10-09", "T-E10-34", "I3")
def test_assessment_cannot_raise_ceiling_or_change_capture(at: datetime) -> None:
    facts = CaptureFacts("exa_contents", "text/plain", True, False, None)
    with pytest.raises(EvidenceInvalid):
        CaptureAssessment("dv_a", "a" * 64, "full", facts, "ca_a", "editor_a", at).require_extractable(
            "a" * 64
        )
    full = CaptureAssessment(
        "dv_a",
        "a" * 64,
        "full",
        CaptureFacts("file_import", "text/plain", True, False, None),
        "ca_a",
        "editor_a",
        at,
    )
    full.require_extractable("a" * 64)
    with pytest.raises(EvidenceInvalid):
        full.require_extractable("b" * 64)


@pytest.mark.trace("T-E10-21", "I3")
def test_rights_require_documented_basis_and_cannot_infer_nasa_from_url(at: datetime) -> None:
    assert LicenseRecord("lic_a", "a" * 64, "metadata_only", None, "editor_a", at).permits_text is False
    assert LicenseRecord("lic_a", "a" * 64, "nasa_public_domain", "S01", "editor_a", at).permits_text
    with pytest.raises(EvidenceInvalid):
        LicenseRecord("lic_a", "a" * 64, "permission_documented", None, "editor_a", at)


@pytest.mark.trace("T-E10-23", "T-E10-46", "T-E10-57", "V24", "I3")
def test_review_heads_fail_closed_and_bind_exact_extraction(at: datetime) -> None:
    target = ReviewTarget("dv_a", "a" * 64, "ex_a", "b" * 64, "c" * 64)
    passed = SourceReview("rev_a", target, "technical_check", "checker", "pass", None, "receipt_a", None, at)
    failed = replace(passed, review_id="rev_b", verdict="revoked")
    assert (
        effective_reviews(
            (passed,), target, "technical_check", "extractor", max_records=10, max_chain_depth=10
        )
        == "pass"
    )
    assert (
        effective_reviews(
            (passed, failed), target, "technical_check", "extractor", max_records=10, max_chain_depth=10
        )
        == "fail"
    )
    assert (
        effective_reviews(
            (passed,),
            replace(target, extraction_id="ex_b"),
            "technical_check",
            "extractor",
            max_records=10,
            max_chain_depth=10,
        )
        == "none"
    )
    corrected = replace(passed, review_id="rev_c", supersedes_review_id="rev_b")
    assert (
        effective_reviews(
            (passed, failed, corrected),
            target,
            "technical_check",
            "extractor",
            max_records=10,
            max_chain_depth=10,
        )
        == "pass"
    )
    for bad in (
        replace(passed, reviewer="extractor"),
        replace(passed, audit_receipt=""),
        replace(passed, supersedes_review_id="absent"),
    ):
        with pytest.raises(EvidenceInvalid):
            effective_reviews(
                (bad,), target, "technical_check", "extractor", max_records=10, max_chain_depth=10
            )


@pytest.mark.trace("T-E10-09", "T-E10-24", "T-E10-59", "I3", "I4")
def test_full_instruction_eligibility_is_independent_of_clinical_review() -> None:
    rule = PurposeRule(
        "patient_instruction", ("clinical_guideline", "first_aid_guidance", "device_manual"), ("full",), False
    )
    assert source_eligible(
        rule,
        use="patient_instruction",
        source_kind="clinical_guideline",
        completeness="full",
        partial_acknowledged=False,
        technical_verdict="pass",
        permits_text=True,
    )
    for use, kind, completeness, verdict, rights in (
        ("patient_instruction", "unclassified", "full", "pass", True),
        ("patient_instruction", "clinical_guideline", "partial", "pass", True),
        ("patient_instruction", "clinical_guideline", "full", "fail", True),
        ("patient_instruction", "clinical_guideline", "full", "pass", False),
        ("risk_context", "clinical_guideline", "full", "pass", True),
    ):
        assert not source_eligible(
            rule,
            use=use,
            source_kind=kind,
            completeness=completeness,
            partial_acknowledged=False,
            technical_verdict=verdict,
            permits_text=rights,
        )


@pytest.mark.trace("T-E10-09", "T-E10-24", "I3")
def test_partial_risk_requires_permission_and_acknowledgment() -> None:
    rule = PurposeRule("risk_context", ("clinical_guideline",), ("full", "partial"), True)
    assert source_eligible(
        rule,
        use="risk_context",
        source_kind="clinical_guideline",
        completeness="partial",
        partial_acknowledged=True,
        technical_verdict="pass",
        permits_text=True,
    )
    assert not source_eligible(
        rule,
        use="risk_context",
        source_kind="clinical_guideline",
        completeness="partial",
        partial_acknowledged=False,
        technical_verdict="pass",
        permits_text=True,
    )
    with pytest.raises(EvidenceInvalid):
        replace(rule, partial_acknowledgment_required=False)
    with pytest.raises(EvidenceInvalid):
        replace(rule, use="patient_instruction")


@pytest.mark.trace("T-E10-23", "T-E10-55", "I3", "I8")
def test_review_cycles_and_missing_ancestry_reject(at: datetime) -> None:
    target = ReviewTarget("dv_a", "a" * 64, "ex_a", "b" * 64, "c" * 64)
    first = SourceReview(
        "rev_a", target, "technical_check", "checker", "pass", None, "receipt_a", "rev_b", at
    )
    second = replace(first, review_id="rev_b", supersedes_review_id="rev_a")
    third = replace(first, review_id="rev_c", supersedes_review_id=None)
    for reviews in ((first, second), (first, second, third), (first, first), (first,)):
        with pytest.raises(EvidenceInvalid):
            effective_reviews(
                reviews, target, "technical_check", "extractor", max_records=10, max_chain_depth=10
            )


@pytest.mark.trace("T-E10-08", "T-E10-34", "T-E10-55", "I3", "I8")
def test_capture_conflicts_missing_pdf_pages_and_unsupported_inputs(at: datetime) -> None:
    for pages in (0, -1, True):
        with pytest.raises(EvidenceInvalid):
            CaptureFacts("http_fetch", "application/pdf", True, False, pages)
    with pytest.raises(EvidenceInvalid):
        CaptureFacts("http_fetch", "application/pdf", True, True, 1)
    assert assess_ceiling(CaptureFacts("http_fetch", "application/pdf", True, False, None)) == "inaccessible"
    for method, media in (("unknown", "application/pdf"), ("http_fetch", "application/octet-stream")):
        with pytest.raises(EvidenceInvalid):
            assess_ceiling(CaptureFacts(method, media, True, False, 1))
    summary = CaptureAssessment(
        "dv_a",
        "a" * 64,
        "summary_only",
        CaptureFacts("exa_contents", "text/plain", True, False, None),
        "ca_a",
        "editor_a",
        at,
    )
    with pytest.raises(EvidenceInvalid):
        summary.require_extractable("a" * 64)


@pytest.mark.trace("T-E10-08", "T-E10-34", "I3")
def test_undeclared_capture_method_cannot_create_extractable_content() -> None:
    with pytest.raises(EvidenceInvalid, match="method"):
        assess_ceiling(CaptureFacts("search_result", "text/plain", True, False, None))


@pytest.mark.trace("T-E10-24", "T-E10-49", "I3", "I4")
def test_clinical_attestation_changes_only_review_label(at: datetime) -> None:
    target = ReviewTarget("dv_a", "a" * 64, "ex_a", "b" * 64, "c" * 64)
    team = SourceReview(
        "rev_a",
        target,
        "clinical_review",
        "checker",
        "pass",
        "team_member_not_clinician",
        "receipt_a",
        None,
        at,
    )
    clinician = replace(
        team, review_id="rev_b", attestation="licensed_clinician", supersedes_review_id="rev_a"
    )
    assert (
        clinical_review_label((), target, "extractor", max_records=10, max_chain_depth=10)
        == "not_clinically_reviewed"
    )
    assert (
        clinical_review_label((team,), target, "extractor", max_records=10, max_chain_depth=10)
        == "not_clinically_reviewed"
    )
    assert (
        clinical_review_label((team, clinician), target, "extractor", max_records=10, max_chain_depth=10)
        == "clinically_reviewed"
    )
    revoked = replace(clinician, review_id="rev_c", verdict="revoked", supersedes_review_id="rev_b")
    assert (
        clinical_review_label(
            (team, clinician, revoked), target, "extractor", max_records=10, max_chain_depth=10
        )
        == "not_clinically_reviewed"
    )
    assert team.attestation == "team_member_not_clinician"


@pytest.mark.trace("T-E10-55", "I8")
def test_review_chain_has_separate_depth_limit(at: datetime) -> None:
    target = ReviewTarget("dv_a", "a" * 64, "ex_a", "b" * 64, "c" * 64)
    first = SourceReview("rev_a", target, "technical_check", "checker", "pass", None, "receipt_a", None, at)
    second = replace(first, review_id="rev_b", supersedes_review_id="rev_a")
    third = replace(first, review_id="rev_c", supersedes_review_id="rev_b")
    with pytest.raises(EvidenceInvalid):
        effective_reviews(
            (first, second, third), target, "technical_check", "extractor", max_records=3, max_chain_depth=2
        )
    assert (
        effective_reviews(
            (first, second, third), target, "technical_check", "extractor", max_records=3, max_chain_depth=3
        )
        == "pass"
    )
