"""List order (E24 `listWarnings`, C14): headline rank, then `raised_at`, then id, all ascending."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from crew_health.rules.domain.warning import Warning


def order_key(w: Warning) -> tuple[int, datetime, str]:
    return (w.headline.rank, w.raised_at, w.warning_id)


def ordered(warnings: Iterable[Warning]) -> tuple[Warning, ...]:
    return tuple(sorted(warnings, key=order_key))


def after(
    warnings: Iterable[Warning], cursor: tuple[int, datetime, str] | None, limit: int
) -> tuple[Warning, ...]:
    """The next `limit` warnings strictly after the cursor key, in list order."""
    if limit < 1:
        return ()
    items = ordered(warnings)
    if cursor is not None:
        items = tuple(w for w in items if order_key(w) > cursor)
    return items[:limit]
