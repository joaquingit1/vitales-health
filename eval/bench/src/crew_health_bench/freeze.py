from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.domain.errors import BenchError


@dataclass(frozen=True, slots=True)
class FrozenManifest:
    root: str
    files: Mapping[str, str]
    frozen_at: datetime
    frozen_by: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


@dataclass(frozen=True, slots=True)
class FreezeFinding:
    path: str
    expected_sha256: str
    actual_sha256: str | None


def _digest(files: Mapping[str, str]) -> str:
    return hashlib.sha256(canonical_json(sorted(files.items()))).hexdigest()


class FreezeLibrary:
    def __init__(
        self, *, repository_root: Path, clock: Clock, frozen_by: str, max_files: int, max_file_bytes: int
    ) -> None:
        if not frozen_by or max_files < 1 or max_file_bytes < 1:
            raise BenchError("case_invalid")
        self.repository_root = repository_root.absolute()
        self.clock, self.frozen_by = clock, frozen_by
        self.max_files, self.max_file_bytes = max_files, max_file_bytes

    def _root(self, root: Path) -> Path:
        absolute = root.absolute()
        if not absolute.is_relative_to(self.repository_root):
            raise BenchError("vault_path_escape", exit_code=5)
        vault_path(absolute, "FROZEN.sha256")
        return absolute

    def _hash(self, path: Path) -> str:
        with path.open("rb") as source:
            data = source.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise BenchError("target_unreadable", exit_code=6)
        return hashlib.sha256(data).hexdigest()

    def _files(self, root: Path, include: Sequence[str], exclude: Sequence[str]) -> Mapping[str, str]:
        if not include or any(
            PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts for p in (*include, *exclude)
        ):
            raise BenchError("vault_path_escape", exit_code=5)
        pending = [root]
        files: dict[str, str] = {}
        visited = 0
        for _ in range(self.max_files + 1):
            if not pending:
                break
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > self.max_files:
                        raise BenchError("target_unreadable", exit_code=6)
                    relative = str(Path(entry.path).relative_to(root))
                    if any(character in relative for character in ("\n", "\r", "\\")):
                        raise BenchError("case_invalid")
                    if any(fnmatchcase(relative, pattern) for pattern in exclude):
                        continue
                    if entry.is_symlink():
                        raise BenchError("vault_path_escape", exit_code=5)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if any(fnmatchcase(relative, pattern) for pattern in include):
                            files[relative] = self._hash(Path(entry.path))
                    else:
                        raise BenchError("target_unreadable", exit_code=6)
        if not files:
            raise BenchError("case_invalid")
        return dict(sorted(files.items()))

    def freeze_tree(self, root: Path, *, include: Sequence[str], exclude: Sequence[str]) -> FrozenManifest:
        root = self._root(root)
        try:
            files = self._files(root, include, exclude)
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        return FrozenManifest(
            str(root.relative_to(self.repository_root)),
            files,
            self.clock.now(),
            self.frozen_by,
            _digest(files),
        )

    def verify_tree(self, root: Path, manifest: FrozenManifest) -> Sequence[FreezeFinding]:
        root = self._root(root)
        if (
            str(root.relative_to(self.repository_root)) != manifest.root
            or len(manifest.files) > self.max_files
            or _digest(manifest.files) != manifest.manifest_sha256
        ):
            raise BenchError("drift")
        findings: list[FreezeFinding] = []
        for relative, expected in sorted(manifest.files.items()):
            path = vault_path(root, relative)
            try:
                actual = self._hash(path)
            except FileNotFoundError:
                actual = None
            except OSError as error:
                raise BenchError("target_unreadable", exit_code=6) from error
            if actual != expected:
                findings.append(FreezeFinding(relative, expected, actual))
        return tuple(findings)


def sha256sum(manifest: FrozenManifest) -> bytes:
    return "".join(f"{digest}  {path}\n" for path, digest in sorted(manifest.files.items())).encode()
