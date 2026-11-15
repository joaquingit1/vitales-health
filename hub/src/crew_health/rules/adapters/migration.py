"""Applying this module's own migration through E01's tracked mechanism (SYSTEM 5.7, 7). Expand only."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, cast

from crew_health.kernel.uow import UnitOfWorkFactory


class _Migrator(Protocol):
    def migrate_tracked(self, name: str, sha256: str, sql: str) -> None: ...


def apply_migration(factory: UnitOfWorkFactory, relative: str, *, name: str) -> None:
    sql = Path(__file__).resolve().parents[1].joinpath(relative).read_bytes().decode("utf-8")
    cast(_Migrator, factory).migrate_tracked(name, hashlib.sha256(sql.encode()).hexdigest(), sql)
