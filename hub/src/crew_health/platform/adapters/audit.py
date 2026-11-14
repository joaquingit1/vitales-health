from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry, AuditEnvelope, AuditVerification, audit_bytes, audit_hash
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWork
from crew_health.platform.adapters import codec
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.domain.errors import fail


class AuditRepository:
    def __init__(
        self, *, node_id: str, incarnation: str, policies: Mapping[str, AuditPolicy] | None = None
    ) -> None:
        self.node_id, self.incarnation = node_id, incarnation
        self.policies = dict(policies) if policies is not None else {}
        self.codec = codec(AuditEnvelope)

    def append(self, uow: UnitOfWork, entry: AuditEntry) -> None:
        policy = self.policies.get(entry.event_type)
        if policy is None:
            raise fail(ErrorCode.INVALID_VALUE)
        policy.validate(entry)
        for field, reference in entry.refs.items():
            kinds = policy.reference_types[field]
            exists = False
            for kind in sorted(kinds):
                rows = uow.connection.rows(
                    "SELECT record_id FROM platform_reference WHERE record_type=? AND record_id=?",
                    (kind, reference),
                    limit=1,
                )
                exists = exists or bool(rows)
            if not exists:
                raise fail(ErrorCode.INVALID_VALUE)
        rows = uow.connection.rows(
            "SELECT seq, entry_hash FROM platform_audit ORDER BY seq DESC LIMIT 1", (), limit=1
        )
        seq, previous = (int(str(rows[0][0])) + 1, str(rows[0][1])) if rows else (1, "0" * 64)
        envelope = AuditEnvelope(self.node_id, self.incarnation, seq, uow.now, entry, previous)
        uow.connection.execute(
            "INSERT INTO platform_audit VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                self.node_id,
                self.incarnation,
                uow.now.isoformat(),
                entry.event_id,
                audit_bytes(envelope),
                audit_hash(envelope),
            ),
        )


class SQLiteAuditVerifier:
    def __init__(self, snapshots: ReadSnapshotFactory, *, page_size: int) -> None:
        self.snapshots, self.page_size = snapshots, page_size
        self.codec = codec(AuditEnvelope)

    def verify(self, *, expected_head_seq: int, expected_head_hash: str) -> AuditVerification:
        sequence, previous = 0, "0" * 64
        with self.snapshots.snapshot() as view:
            while sequence <= expected_head_seq:
                rows = view.connection.rows(
                    "SELECT seq, origin_node_id, origin_incarnation, at, event_id, envelope, entry_hash FROM platform_audit WHERE seq > ? ORDER BY seq LIMIT ?",
                    (sequence, self.page_size),
                    limit=self.page_size,
                )
                if not rows:
                    break
                for row in rows:
                    raw = row[5]
                    if not isinstance(raw, bytes):
                        raise StorageUnavailable("corrupt")
                    try:
                        payload = json.loads(raw)
                        entry = payload["entry"]
                        # AuditEntry mappings remain immutable after boundary parsing.
                        envelope = self.codec.validate_python(payload)
                        valid = (
                            row[0] == sequence + 1 == envelope.audit_seq
                            and row[1] == envelope.origin_node_id
                            and row[2] == envelope.origin_incarnation
                            and datetime.fromisoformat(str(row[3])) == envelope.at
                            and row[4] == entry["event_id"]
                            and previous == envelope.prev_hash
                            and raw == audit_bytes(envelope)
                            and row[6] == audit_hash(envelope)
                        )
                    except ValueError, KeyError, TypeError:
                        valid = False
                    if not valid:
                        return AuditVerification(False, sequence + 1, sequence, previous)
                    sequence, previous = int(str(row[0])), str(row[6])
                if len(rows) < self.page_size:
                    break
        ok = sequence == expected_head_seq and previous == expected_head_hash
        return AuditVerification(ok, None if ok else sequence + 1, sequence, previous)
