from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from crew_health.kernel.clock import require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import _FrozenMapping

if TYPE_CHECKING:
    from crew_health.kernel.uow import Actor


@dataclass(frozen=True, slots=True)
class AuditEntry:
    event_id: str
    event_type: str
    event_version: int
    actor: Actor
    refs: Mapping[str, str]
    correlation_id: str
    causation_id: str | None
    safe_metadata: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "refs", _FrozenMapping(self.refs))
        object.__setattr__(self, "safe_metadata", _FrozenMapping(self.safe_metadata))


@dataclass(frozen=True, slots=True)
class AuditEnvelope:
    origin_node_id: str
    origin_incarnation: str
    audit_seq: int
    at: datetime
    entry: AuditEntry
    prev_hash: str

    def __post_init__(self) -> None:
        require_utc(self.at)


@dataclass(frozen=True, slots=True)
class AuditVerification:
    ok: bool
    first_bad_seq: int | None
    head_seq: int
    head_hash: str


class AuditVerifier(Protocol):
    def verify(self, *, expected_head_seq: int, expected_head_hash: str) -> AuditVerification: ...


def audit_bytes(envelope: AuditEnvelope) -> bytes:
    require_utc(envelope.at)
    entry = envelope.entry
    return canonical_json(
        {
            "origin_node_id": envelope.origin_node_id,
            "origin_incarnation": envelope.origin_incarnation,
            "audit_seq": envelope.audit_seq,
            "at": envelope.at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "entry": {
                "event_id": entry.event_id,
                "event_type": entry.event_type,
                "event_version": entry.event_version,
                "actor": {"kind": entry.actor.kind, "id": entry.actor.id},
                "refs": dict(entry.refs),
                "correlation_id": entry.correlation_id,
                "causation_id": entry.causation_id,
                "safe_metadata": dict(entry.safe_metadata),
            },
            "prev_hash": envelope.prev_hash,
        }
    )


def audit_hash(envelope: AuditEnvelope) -> str:
    return hashlib.sha256(b"crew-health.audit.v1\0" + audit_bytes(envelope)).hexdigest()
