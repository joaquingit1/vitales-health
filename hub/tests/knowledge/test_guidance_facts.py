from datetime import datetime
from pathlib import Path

import pytest

from crew_health.guidance.application.ports import ContentRef, Known, Unknown
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemorySettings
from crew_health.knowledge.adapters.guidance_facts import EvidenceContentFacts
from crew_health.knowledge.application.evidence_query import EvidenceQueryService
from crew_health.knowledge.application.registry_service import RegistryService
from crew_health.knowledge.domain.extraction import Extraction, PageRange
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.registry import DeclaredSource, RegistrySnapshot
from crew_health.knowledge.domain.release import ReleaseMembership, UseStatus
from crew_health.platform.testing.sqlite import database


@pytest.mark.trace("T-E10-24", "T-E10-59", "I3", "I4", "I5")
def test_content_facts_pin_snapshot_hash_purpose_and_source_without_clinical_gate(
    tmp_path: Path, at: datetime, settings: MemorySettings
) -> None:
    ids = MemoryIds(identifiers=())
    text = Path("knowledge/inputs/package/NASA-SOURCES.md").read_text(encoding="utf-8").splitlines()[0]
    extraction = Extraction.create(
        ids=ids,
        document_version_id="dv_a",
        format_version="1",
        text=text,
        pages=(PageRange(1, 0, len(text)),),
        extracted_by="editor_a",
        max_chars=len(text),
        max_pages=1,
    )
    passage = PassageArtifact.create(
        ids=ids, extraction=extraction, char_start=0, char_end=len(text), max_chars=len(text)
    )
    member = ReleaseMembership(
        "evr_a",
        passage,
        extraction,
        ("S01",),
        LicenseRecord("lic_a", "a" * 64, "nasa_public_domain", "S01", "editor_a", at),
        (UseStatus("risk_context", "approved", "approval_a", None),),
        "not_clinically_reviewed",
        frozenset({"station_leo"}),
        "section_a",
        False,
        False,
    )
    source = DeclaredSource("S01", text, "https://www.nasa.gov/", "input", None, None, None)
    sources = RegistryService(
        RegistrySnapshot("a" * 64, (), (), (source,), ()),
        ids=ids,
        max_rows=100,
        max_source_ids=10,
        max_url_chars=1000,
        tracking_parameters=(),
    )
    reader = EvidenceQueryService((member,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    ref = ContentRef("passage", passage.passage_id, passage.text_sha256)
    db = database(tmp_path)
    with db.snapshot() as view:
        facts = EvidenceContentFacts(view, evidence_release_id="evr_a", reader=reader, sources=sources)
        known = facts.content(view, ref, purpose="risk_context", release_id=view.release.release_id)
        assert known.source_and_hash_valid == Known(True)
        assert known.purpose_allowed == Known(True)
        assert known.clinical_review == "not_clinically_reviewed"
        assert known.withdrawn == Known(False)
        assert known.simulation_only == Known(False)
        assert known.environments == Known(frozenset({"station_leo"}))
        assert known.pinned_release_matches
        for content, purpose, release in (
            (ContentRef("passage", passage.passage_id, "0" * 64), "risk_context", view.release.release_id),
            (ref, "patient_instruction", view.release.release_id),
            (ref, "risk_context", "release_other"),
            (
                ContentRef("plan_step", passage.passage_id, passage.text_sha256),
                "risk_context",
                view.release.release_id,
            ),
        ):
            denied = facts.content(view, content, purpose=purpose, release_id=release)
            assert isinstance(denied.source_and_hash_valid, Unknown)
            assert not denied.pinned_release_matches
        with db.snapshot() as other:
            denied = facts.content(other, ref, purpose="risk_context", release_id=other.release.release_id)
            assert isinstance(denied.source_and_hash_valid, Unknown)
    db.close()
