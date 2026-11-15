"""T-E18-19: exercise the native move boundary and metadata barrier without hardware."""

from pathlib import Path

import pytest
from crew_health_bench.adapters.windows_files import WindowsFiles

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "T-E18-19")]


def test_windows_move_is_write_through_and_directory_barrier_is_durable(tmp_path: Path) -> None:
    calls: list[tuple[str, str, int]] = []

    def move(source: str, target: str, flags: int) -> int:
        calls.append((source, target, flags))
        Path(source).replace(target)
        return 1

    files = WindowsFiles(move=move, last_error=lambda: 0)
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"durable")
    files.replace(source, target)
    assert target.read_bytes() == b"durable"
    assert calls[-1][2] == 0x1 | 0x8
    files.sync_directory(tmp_path)
    assert calls[-1][2] == 0x1 | 0x8
    assert Path(calls[-1][1]).read_bytes() == b""


def test_native_move_error_never_looks_like_a_commit(tmp_path: Path) -> None:
    files = WindowsFiles(move=lambda source, target, flags: 0, last_error=lambda: 5)
    with pytest.raises(OSError) as error:
        files.replace(tmp_path / "source", tmp_path / "target")
    assert error.value.errno == 5
