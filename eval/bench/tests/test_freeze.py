"""T-E18-02/20: reusable byte freeze with injected identity, time and scan bounds."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from crew_health.kernel.testing import MemoryClock

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.freeze import FreezeLibrary, sha256sum

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R23", "INV-E18-04")]


def library(repository: Path, maximum: int = 10) -> FreezeLibrary:
    return FreezeLibrary(
        repository_root=repository,
        clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch"),
        frozen_by="evaluation_owner",
        max_files=maximum,
        max_file_bytes=1024,
    )


def test_freeze_detects_changed_and_missing_files_and_verifies_digest(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "a.json").write_bytes(b"first")
    (root / "b.json").write_bytes(b"second")
    freezer = library(tmp_path)
    manifest = freezer.freeze_tree(root, include=("*.json",), exclude=())
    assert manifest.root == "fixture"
    assert manifest.frozen_by == "evaluation_owner"
    assert freezer.verify_tree(root, manifest) == ()
    assert manifest == freezer.freeze_tree(root, include=("*.json",), exclude=())
    assert "a.json" in manifest.files
    assert (
        sha256sum(manifest)
        == "".join(f"{manifest.files[name]}  {name}\n" for name in ("a.json", "b.json")).encode()
    )
    (root / "a.json").write_bytes(b"changed")
    (root / "b.json").unlink()
    assert {finding.path for finding in freezer.verify_tree(root, manifest)} == {"a.json", "b.json"}
    with pytest.raises(BenchError):
        freezer.verify_tree(root, replace(manifest, manifest_sha256="0" * 64))
    with pytest.raises(BenchError):
        freezer.verify_tree(tmp_path, manifest)


def test_freeze_exclusion_bounds_and_symlinks_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_bytes(b"visible")
    (tmp_path / "private.txt").write_bytes(b"excluded")
    manifest = library(tmp_path).freeze_tree(tmp_path, include=("*",), exclude=("private.txt",))
    assert set(manifest.files) == {"a.json"}
    with pytest.raises(BenchError):
        library(tmp_path, 1).freeze_tree(tmp_path, include=("*",), exclude=())
    (tmp_path / "link.json").symlink_to(tmp_path / "a.json")
    with pytest.raises(BenchError):
        library(tmp_path).freeze_tree(tmp_path, include=("*",), exclude=())


def test_freeze_empty_oversized_and_escape_rejected(tmp_path: Path) -> None:
    freezer = library(tmp_path)
    with pytest.raises(BenchError):
        freezer.freeze_tree(tmp_path, include=("*.json",), exclude=())
    (tmp_path / "large.json").write_bytes(b"x" * 1025)
    with pytest.raises(BenchError):
        freezer.freeze_tree(tmp_path, include=("*",), exclude=())
    with pytest.raises(BenchError):
        freezer.freeze_tree(tmp_path.parent, include=("*",), exclude=())
    with pytest.raises(BenchError):
        freezer.freeze_tree(tmp_path, include=("../*",), exclude=())


def test_checksum_file_cannot_be_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "first\nsecond.json").write_bytes(b"bytes")
    with pytest.raises(BenchError):
        library(tmp_path).freeze_tree(tmp_path, include=("*",), exclude=())
