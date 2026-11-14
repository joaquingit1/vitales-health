from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot
from crew_health.platform.adapters.audit import SQLiteAuditVerifier
from crew_health.platform.application.recovery import CaptureFacts
from crew_health.platform.domain.errors import fail
from crew_health.platform.storage import SQLiteDatabase, inspect_sqlite, snapshot_file


def digest_file(path: Path, *, chunk_bytes: int) -> str:
    digest = hashlib.sha256()
    try:
        if not path.is_file() or path.is_symlink() or chunk_bytes < 1:
            raise StorageUnavailable("corrupt")
        size = path.stat().st_size
        with path.open("rb") as source:
            for _ in range((size + chunk_bytes - 1) // chunk_bytes):
                block = source.read(chunk_bytes)
                if not block:
                    raise StorageUnavailable("corrupt")
                digest.update(block)
            if source.read(1):
                raise StorageUnavailable("corrupt")
    except OSError as error:
        raise StorageUnavailable("io_error") from error
    return digest.hexdigest()


def _integer(value: SqlValue) -> int:
    if type(value) is not int or value < 0:
        raise StorageUnavailable("corrupt")
    return value


class CapturedSnapshots:
    def __init__(self, path: Path, *, database: SQLiteDatabase, release: ReleaseHandle) -> None:
        self.path, self.database, self.release = path, database, release

    def snapshot(self) -> AbstractContextManager[ReadSnapshot]:
        return snapshot_file(
            self.path, settings=self.database.settings, release=self.release, clock=self.database.clock
        )


class RecoveryRepository:
    def __init__(
        self, *, database: SQLiteDatabase, identity: Callable[[ReadSnapshot], tuple[str, str]] | None = None
    ) -> None:
        self.database, self.identity = database, identity

    @staticmethod
    def prepare(destination: Path) -> tuple[Path, Path]:
        try:
            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError as error:
            raise fail(ErrorCode.INVALID_TRANSITION) from error
        except OSError as error:
            cause: Literal["disk_full", "io_error"] = (
                "disk_full" if error.errno == errno.ENOSPC else "io_error"
            )
            raise StorageUnavailable(cause) from error
        return destination / "hub.sqlite", destination / "private.sqlite"

    def require_unchanged(self, path: Path, digest: str) -> None:
        if (
            digest_file(path, chunk_bytes=self.database.settings.integer("storage.max_statement_bytes"))
            != digest
        ):
            raise StorageUnavailable("corrupt")

    def _receipts(self, snapshot: ReadSnapshot) -> str:
        digest = hashlib.sha256()
        actor, key = "", ""
        limit = self.database.settings.integer("storage.max_rows")
        for _ in range(limit):
            rows = snapshot.connection.rows(
                "SELECT actor_ref,command_key,identity,receipt,status,resource_ref,result_code,safe_body FROM platform_command_ledger WHERE actor_ref>? OR (actor_ref=? AND command_key>?) ORDER BY actor_ref,command_key LIMIT ?",
                (actor, actor, key, limit),
                limit=limit,
            )
            for row in rows:
                digest.update(
                    canonical_json([value.hex() if isinstance(value, bytes) else value for value in row])
                )
            if len(rows) < limit:
                return digest.hexdigest()
            actor, key = str(rows[-1][0]), str(rows[-1][1])
        raise StorageUnavailable("corrupt")

    def facts(self, hub_path: Path, *, private_sha256: str, vault_generation: int) -> CaptureFacts:
        inspect_sqlite(hub_path)
        with self.database.snapshot() as live:
            release = live.release
        with snapshot_file(
            hub_path, settings=self.database.settings, release=release, clock=self.database.clock
        ) as view:
            identity = view.connection.rows(
                "SELECT node_id,incarnation FROM platform_identity WHERE singleton=1", (), limit=1
            )
            if identity != ((self.database.origin_node_id, self.database.origin_incarnation),):
                raise StorageUnavailable("corrupt")
            refs = view.connection.rows("SELECT MAX(seq) FROM platform_private_reference", (), limit=1)
            watermark = 0 if refs[0][0] is None else _integer(refs[0][0])
            audit = view.connection.rows(
                "SELECT seq,entry_hash FROM platform_audit ORDER BY seq DESC LIMIT 1", (), limit=1
            )
            audit_seq, audit_hash = (_integer(audit[0][0]), str(audit[0][1])) if audit else (0, "0" * 64)
            migrations = view.connection.rows(
                "SELECT name,sha256 FROM platform_migration ORDER BY name",
                (),
                limit=self.database.settings.integer("storage.max_rows"),
            )
            schema = hashlib.sha256(canonical_json(migrations)).hexdigest()
            namespace, release_id = self.identity(view) if self.identity is not None else (None, None)
            receipts = self._receipts(view)
        verified = SQLiteAuditVerifier(
            CapturedSnapshots(hub_path, database=self.database, release=release),
            page_size=self.database.settings.integer("storage.max_rows"),
        ).verify(expected_head_seq=audit_seq, expected_head_hash=audit_hash)
        if not verified.ok:
            raise StorageUnavailable("corrupt")
        return CaptureFacts(
            digest_file(hub_path, chunk_bytes=self.database.settings.integer("storage.max_statement_bytes")),
            private_sha256,
            watermark,
            audit_seq,
            audit_hash,
            receipts,
            schema,
            vault_generation,
            self.database.origin_node_id,
            self.database.origin_incarnation,
            namespace,
            release_id,
        )


class DiskReserve:
    def __init__(self, path: Path, *, settings: HubSettings, clock: Clock) -> None:
        self.path, self.settings, self.clock = path, settings, clock

    def allocate(self) -> None:
        size = self.settings.integer("storage.reserve_mb") * 1024 * 1024
        if self.path.is_symlink():
            raise StorageUnavailable("corrupt")
        if self.path.exists():
            if self.path.stat().st_size != size:
                raise StorageUnavailable("corrupt")
            return
        chunk_bytes = min(size, self.settings.integer("storage.max_statement_bytes"))
        deadline = self.clock.monotonic_ns() + self.settings.integer("backup.timeout_ms") * 1000000
        try:
            with self.path.open("xb") as reserve:
                block = bytes(chunk_bytes)
                for offset in range(0, size, chunk_bytes):
                    if self.clock.monotonic_ns() > deadline:
                        raise StorageUnavailable("io_error")
                    expected = min(chunk_bytes, size - offset)
                    if reserve.write(block[:expected]) != expected:
                        raise StorageUnavailable("io_error")
                reserve.flush()
                os.fsync(reserve.fileno())
        except OSError as error:
            cause: Literal["disk_full", "io_error"] = (
                "disk_full" if error.errno == errno.ENOSPC else "io_error"
            )
            raise StorageUnavailable(cause) from error

    def release(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise StorageUnavailable("io_error") from error
