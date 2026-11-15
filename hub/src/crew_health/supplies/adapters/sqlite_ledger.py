"""SQLite repository of the supplies ledger (E32 5.1). Only this file touches the supplies_* tables; every read is
bounded by the caller's limit, which never exceeds the storage session's own row bound (SYSTEM 5.7): a read that may
match more rows is a keyset page, and the caller pages on with a bounded number of pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from crew_health.kernel.storage import SqlValue
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import UnitOfWork
from crew_health.supplies.application.repository import PairKey, StoredRefusal, StoredUse, View
from crew_health.supplies.domain.errors import LedgerCorrupt
from crew_health.supplies.domain.guard import UsedTotal
from crew_health.supplies.domain.model import ORIGIN_MODES, Head, OriginMode, Partition
from crew_health.supplies.domain.quantity import decimal_text, parse_decimal_text

_PARTITION = "mission_id=? AND origin_mode=? AND environment_id=? AND custody_location_id=?"
_PAIR = "u.mission_id, u.origin_mode, u.environment_id, u.custody_location_id, u.supply_id"


def instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive instant")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_instant(text: SqlValue) -> datetime:
    if not isinstance(text, str):
        raise LedgerCorrupt("instant")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _key(partition: Partition) -> tuple[SqlValue, ...]:
    return (
        partition.mission_id,
        partition.origin_mode,
        partition.environment_id,
        partition.custody_location_id,
    )


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise LedgerCorrupt("text column")
    return value


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise LedgerCorrupt("integer column")
    return value


def _quantity(value: SqlValue, unit: SqlValue) -> Quantity:
    try:
        return Quantity(parse_decimal_text(_text(value)), Unit(_text(unit)))
    except ValueError as error:
        raise LedgerCorrupt("decimal column") from error


def _mode(value: SqlValue) -> OriginMode:
    for mode in ORIGIN_MODES:
        if value == mode:
            return mode
    raise LedgerCorrupt("origin mode")


class SqliteLedger:
    """LedgerRepository over supplies_use, supplies_stock and supplies_use_refused."""

    def head(self, view: View, partition: Partition, supply_id: str) -> Head | None:
        rows = view.connection.rows(
            "SELECT partition_seq, used_after_decimal, unit_ucum, supply_use_id, observed_at_utc FROM supplies_use "
            f"WHERE {_PARTITION} AND supply_id=? ORDER BY partition_seq DESC LIMIT 1",
            (*_key(partition), supply_id),
            limit=1,
        )
        if not rows:
            return None
        seq, used, unit, use_id, observed = rows[0]
        return Head(_int(seq), _quantity(used, unit), _text(use_id), parse_instant(observed))

    def heads(
        self, view: View, partition: Partition, *, after_supply_id: str, limit: int
    ) -> Mapping[str, Head]:
        """One keyset page of the partition's heads, supply_id order, after `after_supply_id`."""
        rows = view.connection.rows(
            "SELECT u.supply_id, u.partition_seq, u.used_after_decimal, u.unit_ucum, u.supply_use_id, "
            "u.observed_at_utc FROM supplies_use u "
            f"WHERE u.{_PARTITION.replace(' AND ', ' AND u.')} AND u.supply_id > ? "
            "AND u.partition_seq = (SELECT MAX(v.partition_seq) "
            "FROM supplies_use v WHERE v.mission_id=u.mission_id AND v.origin_mode=u.origin_mode AND "
            "v.environment_id=u.environment_id AND v.custody_location_id=u.custody_location_id AND "
            "v.supply_id=u.supply_id) ORDER BY u.supply_id LIMIT ?",
            (*_key(partition), after_supply_id, limit),
            limit=limit,
        )
        return {
            _text(sid): Head(_int(seq), _quantity(used, unit), _text(use_id), parse_instant(observed))
            for sid, seq, used, unit, use_id, observed in rows
        }

    def by_command_key(self, view: View, command_key: str) -> StoredUse | None:
        rows = view.connection.rows(
            "SELECT supply_use_id, mission_id, origin_mode, environment_id, custody_location_id, supply_id, "
            "unit_ucum, quantity_decimal, partition_seq, used_after_decimal, plan_run_id, help_session_id, step_id, "
            "occurrence, at_utc, observed_at_utc, by_kind, by_id, config_release_id, command_key, request_sha256 "
            "FROM supplies_use WHERE command_key=?",
            (command_key,),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        partition = Partition(_text(r[1]), _mode(r[2]), _text(r[3]), _text(r[4]))
        return StoredUse(
            supply_use_id=_text(r[0]),
            partition=partition,
            supply_id=_text(r[5]),
            quantity=_quantity(r[7], r[6]),
            seq=_int(r[8]),
            used_after=_quantity(r[9], r[6]),
            plan_run_id=None if r[10] is None else _text(r[10]),
            help_session_id=None if r[11] is None else _text(r[11]),
            step_id=None if r[12] is None else _text(r[12]),
            occurrence=None if r[13] is None else _int(r[13]),
            at=parse_instant(r[14]),
            observed_at=parse_instant(r[15]),
            by_kind=_text(r[16]),
            by_id=_text(r[17]),
            release_id=_text(r[18]),
            command_key=_text(r[19]),
            request_sha256=_text(r[20]),
        )

    def append(self, uow: UnitOfWork, use: StoredUse) -> None:
        p = use.partition
        uow.connection.execute(
            "INSERT INTO supplies_use VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                use.supply_use_id,
                p.mission_id,
                p.origin_mode,
                p.environment_id,
                p.custody_location_id,
                use.supply_id,
                use.quantity.unit.code,
                decimal_text(use.quantity.value),
                use.seq,
                decimal_text(use.used_after.value),
                use.plan_run_id,
                use.help_session_id,
                use.step_id,
                use.occurrence,
                instant(use.at),
                instant(use.observed_at),
                use.by_kind,
                use.by_id,
                use.release_id,
                use.command_key,
                use.request_sha256,
            ),
        )

    def registered_units(self, view: View, *, after_supply_id: str, limit: int) -> Mapping[str, str]:
        rows = view.connection.rows(
            "SELECT supply_id, unit_ucum FROM supplies_stock WHERE supply_id > ? ORDER BY supply_id LIMIT ?",
            (after_supply_id, limit),
            limit=limit,
        )
        return {_text(s): _text(u) for s, u in rows}

    def register_unit(self, uow: UnitOfWork, supply_id: str, unit_code: str, *, first_use_id: str) -> None:
        rows = uow.connection.rows(
            "SELECT unit_ucum FROM supplies_stock WHERE supply_id=?", (supply_id,), limit=1
        )
        if not rows:
            uow.connection.execute(
                "INSERT INTO supplies_stock VALUES (?,?,?)", (supply_id, unit_code, first_use_id)
            )
        elif rows[0][0] != unit_code:
            raise LedgerCorrupt("registered unit differs")

    def totals(
        self, view: View, *, mission_id: str | None, after: PairKey, limit: int
    ) -> Sequence[tuple[PairKey, UsedTotal]]:
        """One keyset page of the latest running total of every (partition, supply) of the mission, or of every
        mission when None, in pair-key order after `after`."""
        mission = "" if mission_id is None else "u.mission_id=? AND "
        rows = view.connection.rows(
            "SELECT u.mission_id, u.origin_mode, u.environment_id, u.custody_location_id, u.supply_id, "
            f"u.used_after_decimal, u.unit_ucum FROM supplies_use u WHERE {mission}"
            f"({_PAIR}) > (?,?,?,?,?) AND u.partition_seq = (SELECT MAX(v.partition_seq) FROM supplies_use v "
            "WHERE v.mission_id=u.mission_id AND v.origin_mode=u.origin_mode AND v.environment_id=u.environment_id "
            "AND v.custody_location_id=u.custody_location_id AND v.supply_id=u.supply_id) "
            f"ORDER BY {_PAIR} LIMIT ?",
            (() if mission_id is None else (mission_id,)) + (*after, limit),
            limit=limit,
        )
        out: list[tuple[PairKey, UsedTotal]] = []
        for m, mode, env, custody, sid, used, unit in rows:
            key = (_text(m), _text(mode), _text(env), _text(custody), _text(sid))
            out.append((key, UsedTotal(key[2], key[1], key[4], _quantity(used, unit))))
        return tuple(out)

    def append_refusal(self, uow: UnitOfWork, refusal: StoredRefusal) -> bool:
        """True when this command key's evidence was written now; False when it already existed."""
        found = uow.connection.rows(
            "SELECT refusal_id FROM supplies_use_refused WHERE command_key=?", (refusal.command_key,), limit=1
        )
        if found:
            return False
        p = refusal.partition
        uow.connection.execute(
            "INSERT INTO supplies_use_refused VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                refusal.refusal_id,
                p.mission_id,
                p.origin_mode,
                p.environment_id,
                p.custody_location_id,
                refusal.supply_id,
                refusal.quantity.unit.code,
                decimal_text(refusal.quantity.value),
                refusal.help_session_id,
                refusal.step_id,
                refusal.code,
                refusal.command_key,
                instant(refusal.at),
                instant(refusal.observed_at),
            ),
        )
        return True

    def chain(
        self, view: View, partition: Partition, supply_id: str, *, after_seq: int, limit: int
    ) -> Sequence[tuple[int, Quantity, Quantity]]:
        rows = view.connection.rows(
            "SELECT partition_seq, quantity_decimal, used_after_decimal, unit_ucum FROM supplies_use "
            f"WHERE {_PARTITION} AND supply_id=? AND partition_seq > ? ORDER BY partition_seq LIMIT ?",
            (*_key(partition), supply_id, after_seq, limit),
            limit=limit,
        )
        return tuple((_int(s), _quantity(q, u), _quantity(a, u)) for s, q, a, u in rows)

    def partitions(self, view: View, *, after: PairKey, limit: int) -> Sequence[tuple[Partition, str]]:
        """One keyset page of the distinct (partition, supply) pairs of every mission, after `after`."""
        rows = view.connection.rows(
            f"SELECT DISTINCT {_PAIR} FROM supplies_use u WHERE ({_PAIR}) > (?,?,?,?,?) ORDER BY {_PAIR} LIMIT ?",
            (*after, limit),
            limit=limit,
        )
        return tuple((Partition(_text(m), _mode(o), _text(e), _text(c)), _text(s)) for m, o, e, c, s in rows)
