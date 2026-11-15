import json
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds, MemorySettings
from crew_health.kernel.commands import canonical_json
from crew_health.knowledge.adapters.bundle import DeterministicBundleWriter
from crew_health.knowledge.adapters.policy_file import load_policy
from crew_health.knowledge.adapters.release_codec import CanonicalReleaseCodec
from crew_health.knowledge.application.release_builder import Built, ReleaseBuilder
from crew_health.knowledge.domain.catalog import CaptureAssessment, CaptureFacts, DocumentVersion
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.export import (
    ApprovalEvent,
    ArtifactReference,
    ReleaseTarget,
    ReviewExport,
    Span,
    UseApproval,
)
from crew_health.knowledge.domain.extraction import Extraction, PageRange
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.review import SourceReview
from crew_health.knowledge.domain.review import PathwayCardTarget
from crew_health.knowledge.domain.release import PathwayCard


def export_fixture(at: datetime) -> tuple[ReviewExport, dict[str, bytes]]:
    source = json.loads(Path("docs/product/cliff/forms/ICL106.json").read_text(encoding="utf-8"))
    parts = [page["text"] for page in source["pages"]]
    text = "\n".join(parts)
    ranges = []
    start = 0
    for index, part in enumerate(parts):
        end = start + len(part) + (1 if index < len(parts) - 1 else 0)
        ranges.append(PageRange(source["pages"][index]["page"], start, end))
        start = end
    raw = text.encode("utf-8")
    digest = sha256(raw).hexdigest()
    ids = MemoryIds(identifiers=())
    facts = CaptureFacts("package_saved_text", "text/plain", True, False, len(parts))
    doc = DocumentVersion.create(
        ids=ids,
        item_id=ids.derived("cat", source["url"]),
        content_sha256=digest,
        facts=facts,
        retrieved_at=at,
        captured_by="editor_capture",
    )
    assessment = CaptureAssessment(
        doc.document_version_id, digest, "full", facts, "assessment_a", "editor_capture", at
    )
    doc = doc.assess(assessment, expected_revision=1)
    extraction = Extraction.create(
        ids=ids,
        document_version_id=doc.document_version_id,
        format_version="1",
        text=text,
        pages=tuple(ranges),
        extracted_by="editor_extract",
        max_chars=len(text),
        max_pages=len(parts),
    )
    target = ReleaseTarget(
        doc,
        extraction,
        ArtifactReference(digest, len(raw)),
        LicenseRecord(
            "lic_a",
            digest,
            "nasa_public_domain",
            "RS07; NASA CliFF ICL106, saved text; original PDF absent",
            "editor_capture",
            at,
        ),
        ("RS07",),
        "modeling_cliff",
        None,
    )
    review = SourceReview(
        "review_technical",
        target.review_target(),
        "technical_check",
        "editor_checker",
        "pass",
        None,
        "receipt_review",
        None,
        at,
        "full",
        target.source_kind,
        None,
    )
    policy_hash = sha256(Path("knowledge/policy/release-policy.json").read_bytes()).hexdigest()
    approval = UseApproval(
        "approval_a",
        target.review_target(),
        "risk_context",
        (Span(0, min(len(parts[0]), 1000), "page_1"),),
        False,
        assessment.assessment_id,
        review.review_id,
        None,
        target.license.license_id,
        policy_hash,
        "editor_approver",
        at,
        1,
        (ApprovalEvent(1, "active", None, None, at, "receipt_approval"),),
    )
    export = ReviewExport(
        "export_a",
        "1",
        "a" * 64,
        None,
        "b" * 64,
        policy_hash,
        None,
        at,
        "editor_export",
        None,
        (target,),
        (review,),
        (review.review_id,),
        (approval,),
        (review.audit_receipt, "receipt_approval"),
        (),
        (),
        (),
        (),
        (),
    )
    return export, {digest: raw}


class Artifacts:
    def __init__(self, members: dict[str, bytes]) -> None:
        self.members = members

    def read(self, reference: ArtifactReference) -> bytes:
        return self.members[reference.sha256]


class ExportAuthority:
    def __init__(self, expected_hash: str) -> None:
        self.expected_hash = expected_hash

    def verify(self, export_sha256: str, checkpoint: bytes) -> None:
        if export_sha256 != self.expected_hash:
            raise EvidenceInvalid("export_checkpoint")


