from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.settings import HubSettings
from crew_health.platform.domain.errors import fail
from crew_health.platform.storage import SQLiteDatabase


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    name: str
    sql: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MigrationAuthorization:
    reader_versions: frozenset[str]
    writer_versions: frozenset[str]
    migrations: Mapping[str, str]
    phase: Literal["expand"]


class CompatibilityVerifier(Protocol):
    def verify(self, proof: bytes) -> MigrationAuthorization: ...


def require_expansion(sql: str) -> None:
    for statement in SQLiteDatabase._statements(sql):
        scrubbed = re.sub(r"'(?:''|[^'])*'|--[^\n]*|/\*[\s\S]*?\*/", " ", statement)
        if re.search(
            r"\b(?:DROP|VACUUM|ATTACH|DETACH|PRAGMA|VIRTUAL|REPLACE|load_extension|readfile|writefile)\b|\bDO\s+UPDATE\b",
            scrubbed,
            re.I,
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        if re.search(r"(?:;|\bBEGIN\b)\s*(?:DELETE|UPDATE)\b", scrubbed, re.I):
            raise fail(ErrorCode.CONFIG_INVALID)
        if re.match(r"\s*CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX|TRIGGER)\b", scrubbed, re.I):
            continue
        if re.match(
            r'\s*ALTER\s+TABLE\s+(?:"[^";]+"|\w+)\s+ADD\s+(?:COLUMN\s+)?(?:"[^";]+"|\w+)\b', scrubbed, re.I
        ):
            continue
        if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", scrubbed, re.I):
            continue
        raise fail(ErrorCode.CONFIG_INVALID)


class MigrationRunner:
    def __init__(
        self, *, database: SQLiteDatabase, settings: HubSettings, verifier: CompatibilityVerifier | None
    ) -> None:
        self.database, self.settings, self.verifier = database, settings, verifier

    def require_compatible(
        self, *, proof: bytes, executable_version: str, write: bool
    ) -> MigrationAuthorization:
        if self.verifier is None:
            raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
        policy = self.verifier.verify(proof)
        versions = policy.writer_versions if write else policy.reader_versions
        if (
            not executable_version
            or executable_version not in versions
            or policy.phase != "expand"
            or len(policy.migrations) > self.settings.integer("storage.max_rows")
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        with self.database.snapshot() as snapshot:
            installed = snapshot.connection.rows(
                "SELECT name,sha256 FROM platform_migration ORDER BY name",
                (),
                limit=self.settings.integer("storage.max_rows"),
            )
        for name, digest in installed:
            if (
                not isinstance(name, str)
                or not isinstance(digest, str)
                or policy.migrations.get(name) != digest
            ):
                raise fail(ErrorCode.CONFIG_INVALID)
        return policy

    def apply(self, migrations: Sequence[MigrationSpec], *, proof: bytes, executable_version: str) -> None:
        policy = self.require_compatible(proof=proof, executable_version=executable_version, write=True)
        if len(migrations) > self.settings.integer("storage.max_rows") or len(
            {value.name for value in migrations}
        ) != len(migrations):
            raise fail(ErrorCode.CONFIG_INVALID)
        for migration in migrations:
            if (
                not migration.name
                or len(migration.name.encode()) > 128
                or len(migration.sql.encode()) > self.settings.integer("storage.max_statement_bytes")
                or hashlib.sha256(migration.sql.encode()).hexdigest() != migration.sha256
                or policy.migrations.get(migration.name) != migration.sha256
            ):
                raise fail(ErrorCode.CONFIG_INVALID)
            require_expansion(migration.sql)
        for migration in migrations:
            self.database.migrate_tracked(migration.name, migration.sha256, migration.sql)
