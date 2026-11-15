"""T-E18-01/02/03/04/07: owner-authorized, reproducible partition publication."""

from pathlib import Path

import pytest
from crew_health.kernel.testing import MemoryIds

from crew_health_bench.adapters.case_documents import CaseDocuments
from crew_health_bench.adapters.documents import load_document, read_json
from crew_health_bench.adapters.partitions import verify_partitions
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.partitioning import FreezePartitions
from crew_health_bench.domain.errors import BenchError

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "INV-E18-01", "INV-E18-04", "T-E18-02")]
ROOT = Path(__file__).parents[1]


class Owner:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def require_owner(self) -> None:
        if not self.allowed:
            raise BenchError("holdout_key_required", exit_code=5)


def test_freeze_publishes_marker_last_and_identical_bytes(tmp_path: Path) -> None:
    documents = CaseDocuments.from_config(ROOT / "config", max_file_bytes=1048576, ids=MemoryIds(identifiers=()))
    loaded = documents.load(ROOT / "fixtures/case-document.json")
    case = loaded.case
    policy = load_document(ROOT / "config/partition-policy.json", max_file_bytes=16777216)
    hidden = (ROOT / "fixtures/hidden-part.json").read_text(encoding="utf-8")
    with FileArtifactStore(tmp_path, max_line_bytes=1048576, max_records=10) as store:
        freezer = FreezePartitions(store=store, owner=Owner(True))
        freezer.freeze(
            (case,),
            policy,
            salt=b"s" * 32,
            fp_salt="public",
            texts={case.case_id: (case.visible, loaded.document.revealable.model_dump_json(), hidden)},
            previous={},
        )
        before = {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        freezer.freeze(
            (case,),
            policy,
            salt=b"s" * 32,
            fp_salt="public",
            texts={case.case_id: (case.visible, loaded.document.revealable.model_dump_json(), hidden)},
            previous={},
        )
        assert before == {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        verify_partitions(tmp_path, max_file_bytes=16777216)
        fingerprints = read_json(tmp_path / "fingerprints/holdout.fp.json", max_file_bytes=16777216)
        assert len(fingerprints["cases"]) == 3
        (tmp_path / "partitions/manifest.json").write_bytes(b"{}")
        with pytest.raises(BenchError, match="integrity"):
            verify_partitions(tmp_path, max_file_bytes=16777216)


def test_missing_owner_or_hidden_text_never_commits_a_freeze(tmp_path: Path) -> None:
    documents = CaseDocuments.from_config(ROOT / "config", max_file_bytes=1048576, ids=MemoryIds(identifiers=()))
    loaded = documents.load(ROOT / "fixtures/case-document.json")
    case = loaded.case
    policy = load_document(ROOT / "config/partition-policy.json", max_file_bytes=16777216)
    with FileArtifactStore(tmp_path, max_line_bytes=1048576, max_records=10) as store:
        with pytest.raises(BenchError, match="holdout_key_required"):
            FreezePartitions(store=store, owner=Owner(False)).freeze(
                (case,), policy, salt=b"s" * 32, fp_salt="public", texts={}, previous={}
            )
        with pytest.raises(BenchError, match="case_invalid"):
            FreezePartitions(store=store, owner=Owner(True)).freeze(
                (case,), policy, salt=b"s" * 32, fp_salt="public", texts={}, previous={}
            )
    assert not (tmp_path / "partitions/FROZEN.sha256").exists()
