"""Warnings of other nodes (E24 3.15, D4.4): kept apart, read only, never evaluated.

A replica lands in `rules_warning_replica` by revision; an older or repeated version changes nothing. The engine
never reads that table, so a local re-evaluation can neither clear nor raise a replicated warning, and a local raise
for the same definition and scope is a separate warning (never merged). The origin's version time is stored as the
physical part of its hybrid clock in ISO form.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from crew_health.kernel.uow import UnitOfWork
from crew_health.rules.application import codec
from crew_health.rules.application.core import Runtime, note_audit
from crew_health.rules.domain.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class ReplicatedWarning:
    warning_id: str
    origin_node_id: str
    origin_at: datetime
    revision: int
    scope: str
    person_id: str | None
    warning_json: str


def apply_replica(rt: Runtime, uow: UnitOfWork, entry: ReplicatedWarning) -> bool:
    """True when the version was new. A version claiming this node as its origin is refused: ownership never moves."""
    if entry.origin_node_id == rt.node_id(uow):
        raise InvariantViolation("a replica never names this node as its origin")
    if entry.scope == "person" and entry.person_id is None:
        raise InvariantViolation("a person warning replica names its person")
    stored = rt.replicas.apply(
        uow.connection,
        warning_id=entry.warning_id,
        origin_node_id=entry.origin_node_id,
        origin_hlc=codec.instant(entry.origin_at),
        revision=entry.revision,
        scope=entry.scope,
        person_id=entry.person_id,
        warning_json=entry.warning_json,
        now=uow.now,
    )
    if stored:
        note_audit(rt, uow, "warning.replica_applied", {"warning": entry.warning_id}, {})
    return stored
