from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters.documents import decode_json
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.windows_files import WindowsFiles, load_windows
from crew_health_bench.domain.documents import object_map
from crew_health_bench.domain.errors import BenchError


def _lock_file(stream: BinaryIO, *, unlock: bool) -> None:
    if sys.platform == "win32":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class FileArtifactStore:
    def __init__(self, root: Path, *, max_line_bytes: int, max_records: int) -> None:
        if max_line_bytes < 1 or max_records < 1:
            raise BenchError("case_invalid")
        self.root = root.absolute()
        self.max_line_bytes, self.max_records = max_line_bytes, max_records
        self._lock: BinaryIO | None = None
        self._windows: WindowsFiles | None = None

    def __enter__(self) -> FileArtifactStore:
        if sys.platform == "win32":
            try:
                self._windows = load_windows()
            except OSError as error:
                raise BenchError("target_unreadable", exit_code=6) from error
        if self._lock is not None:
            raise BenchError("interrupted", exit_code=6)
        try:
            vault_path(self.root.parent, self.root.name)
            self.root.mkdir(parents=True, exist_ok=True)
            if self._windows is None:
                _sync_directory(self.root.parent)
            lock = vault_path(self.root, ".lock").open("a+b")
            try:
                if lock.seek(0, os.SEEK_END) == 0:
                    lock.write(b"\0")
                    lock.flush()
                _lock_file(lock, unlock=False)
            except OSError:
                lock.close()
                raise
            self._lock = lock
            return self
        except OSError as error:
            raise BenchError("interrupted", exit_code=6) from error

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            try:
                _lock_file(lock, unlock=True)
            except OSError as error:
                raise BenchError("target_unreadable", exit_code=6) from error
            finally:
                lock.close()

    def _path(self, run_id: str, stream: str) -> Path:
        if (
            stream not in ("state", "generations", "grades", "preregistration", "official_grades")
            or not run_id
            or "/" in run_id
            or "\\" in run_id
        ):
            raise BenchError("vault_path_escape", exit_code=5)
        if self._lock is None:
            raise BenchError("interrupted", exit_code=6)
        return vault_path(self.root, run_id + "/" + stream + ".jsonl")

    def _read(self, path: Path) -> tuple[tuple[Mapping[str, object], ...], str]:
        if not path.exists():
            return (), ""
        records: list[Mapping[str, object]] = []
        previous = ""
        with path.open("r+b") as stream:
            for seq in range(1, self.max_records + 2):
                offset = stream.tell()
                line = stream.readline(self.max_line_bytes + 1)
                if not line:
                    return tuple(records), previous
                if len(line) > self.max_line_bytes:
                    raise BenchError("integrity", exit_code=6)
                if not line.endswith(b"\n"):
                    stream.truncate(offset)
                    stream.flush()
                    os.fsync(stream.fileno())
                    return tuple(records), previous
                try:
                    row = decode_json(line)
                except BenchError as error:
                    raise BenchError("integrity", exit_code=6) from error
                digest = row.pop("sha256", None)
                if (
                    seq > self.max_records
                    or row.keys() != {"seq", "record", "previous_sha256"}
                    or type(row["seq"]) is not int
                    or row["seq"] != seq
                    or row["previous_sha256"] != previous
                    or digest != sha256(canonical_json(row)).hexdigest()
                ):
                    raise BenchError("integrity", exit_code=6)
                records.append(object_map(row["record"]))
                if not isinstance(digest, str):
                    raise BenchError("integrity", exit_code=6)
                previous = digest
        raise BenchError("integrity", exit_code=6)

    def read_valid(self, run_id: str, stream: str) -> Sequence[Mapping[str, object]]:
        path = self._path(run_id, stream)
        try:
            return self._read(path)[0]
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        except (ValueError, UnicodeError, RecursionError) as error:
            raise BenchError("integrity", exit_code=6) from error

    def append(self, run_id: str, stream: str, record: Mapping[str, object]) -> None:
        path = self._path(run_id, stream)
        try:
            records = self.read_valid(run_id, stream)
            if len(records) >= self.max_records:
                raise BenchError("integrity", exit_code=6)
            previous = self._read(path)[1]
            row = {"seq": len(records) + 1, "record": dict(record), "previous_sha256": previous}
            data = canonical_json({**row, "sha256": sha256(canonical_json(row)).hexdigest()}) + b"\n"
            if len(data) > self.max_line_bytes:
                raise BenchError("integrity", exit_code=6)
            path.parent.mkdir(exist_ok=True)
            if self._windows is not None and not path.exists():
                self.write_atomic(path.relative_to(self.root), data)
                return
            with path.open("ab") as destination:
                if destination.write(data) != len(data):
                    raise BenchError("target_unreadable", exit_code=6)
                destination.flush()
                os.fsync(destination.fileno())
            self._sync_ancestors(path.parent)
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error

        except (ValueError, UnicodeError, RecursionError) as error:
            raise BenchError("integrity", exit_code=6) from error

    def write_atomic(self, path: Path, data: bytes) -> None:
        if self._lock is None or path.is_absolute():
            raise BenchError("vault_path_escape", exit_code=5)
        destination = vault_path(self.root, path.as_posix())
        temporary = vault_path(self.root, path.as_posix() + ".tmp")
        if len(data) > self.max_line_bytes * self.max_records:
            raise BenchError("integrity", exit_code=6)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                if stream.write(data) != len(data):
                    raise BenchError("target_unreadable", exit_code=6)
                stream.flush()
                os.fsync(stream.fileno())
            if self._windows is None:
                os.replace(temporary, destination)
            else:
                self._windows.replace(temporary, destination)
            self._sync_ancestors(destination.parent)
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error

    def _sync_ancestors(self, directory: Path) -> None:
        current = directory
        for _ in range(len(directory.relative_to(self.root).parts) + 1):
            if self._windows is None:
                _sync_directory(current)
            else:
                self._windows.sync_directory(current)
            if current == self.root:
                return
            current = current.parent
        raise BenchError("vault_path_escape", exit_code=5)
