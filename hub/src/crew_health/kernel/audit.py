from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from crew_health.kernel.clock import require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import _FrozenMapping
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id

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


@dataclass(frozen=True)
class AuditPolicy:
    """What one audit event type may carry (SYSTEM 5.8): its safe metadata values and the record kinds each
    reference field may name. A module declares its policies through ModuleRegistry.audit_policy."""

    event_type: str
    metadata: Mapping[str, frozenset[str | int | bool]]
    reference_fields: frozenset[str]
    reference_types: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _FrozenMapping(self.metadata))
        object.__setattr__(self, "reference_types", _FrozenMapping(self.reference_types))
        if not self.event_type or set(self.reference_types) != set(self.reference_fields):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)

    def validate(self, entry: AuditEntry) -> None:
        invalid = ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        if entry.event_type != self.event_type or entry.event_version != 1:
            raise invalid
        require_id(entry.event_id)
        require_id(entry.actor.id)
        require_id(entry.correlation_id)
        if entry.causation_id is not None:
            require_id(entry.causation_id)
        if set(entry.refs) != self.reference_fields or not set(entry.safe_metadata) <= self.metadata.keys():
            raise invalid
        for value in entry.refs.values():
            require_id(value)
        for key, metadata_value in entry.safe_metadata.items():
            if metadata_value not in self.metadata[key]:
                raise invalid


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
