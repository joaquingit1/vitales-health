"""What the supplies application needs from its storage adapter (implemented by adapters/sqlite_ledger.py)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.supplies.domain.guard import UsedTotal
from crew_health.supplies.domain.model import Head, Partition

type View = UnitOfWork | ReadSnapshot
# One (partition, supply) pair as its sort key: mission, origin mode, environment, custody location, supply. Keyset
# pages of the ledger start after one of these, so every read stays within storage.max_rows.
type PairKey = tuple[str, str, str, str, str]
FIRST_PAIR: PairKey = ("", "", "", "", "")


def pair_key(partition: Partition, supply_id: str) -> PairKey:
    return (
        partition.mission_id,
        partition.origin_mode,
        partition.environment_id,
        partition.custody_location_id,
        supply_id,
    )


@dataclass(frozen=True, slots=True)
class StoredUse:
    """One append-only row of supplies_use."""

    supply_use_id: str
    partition: Partition
    supply_id: str
    quantity: Quantity
    seq: int
    used_after: Quantity
    plan_run_id: str | None
    help_session_id: str | None
    step_id: str | None
    occurrence: int | None
    at: datetime  # hub clock when recorded
    observed_at: datetime  # when it happened: client time when given and not after `at`
    by_kind: str
    by_id: str
    release_id: str
    command_key: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class StoredRefusal:
    """Evidence that an offline help-session use was refused (E32 4.1, HZ14): IDs, amount and code only."""

    refusal_id: str
    partition: Partition
    supply_id: str
    quantity: Quantity
    help_session_id: str
    step_id: str | None
    code: str
    command_key: str
    at: datetime
    observed_at: datetime


class LedgerRepository(Protocol):
    def head(self, view: View, partition: Partition, supply_id: str) -> Head | None: ...
    def heads(
        self, view: View, partition: Partition, *, after_supply_id: str, limit: int
    ) -> Mapping[str, Head]: ...
    def by_command_key(self, view: View, command_key: str) -> StoredUse | None: ...
    def append(self, uow: UnitOfWork, use: StoredUse) -> None: ...
    def registered_units(self, view: View, *, after_supply_id: str, limit: int) -> Mapping[str, str]: ...
    def register_unit(
        self, uow: UnitOfWork, supply_id: str, unit_code: str, *, first_use_id: str
    ) -> None: ...
    def totals(
        self, view: View, *, mission_id: str | None, after: PairKey, limit: int
    ) -> Sequence[tuple[PairKey, UsedTotal]]: ...
    def append_refusal(self, uow: UnitOfWork, refusal: StoredRefusal) -> bool: ...
    def chain(
        self, view: View, partition: Partition, supply_id: str, *, after_seq: int, limit: int
    ) -> Sequence[tuple[int, Quantity, Quantity]]: ...
    def partitions(self, view: View, *, after: PairKey, limit: int) -> Sequence[tuple[Partition, str]]: ...
