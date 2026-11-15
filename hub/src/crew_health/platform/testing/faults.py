from __future__ import annotations

from collections.abc import Mapping

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.storage import SqlValue, StatementResult, StorageSession


class StorageFaults:
    """A bounded port decorator for deterministic before/after statement faults."""

    def __init__(
        self,
        session: StorageSession,
        *,
        before: Mapping[int, StorageUnavailable],
        after: Mapping[int, StorageUnavailable],
        maximum_calls: int,
    ) -> None:
        if (
            maximum_calls < 1
            or len(before) + len(after) > maximum_calls
            or any(not 1 <= value <= maximum_calls for value in (*before, *after))
        ):
            raise ValueError("invalid fault plan")
        self.session, self.before, self.after, self.maximum_calls = (
            session,
            dict(before),
            dict(after),
            maximum_calls,
        )
        self.calls = 0

    def _begin(self) -> None:
        if self.calls >= self.maximum_calls:
            raise StorageUnavailable("io_error")
        self.calls += 1
        error = self.before.get(self.calls)
        if error is not None:
            raise error

    def _end(self) -> None:
        error = self.after.get(self.calls)
        if error is not None:
            raise error

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        self._begin()
        result = self.session.execute(sql, parameters)
        self._end()
        return result

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        self._begin()
        result = self.session.rows(sql, parameters, limit=limit)
        self._end()
        return result
