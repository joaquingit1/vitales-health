from datetime import datetime
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds, MemorySettings
from crew_health.kernel.uow import Actor
from crew_health.knowledge.adapters.release_index import SQLiteReleaseIndex, add_index_batch, seal_index
from crew_health.knowledge.domain.extraction import Extraction, PageRange
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import ApplicabilityFilter, ReleaseMembership, UseStatus
from crew_health.knowledge.domain.results import IndexUnavailable
from crew_health.platform.testing.sqlite import database


def indexed_member(at: datetime) -> ReleaseMembership:
    text = Path("knowledge/inputs/package/NASA-SOURCES.md").read_text(encoding="utf-8").splitlines()[0]
    ids = MemoryIds(identifiers=())
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
    return ReleaseMembership(
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


@pytest.mark.trace("T-E10-10", "T-E10-15", "T-E10-44", "T-E10-58", "I3", "I8")
def test_real_sqlite_lexical_index_filters_before_limit(
    tmp_path: Path, at: datetime, settings: MemorySettings
) -> None:
    db = database(tmp_path)
    sql = Path("hub/src/crew_health/knowledge/adapters/migrations/0002_knowledge_releases.sql").read_text(
        encoding="utf-8"
    )
    db.migrate(sql)
    member = indexed_member(at)
    with db.begin(actor=Actor("person", "editor_a"), correlation_id="correlation_a") as uow:
        add_index_batch(uow, (member,), settings=settings)
        seal_index(uow, member.release_id, 1, settings=settings)
    with db.snapshot() as view:
        index = SQLiteReleaseIndex(view, settings=settings)
        filters = ApplicabilityFilter(frozenset({"station_leo"}), False)
        for mode in ("exact", "stemmed"):
            assert index.search(member.release_id, "NASA", "risk_context", filters, mode, 1) == (
                member.passage.passage_id,
            )
        assert index.search(member.release_id, "NASA", "patient_instruction", filters, "exact", 1) == ()
        assert (
            index.search(
                member.release_id,
                "NASA",
                "risk_context",
                ApplicabilityFilter(frozenset({"mars_surface"}), True),
                "exact",
                1,
            )
            == ()
        )
        assert index.search(member.release_id, "nonexistent", "risk_context", filters, "exact", 1) == ()
    db.close()


@pytest.mark.trace("T-E10-44", "T-E10-58", "I8")
def test_missing_or_unsealed_index_is_unavailable(tmp_path: Path, settings: MemorySettings) -> None:
    db = database(tmp_path)
    filters = ApplicabilityFilter(None, True)
    with db.snapshot() as view:
        assert isinstance(
            SQLiteReleaseIndex(view, settings=settings).search(
                "evr_a", "NASA", "risk_context", filters, "exact", 1
            ),
            IndexUnavailable,
        )
    db.migrate(
        Path("hub/src/crew_health/knowledge/adapters/migrations/0002_knowledge_releases.sql").read_text(
            encoding="utf-8"
        )
    )
    with db.snapshot() as view:
        assert isinstance(
            SQLiteReleaseIndex(view, settings=settings).search(
                "evr_a", "NASA", "risk_context", filters, "exact", 1
            ),
            IndexUnavailable,
        )
    db.close()
