"""What every supplies use case shares: its dependencies, the mission partition and the computed cover rows."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.supplies.application.ports import MissionFacts, MissionStatePort, PlanRunReader
from crew_health.supplies.application.release_index import ReleaseIndex, ReleaseIndexCache
from crew_health.supplies.application.repository import LedgerRepository, PairKey, View, pair_key
from crew_health.supplies.application.settings import SuppliesLimits
from crew_health.supplies.domain.cover import compute_row
from crew_health.supplies.domain.errors import LedgerCorrupt, SuppliesError
from crew_health.supplies.domain.model import CoverRow, Head, Partition


class MissionUnknown(SuppliesError):
    """No current mission: the partition, and so every used total, is unknown (fail loud, never a default)."""

    def __init__(self) -> None:
        super().__init__("no current mission")


class BoundedCalls:
    """Calls into another module's reader with a timeout and at most `inflight` calls running (AGENTS 3.2)."""

    def __init__(self, *, inflight: int, timeout_ms: int) -> None:
        self._slots = threading.BoundedSemaphore(inflight)
        self._pool = ThreadPoolExecutor(max_workers=inflight, thread_name_prefix="supplies-ref")
        self._timeout = timeout_ms / 1000

    def call[T](self, fn: Callable[[], T]) -> T:
        if not self._slots.acquire(blocking=False):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "reference reads busy", retryable=True)
        try:
            future: Future[T] = self._pool.submit(fn)
        except RuntimeError:
            self._slots.release()
            raise
        future.add_done_callback(lambda _: self._slots.release())
        try:
            return future.result(timeout=self._timeout)
        except FutureTimeout as error:
            raise ProblemError(
                ErrorCode.INTERNAL_ERROR, "reference read timed out", retryable=True
            ) from error


@dataclass(slots=True)
class Corrupt:
    """Partitions the start-up verifier found broken or could not reach within its bounds (E32 5.1): their cover is
    unknown and their uses refused."""

    keys: frozenset[tuple[Partition, str]] = frozenset()
    unverified_after: PairKey | None = None  # pairs past this key were not verified
    lock: threading.Lock = field(default_factory=threading.Lock)

    def mark(self, keys: frozenset[tuple[Partition, str]], unverified_after: PairKey | None = None) -> None:
        with self.lock:
            self.keys, self.unverified_after = keys, unverified_after

    def has(self, partition: Partition, supply_id: str) -> bool:
        with self.lock:
            if (partition, supply_id) in self.keys:
                return True
            return (
                self.unverified_after is not None and pair_key(partition, supply_id) > self.unverified_after
            )


@dataclass(frozen=True, slots=True)
class Deps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    ids: IdFactory
    idempotency: IdempotencyStore
    repository: LedgerRepository
    indexes: ReleaseIndexCache
    limits: SuppliesLimits
    mission: MissionStatePort | None
    help_sessions: HelpSessionReader | None
    plan_runs: PlanRunReader | None
    releases: ConfigReleaseReader | None
    references: BoundedCalls
    corrupt: Corrupt


def current_mission(deps: Deps, view: View) -> MissionFacts:
    facts = None if deps.mission is None else deps.mission.current(view)
    if facts is None:
        raise MissionUnknown()
    return facts


def partition(facts: MissionFacts, environment_id: str) -> Partition:
    """SYSTEM 8.5: one local site, so the custody location is the environment until custody transfers exist."""
    return Partition(facts.mission_id, facts.mission_mode, environment_id, environment_id)


def read_heads(deps: Deps, view: View, where: Partition) -> Mapping[str, Head]:
    """Every head of the partition in keyset pages of storage.max_rows; more than supplies.max_supplies used
    supplies is refused loudly (PAYLOAD_TOO_LARGE), never truncated."""
    page = deps.limits.page_rows
    found: dict[str, Head] = {}
    after = ""
    for _ in range(deps.limits.max_supplies // page + 1):
        batch = deps.repository.heads(view, where, after_supply_id=after, limit=page)
        found.update(batch)
        if len(found) > deps.limits.max_supplies:
            break
        if len(batch) < page:
            return found
        after = max(batch)
    raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "more used supplies than supplies.max_supplies")


def cover_rows(
    deps: Deps, view: View, index: ReleaseIndex, where: Partition, heads: Mapping[str, Head]
) -> tuple[CoverRow, ...]:
    """Every kit-map supply in document order. A corrupt partition shows its quantity as not known; a head counted
    in another unit than the supply's cannot be shown at all (LedgerCorrupt: the partition is unreadable)."""
    needs = index.needs_in(where.environment_id)
    stock = index.kit.stock.get(where.environment_id, {})
    rows: list[CoverRow] = []
    for supply in index.kit.supplies:
        row = stock.get(supply.supply_id)
        head = heads.get(supply.supply_id)
        if head is not None and head.used.unit != supply.unit:
            raise LedgerCorrupt(where.environment_id)
        if row is not None and deps.corrupt.has(where, supply.supply_id):
            row = replace(row, packed=None)
        rows.append(
            compute_row(
                supply,
                row,
                head,
                needs.get(supply.supply_id, ()),
                index.threshold,
                where.environment_id,
            )
        )
    return tuple(rows)
