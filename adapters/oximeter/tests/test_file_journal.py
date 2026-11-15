"""Fault injection for atomic journal replacement and corrupt restart state."""

import errno
import hashlib
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.application.ports import (
    JournalCorrupt,
    JournalFull,
    JournalRecord,
    JournalUnavailable,
)


def record(key: str = "key") -> JournalRecord:
    return JournalRecord(key, "send", "pending", "req-1", b'{"value":97.5}', None)


@pytest.mark.trace("V16", "V35")
def test_journal_restart_preserves_bytes_sorted_records_and_replacement(tmp_path: Path) -> None:
    journal = FileJournal(tmp_path, max_entries=2, max_bytes=4096)
    journal.put(record("z"))
    journal.put(record("a"))
    journal.put(record("a"))
    restarted = FileJournal(tmp_path, max_entries=2, max_bytes=4096)
    assert restarted.records() == (record("a"), record("z"))
    assert restarted.get("z") == record("z")
    restarted.remove("z")
    restarted.remove("z")
    assert restarted.get("z") is None


@pytest.mark.trace("V35", "AGENTS 3.2")
def test_journal_enforces_capacity_without_losing_existing_records(tmp_path: Path) -> None:
    journal = FileJournal(tmp_path, max_entries=1, max_bytes=4096)
    journal.put(record("first"))
    with pytest.raises(JournalFull):
        journal.put(record("second"))
    assert journal.get("first") == record("first")
    small = FileJournal(tmp_path / "small", max_entries=1, max_bytes=1)
    with pytest.raises(JournalFull):
        small.put(record())
    assert small.records() == ()
    for count, size in ((0, 10), (1, 0), (10001, 10), (1, 67108865)):
        with pytest.raises(ValueError):
            FileJournal(tmp_path, max_entries=count, max_bytes=size)


@pytest.mark.trace("V35")
@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "[]",
        "{}",
        '{"key":1,"kind":"send","state":"pending","request_id":"r","body":"eA==","detail":null}',
        '{"key":"key","kind":"other","state":"pending","request_id":"r","body":"eA==","detail":null}',
        '{"key":"key","kind":"send","state":"pending","request_id":"r","body":"!!!","detail":null}',
        '{"key":"other","kind":"send","state":"pending","request_id":"r","body":"eA==","detail":null}',
    ],
)
def test_corrupt_journal_is_reported_and_kept(tmp_path: Path, document: str) -> None:
    path = tmp_path / (hashlib.sha256(b"key").hexdigest() + ".json")
    path.write_text(document, encoding="utf-8")
    journal = FileJournal(tmp_path, max_entries=1, max_bytes=4096)
    with pytest.raises(JournalCorrupt):
        journal.records()
    assert path.read_text(encoding="utf-8") == document


@pytest.mark.trace("V35", "AGENTS 3.2")
def test_oversize_or_too_many_restart_files_fail_loudly(tmp_path: Path) -> None:
    path = tmp_path / (hashlib.sha256(b"key").hexdigest() + ".json")
    path.write_bytes(b"x" * 100)
    journal = FileJournal(tmp_path, max_entries=1, max_bytes=10)
    with pytest.raises(JournalCorrupt):
        journal.get("key")
    (tmp_path / "another.json").write_text("{}", encoding="utf-8")
    with pytest.raises(JournalFull):
        journal.records()


@pytest.mark.trace("V35")
@pytest.mark.parametrize("operation", ["get", "records", "remove", "put", "mkdir"])
def test_filesystem_errors_are_typed_without_discarding_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    journal = FileJournal(tmp_path, max_entries=2, max_bytes=4096)
    journal.put(record())

    def unavailable(*args: object, **kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "denied")

    action: Callable[[], object]
    if operation == "mkdir":
        monkeypatch.setattr(Path, "mkdir", unavailable)
        action = partial(FileJournal, tmp_path / "new", max_entries=1, max_bytes=100)
    elif operation == "put":
        monkeypatch.setattr("tempfile.NamedTemporaryFile", unavailable)
        action = partial(journal.put, record("new"))
    elif operation == "remove":
        monkeypatch.setattr(Path, "unlink", unavailable)
        action = partial(journal.remove, "key")
    else:
        monkeypatch.setattr(Path, "open", unavailable)
        action = partial(journal.get, "key") if operation == "get" else journal.records
    with pytest.raises(JournalUnavailable) as failure:
        action()
    assert failure.value.cause == "io_error"


@pytest.mark.trace("V35", "AGENTS 1.6")
def test_atomic_replace_syncs_directory_and_rejects_corrupt_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    syncs: list[int] = []
    original = os.fsync

    def synced(fd: int) -> None:
        syncs.append(fd)
        original(fd)

    monkeypatch.setattr(os, "fsync", synced)
    journal = FileJournal(tmp_path, max_entries=2, max_bytes=4096)
    journal.put(record())
    assert len(syncs) == (1 if os.name == "nt" else 2)
    path = tmp_path / (hashlib.sha256(b"key").hexdigest() + ".json")
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(JournalCorrupt):
        journal.put(record())
    assert path.read_text(encoding="utf-8") == "{corrupt"


@pytest.mark.trace("V35")
def test_deeply_nested_json_is_typed_corruption(tmp_path: Path) -> None:
    path = tmp_path / (hashlib.sha256(b"key").hexdigest() + ".json")
    path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    journal = FileJournal(tmp_path, max_entries=1, max_bytes=10000)
    with pytest.raises(JournalCorrupt):
        journal.get("key")
