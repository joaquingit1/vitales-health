from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryIds, MemorySettings
from crew_health.kernel.uow import Actor
from crew_health.knowledge.adapters.release_codec import CanonicalReleaseCodec
from crew_health.knowledge.adapters.sqlite_lineage_archive import SQLiteLineageArchive
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.export import ReviewExport
from crew_health.knowledge.domain.manifest import MemberSnapshot, ReleaseContent
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import UseStatus
from crew_health.platform.testing.sqlite import database


@pytest.mark.trace("T-E10-16", "T-E10-49", "T-E10-51", "T-E10-52", "R21", "I2", "I5")
def test_real_sqlite_retains_exact_purpose_snapshots_across_branches(
    tmp_path: Path, review_export: ReviewExport, settings: MemorySettings
) -> None:
    target = review_export.targets[0]
    span = review_export.approvals[0].spans[0]
    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=target.extraction,
        char_start=span.char_start,
        char_end=span.char_end,
        max_chars=settings.integer("knowledge.passage.max_chars"),
    )
    first = MemberSnapshot(
        passage,
        target.extraction,
        target.source_ids,
        target.license,
        (UseStatus("risk_context", "approved", "approval_a", None),),
        "not_clinically_reviewed",
        None,
        span.section,
        False,
        False,
    )
    changed = replace(
        first,
        uses=(UseStatus("risk_context", "withdrawn", "approval_a", None),),
        source_ids=(*first.source_ids, "EV01"),
    )
    db = database(tmp_path)
    db.migrate(
        Path("hub/src/crew_health/knowledge/adapters/migrations/0003_knowledge_lineage.sql").read_text(
            encoding="utf-8"
        )
    )
    archive = SQLiteLineageArchive(settings=settings, codec=CanonicalReleaseCodec(settings=settings))
    for release_id, member in (("evr_a", first), ("evr_b", changed), ("evr_c", first)):
        with db.begin(actor=Actor("operator", "operator_a"), correlation_id="correlation_a") as uow:
            archive.append(uow, release_id, ReleaseContent((member,), (), ()))
    with db.snapshot() as view:
        assert archive.read(view, "evr_a", passage.passage_id) == first.bind("evr_a")
        assert archive.read(view, "evr_b", passage.passage_id) == changed.bind("evr_b")
        assert archive.read(view, "evr_c", passage.passage_id) == first.bind("evr_c")
        assert archive.read(view, "evr_missing", passage.passage_id) is None
        history = archive.history(view, passage.passage_id, None, 2)
        assert tuple(row.release_id for row in history.items) == ("evr_a", "evr_b")
        assert history.next_cursor == "evr_b"
        assert archive.history(view, passage.passage_id, history.next_cursor, 2).items == (
            first.bind("evr_c"),
        )
    for table in ("knowledge_passage_artifact", "knowledge_release_member", "knowledge_release_use"):
        with pytest.raises(StorageUnavailable):
            with db.begin(actor=Actor("operator", "operator_a"), correlation_id="correlation_delete") as uow:
                uow.connection.execute("DELETE FROM " + table)
    db.migrate(
        "DROP TRIGGER knowledge_artifact_no_update; UPDATE knowledge_passage_artifact SET char_start=1;"
    )
    with db.snapshot() as view:
        with pytest.raises(EvidenceInvalid, match="artifact"):
            archive.read(view, "evr_a", passage.passage_id)
    db.close()
