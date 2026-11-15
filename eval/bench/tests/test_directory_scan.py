"""T-E18-03/04/05: complete bounded directory scans, no silent omissions."""

from pathlib import Path

import pytest

from crew_health_bench.adapters.leak_scanner import LeakScanner
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.leak import Fingerprint
from crew_health_bench.domain.text import fingerprints

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "V33", "T-E18-03", "T-E18-04")]


def test_nested_targets_include_every_file_and_detect_canaries(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/prompt.txt").write_text("private_evaluation_canary", encoding="utf-8")
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    fp = Fingerprint(
        "case_ref", fingerprints("hidden rubric fixture", fp_salt="salt"), ("private_evaluation_canary",)
    )
    scanner = LeakScanner(max_files=10, max_file_bytes=4096)
    findings = scanner.scan((("prompts", tmp_path),), (fp,), fp_salt="salt", leak_jaccard=0.2)
    assert any(row["kind"] == "canary" and row["path"].endswith("prompt.txt") for row in findings)


def test_scan_refuses_bound_exhaustion_missing_binary_or_symlink_targets(tmp_path: Path) -> None:
    fp = Fingerprint("case_ref", frozenset(), ("canary",))
    (tmp_path / "one").write_bytes(b"clean")
    (tmp_path / "two").write_bytes(b"clean")
    with pytest.raises(BenchError, match="target_unreadable"):
        LeakScanner(max_files=1, max_file_bytes=4096).scan(
            (("tools", tmp_path),), (fp,), fp_salt="salt", leak_jaccard=0.2
        )
    (tmp_path / "two").write_bytes(b"\xff")
    with pytest.raises(BenchError):
        LeakScanner(max_files=10, max_file_bytes=4096).scan(
            (("tools", tmp_path),), (fp,), fp_salt="salt", leak_jaccard=0.2
        )
    (tmp_path / "two").unlink()
    (tmp_path / "two").symlink_to(tmp_path / "one")
    with pytest.raises(BenchError, match="vault_path_escape"):
        LeakScanner(max_files=10, max_file_bytes=4096).scan(
            (("tools", tmp_path),), (fp,), fp_salt="salt", leak_jaccard=0.2
        )
