from __future__ import annotations

import os
from pathlib import Path

from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.domain.errors import BenchError


def bounded_files(root: Path, *, max_files: int) -> tuple[Path, ...]:
    if max_files < 1:
        raise BenchError("case_invalid")
    root = root.absolute()
    vault_path(root.parent, root.name)
    if root.is_file():
        return (root,)
    if not root.is_dir():
        raise BenchError("target_unreadable", instance=str(root), exit_code=6)
    pending = [root]
    paths: list[Path] = []
    visited = 0
    try:
        for _ in range(max_files + 1):
            if not pending:
                break
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    visited += 1
                    if visited > max_files:
                        raise BenchError("target_unreadable", exit_code=6)
                    if entry.is_symlink():
                        raise BenchError("vault_path_escape", exit_code=5)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        paths.append(Path(entry.path))
                    else:
                        raise BenchError("target_unreadable", exit_code=6)
        if pending or not paths:
            raise BenchError("target_unreadable", exit_code=6)
        return tuple(sorted(paths))
    except OSError as error:
        raise BenchError("target_unreadable", exit_code=6) from error
