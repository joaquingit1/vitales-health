from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

type SqlValue = str | int | bytes | None


@dataclass(frozen=True, slots=True)
class StatementResult:
    affected_rows: int
    last_row_id: int | None


class StorageSession(Protocol):
    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult: ...
    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]: ...
