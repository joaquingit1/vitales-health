"""The bounded durable send queue (E23 5.2, AGENTS rule 6).

One SQLite file per adapter, journal WAL, synchronous FULL. In ONE transaction an enqueue commits the row, its
per-channel `adapter_sequence` and the source progress, so a crash leaves either all three or none: no accepted
row is lost and no sequence is reused. A batch is frozen once, with its batch id, idempotency key and body bytes,
and every retry, after any restart, resends exactly those bytes. A batch leaves the queue only on a validated
per-index result or a matching accepted_command receipt; a rejected row is kept as bounded diagnostics, never
dropped. A full queue raises QueueFull and the source pauses; nothing is discarded.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crew_health_adapter_habitat.errors import QueueFull, QueueStorageError
from crew_health_adapter_habitat.ports import Settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sequences (channel_id TEXT PRIMARY KEY, next_seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS progress (feed_id TEXT PRIMARY KEY, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, sequence INTEGER NOT NULL,
  body TEXT NOT NULL, UNIQUE (channel_id, sequence));
CREATE TABLE IF NOT EXISTS batches (
  ordinal INTEGER PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE, key TEXT NOT NULL, body BLOB NOT NULL,
  row_count INTEGER NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rejected (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, batch_id TEXT, idx INTEGER,
  channel_id TEXT, sequence INTEGER, code TEXT NOT NULL, raw TEXT);
CREATE TABLE IF NOT EXISTS counts (code TEXT PRIMARY KEY, n INTEGER NOT NULL);
"""


@dataclass(frozen=True, slots=True)
class FrozenBatch:
    batch_id: str
    key: str
    body: bytes
    row_count: int
    state: str


@dataclass(frozen=True, slots=True)
class RejectedRow:
    index: int
    code: str