@pytest.mark.trace("T-E10-12", "T-E10-21", "T-E10-25", "T-E10-47", "T-E10-50", "E10-03", "I2", "I3")
def test_nonpublished_build_is_deterministic_and_preserves_saved_nasa_text(
    at: datetime, settings: MemorySettings
) -> None:
    export, members = export_fixture(at)
    codec = CanonicalReleaseCodec(settings=settings)
    builder = ReleaseBuilder(
        ids=MemoryIds(identifiers=()),
        artifacts=Artifacts(members),
        settings=settings,
        policy=load_policy(Path("knowledge/policy/release-policy.json").read_bytes(), settings=settings),
        codec=codec,
        writer=DeterministicBundleWriter(settings=settings),
        authority=ExportAuthority(sha256(codec.encode_export(export)).hexdigest()),
    )
    first = builder.build(export)
    second = builder.build(export)
    assert isinstance(first, Built)
    assert first == second
    assert first.content.memberships[0].passage.text == export.targets[0].extraction.text[:1000]
    assert first.content.memberships[0].clinical_review == "not_clinically_reviewed"
    assert first.content.memberships[0].license.content_sha256 == export.targets[0].original.sha256


@pytest.mark.trace("T-E10-09", "T-E10-23", "T-E10-24", "T-E10-57", "I3")
def test_build_rejects_wrong_purpose_omitted_heads_receipts_and_original_tampering(
    at: datetime, settings: MemorySettings
) -> None:
    export, members = export_fixture(at)
    codec = CanonicalReleaseCodec(settings=settings)
    builder = ReleaseBuilder(
        ids=MemoryIds(identifiers=()),
        artifacts=Artifacts(members),
        settings=settings,
        policy=load_policy(Path("knowledge/policy/release-policy.json").read_bytes(), settings=settings),
        codec=codec,
        writer=DeterministicBundleWriter(settings=settings),
        authority=ExportAuthority(sha256(codec.encode_export(export)).hexdigest()),
    )
    for invalid in (
        replace(export, review_heads=()),
        replace(export, audit_receipts=()),
        replace(export, approvals=(replace(export.approvals[0], use="patient_instruction"),)),
        replace(export, source_reviews=(replace(export.source_reviews[0], reviewer="editor_extract"),)),
    ):
        assert not isinstance(builder.build(invalid), Built)
    members[export.targets[0].original.sha256] = b"tampered"
    assert not isinstance(builder.build(export), Built)


@pytest.mark.trace("T-E10-24", "T-E10-59", "I3", "I4")
def test_authored_card_binds_support_and_independent_review_label(at: datetime, settings: MemorySettings) -> None:
    export, members = export_fixture(at)
    target = export.targets[0]
    span = export.approvals[0].spans[0]
    passage_id = MemoryIds(identifiers=()).derived("psg", target.extraction.extraction_id, str(span.char_start), str(span.char_end))
    row = next(row for row in json.loads(Path("knowledge/inputs/package/condition-assessments-119.json").read_bytes()) if row["condition_id"] == "ICL106")
    assessment = (("clinical_assessment", row["clinical_assessment"]), ("limitations", row["limitations"]))
    body = {"condition_id": row["condition_id"], "indicative_assessment": assessment, "workup_source_ids": ("RS07",), "supporting_passage_ids": (passage_id,)}
    digest = sha256(canonical_json(body)).hexdigest()
    card = PathwayCard(row["condition_id"], assessment, ("RS07",), (passage_id,), digest, "editor_card", True, False, None)
    review = SourceReview("review_card", PathwayCardTarget(card.condition_id, digest), "technical_check", "editor_checker", "pass", None, "receipt_card_review", None, at, "full", None, None)
    approval = replace(export.approvals[0], approval_id="approval_card", target=review.target, spans=(), assessment_id=None, license_id=None, technical_check_id=review.review_id)
    export = replace(export, cards=(card,), source_reviews=(*export.source_reviews, review), review_heads=(*export.review_heads, review.review_id),
        approvals=(*export.approvals, approval), audit_receipts=(*export.audit_receipts, review.audit_receipt))
    codec = CanonicalReleaseCodec(settings=settings)
    builder = ReleaseBuilder(ids=MemoryIds(identifiers=()), artifacts=Artifacts(members), settings=settings,
        policy=load_policy(Path("knowledge/policy/release-policy.json").read_bytes(), settings=settings), codec=codec,
        writer=DeterministicBundleWriter(settings=settings), authority=ExportAuthority(sha256(codec.encode_export(export)).hexdigest()))
    built = builder.build(export)
    assert isinstance(built, Built)
    assert built.content.cards[0].card == card
    assert built.content.cards[0].clinical_review == "not_clinically_reviewed"
