"""Start-up ledger verification (E32 5.1, INV-S12): every partition's chain is contiguous from 1 with exact running
totals. A broken one is reported, never repaired: its cover shows unknown and its uses are refused."""

from __future__ import annotations

from crew_health.kernel.uow import ReadSnapshot
from crew_health.supplies.application.repository import LedgerRepository
from crew_health.supplies.domain.errors import LedgerCorrupt
from crew_health.supplies.domain.ledger import check_chain
from crew_health.supplies.domain.model import Partition


def verify_ledger(
    view: ReadSnapshot,
    repository: LedgerRepository,
    *,
    max_partitions: int,
    batch_rows: int,
    max_batches: int,
) -> frozenset[tuple[Partition, str]]:
    """Reads at most `max_batches` batches of `batch_rows` per partition; a chain longer than that is unverified,
    which counts as broken (fail safe)."""
    broken: set[tuple[Partition, str]] = set()
    for where, supply_id in repository.partitions(view, limit=max_partitions):
        if not _verified(view, repository, where, supply_id, batch_rows, max_batches):
            broken.add((where, supply_id))
    return frozenset(broken)


def _verified(
    view: ReadSnapshot,
    repository: LedgerRepository,
    where: Partition,
    supply_id: str,
    batch_rows: int,
    max_batches: int,
) -> bool:
    last = None
    for _ in range(max_batches):
        try:
            seq = 0 if last is None else last[0]
            entries = repository.chain(view, where, supply_id, after_seq=seq, limit=batch_rows)
            if not entries:
                return last is not None
            last = check_chain(entries, last)
        except ValueError, LedgerCorrupt:
            return False
        if len(entries) < batch_rows:
            return True
    return False
