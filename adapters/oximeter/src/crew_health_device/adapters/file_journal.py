"""Bounded atomic journal shared by acquisition and delivery across restarts."""

import base64
import binascii
import errno
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from itertools import islice
from pathlib import Path
from threading import RLock

from crew_health_device.application.ports import (
    JournalCorrupt,
    JournalFull,
    JournalRecord,
    JournalUnavailable,
)


class FileJournal:
    def __init__(self, directory: Path, *, max_entries: int, max_bytes: int) -> None:
        if not 1 <= max_entries <= 10000 or not 1 <= max_bytes <= 67108864:
            raise ValueError("invalid_journal_bound")
        self._directory = directory
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = RLock()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._unavailable(exc) from exc

    @staticmethod
    def _unavailable(exc: OSError) -> JournalUnavailable:
        return JournalUnavailable("disk_full" if exc.errno == errno.ENOSPC else "io_error")

    def _path(self, key: str) -> Path:
        return self._directory / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def _paths(self) -> tuple[Path, ...]:
        paths = tuple(islice(self._directory.glob("*.json"), self._max_entries + 1))
        if len(paths) > self._max_entries:
            raise JournalFull()
        return tuple(sorted(paths))

    def _read(self, path: Path) -> JournalRecord:
        try:
            with path.open("rb") as stream:
                data = stream.read(self._max_bytes + 1)
            if len(data) > self._max_bytes:
                raise JournalCorrupt(path.stem)
            doc: object = json.loads(data)
            if not isinstance(doc, dict) or set(doc) != {
                "key",
                "kind",
                "state",
                "request_id",
                "body",
                "detail",
            }:
                raise ValueError("record_shape")
            if any(not isinstance(doc[k], str) for k in ("key", "kind", "state", "request_id", "body")):
                raise ValueError("record_types")
            if doc["kind"] not in ("acquisition", "send") or not isinstance(doc["detail"], str | type(None)):
                raise ValueError("record_kind")
            record = JournalRecord(
                doc["key"],
                doc["kind"],
                doc["state"],
                doc["request_id"],
                base64.b64decode(doc["body"], validate=True),
                doc["detail"],
            )
            if self._path(record.key) != path:
                raise ValueError("key_mismatch")
            return record
        except (ValueError, TypeError, KeyError, RecursionError, binascii.Error) as exc:
            raise JournalCorrupt(path.stem) from exc

    def put(self, record: JournalRecord) -> None:
        with self._lock:
            path = self._path(record.key)
            doc = asdict(record)
            doc["body"] = base64.b64encode(record.body).decode("ascii")
            data = json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            try:
                paths = self._paths()
                if path in paths:
                    self._read(path)
                if path not in paths and len(paths) >= self._max_entries:
                    raise JournalFull()
                size = sum(p.stat().st_size for p in paths if p != path) + len(data)
                if size > self._max_bytes:
                    raise JournalFull()
                self._atomic_write(path, data)
            except OSError as exc:
                raise self._unavailable(exc) from exc

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self._directory, suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _sync_directory(self) -> None:
        # Windows does not support opening a directory for os.fsync.
        if os.name == "nt":
            return
        descriptor = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def get(self, key: str) -> JournalRecord | None:
        with self._lock:
            try:
                path = self._path(key)
                return self._read(path) if path.exists() else None
            except OSError as exc:
                raise self._unavailable(exc) from exc

    def records(self) -> tuple[JournalRecord, ...]:
        with self._lock:
            try:
                return tuple(sorted((self._read(p) for p in self._paths()), key=lambda r: r.key))
            except OSError as exc:
                raise self._unavailable(exc) from exc

    def remove(self, key: str) -> None:
        with self._lock:
            try:
                self._path(key).unlink(missing_ok=True)
                self._sync_directory()
            except OSError as exc:
                raise self._unavailable(exc) from exc
