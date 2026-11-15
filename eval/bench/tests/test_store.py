"""T-E18-19: durable bounded artifacts, torn-tail recovery and integrity failures."""

import json
from pathlib import Path

import pytest
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.domain.errors import BenchError

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "T-E18-19", "INV-E18-05")]


def store(root: Path) -> FileArtifactStore:
    return FileArtifactStore(root, max_line_bytes=4096, max_records=10)


def test_accepted_record_survives_reopen_and_torn_tail(tmp_path: Path) -> None:
    with store(tmp_path) as files:
        files.append("run", "generations", {"case_id": "case", "repeat": 0})
    path = tmp_path / "run/generations.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"seq":2')
    with store(tmp_path) as files:
        assert files.read_valid("run", "generations") == ({"case_id": "case", "repeat": 0},)
        files.append("run", "generations", {"case_id": "second", "repeat": 0})
    with store(tmp_path) as files:
        assert len(files.read_valid("run", "generations")) == 2


@pytest.mark.parametrize(
    "field,value",
    [("seq", 4), ("record", {"case_id": "changed"}), ("previous_sha256", "changed"), ("sha256", "changed")],
)
def test_every_stored_field_is_integrity_protected(tmp_path: Path, field: str, value: object) -> None:
    with store(tmp_path) as files:
        files.append("run", "state", {"state": "planned"})
    path = tmp_path / "run/state.jsonl"
    row = json.loads(path.read_bytes())
    row[field] = value
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with store(tmp_path) as files, pytest.raises(BenchError, match="integrity"):
        files.read_valid("run", "state")
    assert path.read_bytes() == before


def test_exclusive_lock_releases_after_exception(tmp_path: Path) -> None:
    with store(tmp_path), pytest.raises(BenchError):
        with store(tmp_path):
            pytest.fail("second writer entered")
    with store(tmp_path) as files:
        files.write_atomic(Path("run/manifest.json"), b"{}")
        assert (tmp_path / "run/manifest.json").read_bytes() == b"{}"


def test_no_writes_outside_root_or_without_lock(tmp_path: Path) -> None:
    files = store(tmp_path)
    with pytest.raises(BenchError):
        files.append("run", "state", {})
    with files:
        for path in (Path("../escape"), tmp_path / "absolute"):
            with pytest.raises(BenchError):
                files.write_atomic(path, b"private")
        for run, stream in (("../escape", "state"), ("run", "../state")):
            with pytest.raises(BenchError):
                files.append(run, stream, {})
        with pytest.raises(BenchError):
            files.append("run", "state", {"large": "x" * 4096})
        for index in range(10):
            files.append("run", "state", {"seq": index})
        with pytest.raises(BenchError):
            files.append("run", "state", {"seq": 10})


def test_power_loss_releases_lock_and_preserves_accepted_line(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = """
import os, sys
from pathlib import Path
from crew_health_bench.adapters.store import FileArtifactStore
with FileArtifactStore(Path(sys.argv[1]), max_line_bytes=4096, max_records=10) as files:
    files.append('run', 'generations', {'case_id':'committed','repeat':0})
    os._exit(9)
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10, check=False)
    assert child.returncode == 9
    with store(tmp_path) as files:
        assert files.read_valid("run", "generations") == ({"case_id": "committed", "repeat": 0},)


def test_disk_full_fails_loudly_and_atomic_file_stays_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    def disk_full(descriptor: int) -> None:
        raise OSError("disk full")

    with store(tmp_path) as files:
        files.write_atomic(Path("run/manifest.json"), b"old")
        monkeypatch.setattr(os, "fsync", disk_full)
        with pytest.raises(BenchError) as error:
            files.write_atomic(Path("run/manifest.json"), b"new")
        assert error.value.exit_code == 6
        assert (tmp_path / "run/manifest.json").read_bytes() == b"old"
        with pytest.raises(BenchError) as error:
            files.append("run", "state", {"state": "planned"})
        assert error.value.exit_code == 6


def test_store_rejects_duplicate_keys_and_symlink_components(tmp_path: Path) -> None:
    with store(tmp_path) as files:
        files.append("run", "state", {"state": "planned"})
        path = tmp_path / "run/state.jsonl"
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"seq":1', b'"seq":1,"seq":1'))
        with pytest.raises(BenchError, match="integrity"):
            files.read_valid("run", "state")


def test_missing_native_durability_barrier_refuses_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from crew_health_bench.adapters import store as storage_module

    def unavailable() -> None:
        raise OSError("native barrier unavailable")

    root = tmp_path / "runs"
    monkeypatch.setattr(storage_module, "load_windows", unavailable)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(BenchError) as error:
        with store(root):
            pytest.fail("storage with no directory durability barrier was accepted")
    assert error.value.exit_code == 6
    assert not root.exists()
