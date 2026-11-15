"""Applying this module's own migration through E01's tracked mechanism (SYSTEM 5.7, 7).

E01's composition root runs the migrations of the modules it was built with. This module runs its own in the
STORAGE startup phase, through the same `migrate_tracked` (name, SHA-256, SQL), so a changed file is refused
loudly and a repeated start applies nothing twice. The migration is expand only: the previous release keeps
running against it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, cast

from crew_health.kernel.uow import UnitOfWorkFactory


class Migrator(Protocol):
    def migrate_tracked(self, name: str, sha256: str, sql: str) -> None: ...


class ReplicatedTables(Protocol):
    def register_replicated_table(self, table: str, *, record_type: str, key_column: str) -> None: ...


def apply_migration(factory: UnitOfWorkFactory, relative: str, *, name: str) -> None:
    raw = Path(__file__).resolve().parents[1].joinpath(relative).read_bytes()
    sql = raw.decode("utf-8")
    cast(Migrator, factory).migrate_tracked(name, hashlib.sha256(sql.encode()).hexdigest(), sql)


def replicated_tables_of(factory: UnitOfWorkFactory) -> ReplicatedTables:
    return cast(ReplicatedTables, factory)
