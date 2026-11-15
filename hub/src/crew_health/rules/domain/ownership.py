"""Ownership across nodes (E24 3.15, D4.4, INV-18).

The node that holds the input raises and owns the warning. Only the origin node changes it. A replica
is read only: it is listed with its origin and age, and only acknowledge and add_note may be proposed
for it (as local append records the origin applies). The engine never reads a replica.
"""

from __future__ import annotations

from datetime import datetime

from crew_health.rules.domain.errors import OriginMismatch
from crew_health.rules.domain.views import ReplicaInfo
from crew_health.rules.domain.vocabulary import WarningAction
from crew_health.rules.domain.warning import Warning

REPLICA_ACTIONS: tuple[WarningAction, ...] = (WarningAction.ACKNOWLEDGE, WarningAction.ADD_NOTE)


def is_origin(w: Warning, node_id: str) -> bool:
    return bool(node_id) and w.origin_node_id == node_id


def require_origin(w: Warning, node_id: str) -> None:
    if not is_origin(w, node_id):
        raise OriginMismatch("only the origin node changes a warning")


def replica_info(*, origin_node_id: str, now: datetime, origin_version_time: datetime) -> ReplicaInfo:
    """Origin node and age of a replicated warning. A version from the future shows age zero, never negative."""
    age = max(0, int((now - origin_version_time).total_seconds()))
    return ReplicaInfo(origin_node_id, age)
