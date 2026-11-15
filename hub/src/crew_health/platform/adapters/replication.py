from __future__ import annotations

import hashlib

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReplicationChange, ReplicationRef, UnitOfWork
from crew_health.platform.adapters import codec


def append_change(
    uow: UnitOfWork, change: ReplicationChange, *, node_id: str, incarnation: str
) -> ReplicationRef:
    require_id(change.record_id)
    require_id(change.release_id)
    raw = codec(ReplicationChange).dump_json(change)
    rows = uow.connection.rows("SELECT seq, entry_hash FROM repl_log ORDER BY seq DESC LIMIT 1", (), limit=1)
    seq, previous = (int(str(rows[0][0])) + 1, str(rows[0][1])) if rows else (1, "0" * 64)
    envelope = canonical_json(
        {
            "seq": seq,
            "origin_node_id": node_id,
            "origin_incarnation": incarnation,
            "change": raw.decode(),
            "prev_hash": previous,
        }
    )
    digest = hashlib.sha256(b"crew-health.replication.v1\0" + envelope).hexdigest()
    uow.connection.execute(
        "INSERT INTO repl_log VALUES (?, ?, ?, ?, ?, ?)", (seq, node_id, incarnation, raw, previous, digest)
    )
    uow.connection.execute(
        "INSERT OR IGNORE INTO platform_reference VALUES (?,?)", (change.record_type, change.record_id)
    )
    return ReplicationRef(node_id, incarnation, seq)
