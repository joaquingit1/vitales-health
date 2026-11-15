from __future__ import annotations

import hashlib

from crew_health.kernel.clock import Hlc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReplicationChange, ReplicationRef, UnitOfWork
from crew_health.platform.adapters import codec


def append_change(
    uow: UnitOfWork, change: ReplicationChange, *, node_id: str, incarnation: str, hlc: Hlc | None = None
) -> ReplicationRef:
    """Append one hash-chained entry. With a stamp (SYSTEM 5.2), the HLC high water advanced in this same
    transaction is stored on the entry and covered by its hash; a rollback discards both."""
    require_id(change.record_id)
    require_id(change.release_id)
    raw = codec(ReplicationChange).dump_json(change)
    rows = uow.connection.rows("SELECT seq, entry_hash FROM repl_log ORDER BY seq DESC LIMIT 1", (), limit=1)
    seq, previous = (int(str(rows[0][0])) + 1, str(rows[0][1])) if rows else (1, "0" * 64)
    fields: dict[str, object] = {
        "seq": seq,
        "origin_node_id": node_id,
        "origin_incarnation": incarnation,
        "change": raw.decode(),
        "prev_hash": previous,
    }
    if hlc is not None:
        if hlc.node_id != node_id or hlc.incarnation_id != incarnation:
            raise StorageUnavailable("corrupt")
        fields["hlc"] = [hlc.physical_ms, hlc.logical]
    digest = hashlib.sha256(b"crew-health.replication.v1\0" + canonical_json(fields)).hexdigest()
    if hlc is None:
        uow.connection.execute(
            "INSERT INTO repl_log(seq,origin_node_id,origin_incarnation,change,prev_hash,entry_hash) VALUES (?,?,?,?,?,?)",
            (seq, node_id, incarnation, raw, previous, digest),
        )
    else:
        uow.connection.execute(
            "INSERT INTO repl_log(seq,origin_node_id,origin_incarnation,change,prev_hash,entry_hash,hlc_physical_ms,hlc_logical) VALUES (?,?,?,?,?,?,?,?)",
            (seq, node_id, incarnation, raw, previous, digest, hlc.physical_ms, hlc.logical),
        )
    uow.connection.execute(
        "INSERT OR IGNORE INTO platform_reference VALUES (?,?)", (change.record_type, change.record_id)
    )
    return ReplicationRef(node_id, incarnation, seq)