class DurableQueue:
    def __init__(self, path: Path, settings: Settings, adapter_id: str) -> None:
        self.path, self.settings, self.adapter_id = path, settings, adapter_id
        try:
            self.db = sqlite3.connect(path, isolation_level=None, timeout=settings.http_timeout_s)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise QueueStorageError("queue file cannot be opened") from error

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")
        except sqlite3.Error as error:
            raise QueueStorageError(_safe(error)) from error

    def _scalar(self, sql: str, *args: object) -> int:
        try:
            row = self.db.execute(sql, args).fetchone()
        except sqlite3.Error as error:
            raise QueueStorageError(_safe(error)) from error
        return int(row[0]) if row else 0

    def depth(self) -> int:
        """Rows held: waiting plus inside any unacknowledged batch."""
        return self._scalar("SELECT count(*) FROM rows") + self._scalar(
            "SELECT coalesce(sum(row_count),0) FROM batches"
        )

    def is_full(self) -> bool:
        return self.depth() >= self.settings.queue_max

    def progress(self, feed_id: str) -> int:
        return self._scalar("SELECT position FROM progress WHERE feed_id=?", feed_id)

    def rewind(self, feed_id: str) -> None:
        """An explicit restart of a source from its top. Sequences keep counting; they never go back."""
        with self._txn() as db:
            db.execute("DELETE FROM progress WHERE feed_id=?", (feed_id,))

    def meta(self, key: str, make: Callable[[], str]) -> str:
        with self._txn() as db:
            row = db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            if row is not None:
                return str(row[0])
            value = make()
            db.execute("INSERT INTO meta (k, v) VALUES (?, ?)", (key, value))
            return value

    def enqueue(
        self,
        channel_id: str,
        make_row: Callable[[int], Mapping[str, Any]],
        *,
        feed_id: str | None,
        position: int | None,
    ) -> int | None:
        """Commit row, sequence and progress together. Returns the sequence, or None when the row is oversized
        (kept as a rejection, progress still advances, no sequence consumed). Raises QueueFull when at the bound."""
        if self.is_full():
            raise QueueFull(f"{self.depth()} rows")
        with self._txn() as db:
            row = db.execute("SELECT next_seq FROM sequences WHERE channel_id=?", (channel_id,)).fetchone()
            sequence = 0 if row is None else int(row[0])
            body = json.dumps(make_row(sequence), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(body.encode("utf-8")) > self.settings.row_max_bytes:
                self._reject(db, "local", None, None, channel_id, None, "ROW_TOO_LARGE", None)
                self._advance(db, feed_id, position)
                return None
            db.execute(
                "INSERT OR REPLACE INTO sequences (channel_id, next_seq) VALUES (?, ?)",
                (channel_id, sequence + 1),
            )
            db.execute(
                "INSERT INTO rows (channel_id, sequence, body) VALUES (?, ?, ?)", (channel_id, sequence, body)
            )
            self._advance(db, feed_id, position)
            return sequence

    def _advance(self, db: sqlite3.Connection, feed_id: str | None, position: int | None) -> None:
        if feed_id is not None and position is not None:
            db.execute(
                "INSERT OR REPLACE INTO progress (feed_id, position) VALUES (?, ?)", (feed_id, position)
            )

    def skip(self, feed_id: str, position: int, code: str) -> None:
        """A source row that cannot be parsed: counted by code, progress advances, nothing is substituted."""
        with self._txn() as db:
            db.execute(
                "INSERT INTO counts (code, n) VALUES (?, 1) ON CONFLICT(code) DO UPDATE SET n = n + 1",
                (code,),
            )
            self._advance(db, feed_id, position)

    def counts(self) -> dict[str, int]:
        try:
            return {str(c): int(n) for c, n in self.db.execute("SELECT code, n FROM counts ORDER BY code")}
        except sqlite3.Error as error:
            raise QueueStorageError(_safe(error)) from error

    def _reject(
        self,
        db: sqlite3.Connection,
        source: str,
        batch_id: str | None,
        index: int | None,
        channel_id: str | None,
        sequence: int | None,
        code: str,
        raw: str | None,
    ) -> None:
        kept = int(db.execute("SELECT count(*) FROM rejected").fetchone()[0])
        db.execute(
            "INSERT INTO counts (code, n) VALUES (?, 1) ON CONFLICT(code) DO UPDATE SET n = n + 1", (code,)
        )
        if kept < self.settings.batch_max_readings:
            db.execute(
                "INSERT INTO rejected (source, batch_id, idx, channel_id, sequence, code, raw) VALUES (?,?,?,?,?,?,?)",
                (source, batch_id, index, channel_id, sequence, code, raw),
            )

    def next_batch(self) -> FrozenBatch | None:
        """The oldest unacknowledged batch, or a new one frozen from the oldest waiting rows."""
        existing = self._oldest()
        if existing is not None:
            return existing
        with self._txn() as db:
            rows = db.execute(
                "SELECT id, body FROM rows ORDER BY id LIMIT ?", (self.settings.batch_max_readings,)
            ).fetchall()
            if not rows:
                return None
            ordinal = int(db.execute("SELECT coalesce(max(ordinal),0)+1 FROM batches").fetchone()[0])
            number = self._serial(db)
            batch_id = f"bat_{self.adapter_id}_{number}"
            readings = ",".join(str(r[1]) for r in rows)
            body = ('{"batch_id":' + json.dumps(batch_id) + ',"readings":[' + readings + "]}").encode("utf-8")
            db.execute(
                "INSERT INTO batches (ordinal, batch_id, key, body, row_count, state) VALUES (?,?,?,?,?, 'open')",
                (ordinal, batch_id, "key_" + batch_id, body, len(rows)),
            )
            db.executemany("DELETE FROM rows WHERE id=?", [(r[0],) for r in rows])
        return self._oldest()

    def _serial(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT v FROM meta WHERE k='batch_serial'").fetchone()
        number = (0 if row is None else int(row[0])) + 1
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('batch_serial', ?)", (str(number),))
        return number

    def _oldest(self) -> FrozenBatch | None:
        try:
            row = self.db.execute(
                "SELECT batch_id, key, body, row_count, state FROM batches ORDER BY ordinal LIMIT 1"
            ).fetchone()
        except sqlite3.Error as error:
            raise QueueStorageError(_safe(error)) from error
        return None if row is None else FrozenBatch(row[0], row[1], bytes(row[2]), int(row[3]), row[4])

    def note_attempt(self, batch_id: str) -> None:
        with self._txn() as db:
            db.execute("UPDATE batches SET attempts = attempts + 1 WHERE batch_id=?", (batch_id,))

    def quarantine(self, batch_id: str, code: str) -> None:
        """A batch the hub refused or contradicted: it stays queued under its key and body, flagged."""
        with self._txn() as db:
            db.execute("UPDATE batches SET state=? WHERE batch_id=?", ("quarantined:" + code, batch_id))

    def acknowledge(
        self, batch: FrozenBatch, rejected: tuple[RejectedRow, ...], *, receipt_only: bool = False
    ) -> None:
        """Remove an accepted batch. Rejected rows stay as diagnostics with their index and code."""
        readings = json.loads(batch.body)["readings"]
        with self._txn() as db:
            for item in rejected:
                row = readings[item.index]
                self._reject(
                    db,
                    "hub",
                    batch.batch_id,
                    item.index,
                    row["channel_id"],
                    row["adapter_sequence"],
                    item.code,
                    json.dumps(row, sort_keys=True),
                )
            if receipt_only:
                db.execute(
                    "INSERT INTO counts (code, n) VALUES ('RECEIPT_ONLY', 1) ON CONFLICT(code) DO UPDATE SET n = n + 1"
                )
            db.execute("DELETE FROM batches WHERE batch_id=?", (batch.batch_id,))

    def rejected_rows(self) -> list[tuple[str, str | None, int | None, str | None, int | None, str]]:
        found = self.db.execute(
            "SELECT source, batch_id, idx, channel_id, sequence, code FROM rejected ORDER BY id"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in found]

    def next_sequence(self, channel_id: str) -> int:
        return self._scalar("SELECT next_seq FROM sequences WHERE channel_id=?", channel_id)


def _safe(error: sqlite3.Error) -> str:
    text = str(error)
    return "disk full" if "full" in text else type(error).__name__
