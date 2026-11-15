"""P29-2 interim: inclusive delivery order, bounded or a loud failure."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from crew_health.catchup.domain.errors import LogTooLarge


class DeliveredRow(Protocol):
    @property
    def delivered_at(self) -> datetime | None: ...
    @property
    def ref_id(self) -> str: ...


def select_items[T: DeliveredRow](rows: Sequence[T], since: datetime | None, limit: int) -> tuple[T, ...]:
    if limit < 1:
        raise ValueError("limit must be positive")
    selected: list[T] = []
    for row in rows:
        if row.delivered_at is not None and (since is None or row.delivered_at >= since):
            if len(selected) == limit:
                raise LogTooLarge()
            selected.append(row)
    return tuple(sorted(selected, key=lambda row: (row.delivered_at, row.ref_id)))
