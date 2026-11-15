"""Start-up ledger verification (E32 5.1, INV-S12): every partition's chain is contiguous from 1 with exact running
totals. A broken one is reported, never repaired: its cover shows unknown and its uses are refused. Verification
never stops the hub: every read is a bounded keyset page, and whatever lies past the last page it may read counts
as unverified, which is unknown cover too (fail safe)."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.kernel.uow import ReadSnapshot
from crew_health.supplies.application.repository import FIRST_PAIR, LedgerRepository, PairKey, pair_key
from crew_health.supplies.domain.errors import SuppliesError
from crew_health.supplies.domain.ledger import check_chain
from crew_health.supplies.domain.model import Partition


@dataclass(frozen=True, slots=True)
class Verification:
    broken: frozenset[tuple[Partition, str]]
    unverified_after: PairKey | None  # every pair after this key was not read: unknown, as if broken


def verify_ledger(
    view: ReadSnapshot,
    repository: LedgerRepository,
    *,
    page_rows: int,
    batch_rows: int,
    max_batches: int,
    max_pages: int,
) -> Verification:
    """Pages through every (partition, supply) pair, `page_rows` at a time and at most `max_pages` pages; reads at
    most `max_batches` batches of `batch_rows` per pair, and a chain longer than that is unverified (broken)."""
    broken: set[tuple[Partition, str]] = set()
    after = FIRST_PAIR
    for _ in range(max_pages):
        page = repository.partitions(view, after=after, limit=page_rows)
        for where, supply_id in page:
            if not _verified(view, repository, where, supply_id, min(batch_rows, page_rows), max_batches):
                broken.add((where, supply_id))
        if len(page) < page_rows:
            return Verification(frozenset(broken), None)
        after = pair_key(*page[-1])
    return Verification(frozenset(broken), after)


def _verified(
    view: ReadSnapshot,
    repository: LedgerRepository,
    where: Partition,
    supply_id: str,
    batch_rows: int,
    max_batches: int,
) -> bool:
    """A row that breaks the chain, cannot be parsed or is counted in another unit marks the pair broken."""
    last = None
    for _ in range(max_batches):
        try:
            seq = 0 if last is None else last[0]
            entries = repository.chain(view, where, supply_id, after_seq=seq, limit=batch_rows)
            if not entries:
                return last is not None
            last = check_chain(entries, last)
        except ValueError, SuppliesError:
            return False
        if len(entries) < batch_rows:
            return True
    return False
