"""The worker's state directory (FileEvidenceStore): undelivered attempts survive a restart, a crash mid write never
loads half a file, and damage fails loudly instead of dropping evidence (AGENTS 1.6, AGENTS 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_health_camera.adapters.evidence_store import FileEvidenceStore
from crew_health_camera.application.ports import EvidenceUnreadable

DOC = {"spec": {"acquisition_id": "acq_1"}, "receipt_id": "rcp_1", "body": {"outcome": "success"}}


@pytest.mark.trace("AGENTS-1.6", "V16")
def test_saved_evidence_is_loaded_by_a_new_store_over_the_same_directory(tmp_path: Path) -> None:
    FileEvidenceStore(tmp_path, max_files=4).save("acq_1", DOC)
    assert FileEvidenceStore(tmp_path, max_files=4).pending() == (DOC,)


@pytest.mark.trace("AGENTS-1.6")
def test_a_crash_mid_write_leaves_only_a_temporary_file_that_is_never_loaded(tmp_path: Path) -> None:
    store = FileEvidenceStore(tmp_path, max_files=4)
    store.save("acq_1", DOC)
    (tmp_path / "acq_2.tmp").write_text('{"spec": {"acqu', encoding="utf-8")
    assert store.pending() == (DOC,)


@pytest.mark.trace("AGENTS-1.6", "V08")
def test_delivered_evidence_is_removed_and_rejected_evidence_is_kept_aside(tmp_path: Path) -> None:
    store = FileEvidenceStore(tmp_path, max_files=4)
    store.save("acq_1", DOC)
    store.save("acq_2", DOC)
    store.delivered("acq_1")
    store.rejected("acq_2")
    store.rejected("acq_3")
    assert store.pending() == ()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acq_2.rejected.json"]


@pytest.mark.trace("AGENTS-6.storage")
@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_a_damaged_file_fails_loudly_and_stays_on_disk(tmp_path: Path, content: str) -> None:
    (tmp_path / "acq_1.json").write_text(content, encoding="utf-8")
    with pytest.raises(EvidenceUnreadable):
        FileEvidenceStore(tmp_path, max_files=4).pending()
    assert (tmp_path / "acq_1.json").exists()


@pytest.mark.trace("AGENTS-6.storage", "AGENTS-3.2")
def test_more_saved_attempts_than_the_table_holds_fail_loudly_instead_of_dropping_some(
    tmp_path: Path,
) -> None:
    store = FileEvidenceStore(tmp_path, max_files=1)
    store.save("acq_1", DOC)
    store.save("acq_2", DOC)
    with pytest.raises(EvidenceUnreadable):
        store.pending()


@pytest.mark.trace("AGENTS-3.7")
@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "x" * 200])
def test_an_acquisition_id_that_is_not_a_plain_name_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        FileEvidenceStore(tmp_path, max_files=4).save(bad, DOC)
