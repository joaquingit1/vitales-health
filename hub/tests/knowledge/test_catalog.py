from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds
from crew_health.knowledge.domain.catalog import (
    CaptureAssessment,
    CaptureFacts,
    CatalogItem,
    DocumentVersion,
)
from crew_health.knowledge.domain.errors import EvidenceInvalid


@pytest.mark.trace("T-E10-08", "T-E10-34", "T-E10-46", "E10-02", "I3")
def test_capture_requires_exact_assessment_before_extraction(at: datetime) -> None:
    raw = Path("knowledge/inputs/package/NASA-SOURCES.md").read_bytes()
    facts = CaptureFacts("package_saved_text", "text/plain", True, False, None)
    captured = DocumentVersion.create(
        ids=MemoryIds(identifiers=()),
        item_id="cat_source",
        content_sha256=sha256(raw).hexdigest(),
        facts=facts,
        retrieved_at=at,
        captured_by="editor_a",
    )
    with pytest.raises(EvidenceInvalid, match="capture_not_assessed"):
        captured.require_extractable()
    assessment = CaptureAssessment(
        captured.document_version_id, captured.content_sha256, "full", facts, "assessment_a", "editor_a", at
    )
    assessed = captured.assess(assessment, expected_revision=1)
    assessed.require_extractable()
    assert captured.assessment is None
    assert assessed.revision == 2
    assert assessed.document_version_id == captured.document_version_id
    with pytest.raises(EvidenceInvalid, match="stale_revision"):
        assessed.assess(assessment, expected_revision=1)
    for bad in (
        replace(assessment, document_version_id="dv_other"),
        replace(assessment, content_sha256="0" * 64),
    ):
        with pytest.raises(EvidenceInvalid, match="assessment_target"):
            captured.assess(bad, expected_revision=1)
    with pytest.raises(EvidenceInvalid, match="capture_not_extractable"):
        replace(assessed, withdrawn=True).require_extractable()


@pytest.mark.trace("T-E10-06", "T-E10-51", "I2")
def test_catalog_identity_retains_aliases_without_merging_titles() -> None:
    ids = MemoryIds(identifiers=())
    url = "https://www.nasa.gov/"
    item = CatalogItem.create(ids=ids, normalized_url=url, original_url=url, title="source", source_id="S01")
    registered = item.register_source(
        "EV01", title="another declared title", original_url=url, expected_revision=1
    )
    assert item.source_ids == ("S01",)
    assert registered.source_ids == ("EV01", "S01")
    assert registered.item_id == item.item_id
    assert registered.titles == ("another declared title", "source")
    assert registered.revision == 2
    assert (
        registered.register_source(
            "EV01", title="another declared title", original_url=url, expected_revision=2
        )
        == registered
    )
    with pytest.raises(EvidenceInvalid, match="stale_revision"):
        registered.register_source("RT03", title="source", original_url=url, expected_revision=1)


@pytest.mark.trace("T-E10-08", "T-E10-55", "I3", "I8")
def test_catalog_preconditions_and_capture_revision_are_checked(at: datetime) -> None:
    ids = MemoryIds(identifiers=())
    for title in ("", " "):
        with pytest.raises(EvidenceInvalid):
            CatalogItem.create(
                ids=ids,
                normalized_url="https://www.nasa.gov/",
                original_url="https://www.nasa.gov/",
                title=title,
                source_id="S01",
            )
    captured = DocumentVersion.create(
        ids=ids,
        item_id="cat_a",
        content_sha256="a" * 64,
        facts=CaptureFacts("file_import", "text/plain", True, False, None),
        retrieved_at=at,
        captured_by="editor_a",
    )
    with pytest.raises(EvidenceInvalid):
        replace(captured, revision=0)
