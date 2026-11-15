"""SQLite repository of the medicines module (E32 5.2). Every read is bounded; SQLite never does decimal arithmetic."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.medicines.application.codec import dumps, loads
from crew_health.medicines.application.ports import ClockCheck, Movement, Partition, RecordRow

MIGRATION = Path(__file__).parent / "migrations" / "0001_medicines.sql"
type View = UnitOfWork | ReadSnapshot


def _t(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "medicines row is malformed", retryable=False)
    return value


def _o(value: SqlValue) -> str | None:
    return None if value is None else _t(value)


def instant(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def parse_instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "medicines row is malformed", retryable=False)
    return value


def _oint(value: SqlValue) -> int | None:
    return None if value is None else _int(value)


_CLOCK = (
    "seq, kind, at_utc, monotonic_ns, process_epoch, reference_seq, wall_delta_ms, monotonic_delta_ms, "
    "correction_version"
)


def _check(r: tuple[SqlValue, ...]) -> ClockCheck:
    kind = _t(r[1])
    if kind not in ("reading", "discontinuity"):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "medicines row is malformed", retryable=False)
    return ClockCheck(
        seq=_int(r[0]),
        kind="reading" if kind == "reading" else "discontinuity",
        at=parse_instant(_t(r[2])),
        monotonic_ns=_int(r[3]),
        process_epoch=_t(r[4]),
        reference_seq=_oint(r[5]),
        wall_delta_ms=_oint(r[6]),
        monotonic_delta_ms=_oint(r[7]),
        correction_version=_oint(r[8]),
    )


def _row(r: tuple[SqlValue, ...]) -> RecordRow:
    body = loads(_t(r[10]))
    return RecordRow(
        record_id=_t(r[0]),
        kind=_t(r[1]),
        mission_id=_t(r[2]),
        origin_mode=_t(r[3]),
        subject_person_id=_o(r[4]),
        lot_id=_o(r[5]),
        environment_id=_o(r[6]),
        at=parse_instant(_t(r[7])),
        by_id=_t(r[8]),
        command_key=_o(r[9]),
        body=body,
    )


_COLUMNS = (
    "record_id, kind, mission_id, origin_mode, subject_person_id, lot_id, environment_id, at_utc, by_id, "
    "command_key, body_json"
)


class SqliteMedicines:
    """Implements medicines.application.ports.MedicinesRepository."""

    def add_record(self, uow: UnitOfWork, row: RecordRow) -> None:
        uow.connection.execute(
            f"INSERT INTO medicines_record ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.record_id,
                row.kind,
                row.mission_id,
                row.origin_mode,
                row.subject_person_id,
                row.lot_id,
                row.environment_id,
                instant(row.at),
                row.by_id,
                row.command_key,
                dumps(row.body),
            ),
        )

    def record(self, view: View, record_id: str) -> RecordRow | None:
        rows = view.connection.rows(
            f"SELECT {_COLUMNS} FROM medicines_record WHERE record_id=?", (record_id,), limit=1
        )
        return _row(rows[0]) if rows else None

    def by_command_key(self, view: View, command_key: str) -> RecordRow | None:
        rows = view.connection.rows(
            f"SELECT {_COLUMNS} FROM medicines_record WHERE command_key=?", (command_key,), limit=1
        )
        return _row(rows[0]) if rows else None

    def records(
        self,
        view: View,
        kind: str,
        partition: Partition,
        *,
        limit: int,
        subject_person_id: str | None = None,
        lot_id: str | None = None,
    ) -> tuple[RecordRow, ...]:
        sql = f"SELECT {_COLUMNS} FROM medicines_record WHERE kind=? AND mission_id=? AND origin_mode=?"
        params: list[SqlValue] = [kind, partition.mission_id, partition.origin_mode]
        if subject_person_id is not None:
            sql += " AND subject_person_id=?"
            params.append(subject_person_id)
        if lot_id is not None:
            sql += " AND lot_id=?"
            params.append(lot_id)
        sql += " ORDER BY at_utc, rowid"  # insert order breaks a tie in time
        return tuple(_row(r) for r in view.connection.rows(sql, tuple(params), limit=limit))

    def terminal(self, view: View, proposal_id: str) -> tuple[str, str] | None:
        rows = view.connection.rows(
            "SELECT state, reason FROM medicines_proposal_terminal WHERE proposal_id=?",
            (proposal_id,),
            limit=1,
        )
        return (_t(rows[0][0]), _t(rows[0][1])) if rows else None

    def add_terminal(self, uow: UnitOfWork, proposal_id: str, state: str, reason: str) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_proposal_terminal (proposal_id, state, at_utc, reason) VALUES (?, ?, ?, ?)",
            (proposal_id, state, instant(uow.now), reason),
        )

    def add_movement(self, uow: UnitOfWork, movement: Movement) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_movement (movement_id, mission_id, origin_mode, environment_id, lot_id, kind, "
            "delta_decimal, unit_ucum, administration_id, at_utc, by_id, command_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                movement.movement_id,
                movement.partition.mission_id,
                movement.partition.origin_mode,
                movement.partition.environment_id,
                movement.lot_id,
                movement.kind,
                str(movement.delta),
                movement.unit,
                movement.administration_id,
                instant(uow.now),
                movement.by_id,
                movement.command_key,
            ),
        )

    def movement_by_key(self, view: View, command_key: str) -> str | None:
        rows = view.connection.rows(
            "SELECT movement_id FROM medicines_movement WHERE command_key=?", (command_key,), limit=1
        )
        return _t(rows[0][0]) if rows else None

    def net_movements(self, view: View, partition: Partition, *, limit: int) -> Mapping[str, Decimal]:
        rows = view.connection.rows(
            "SELECT lot_id, delta_decimal FROM medicines_movement WHERE mission_id=? AND origin_mode=? AND environment_id=?",
            (partition.mission_id, partition.origin_mode, partition.environment_id),
            limit=limit,
        )
        out: dict[str, Decimal] = {}
        for lot_id, delta in rows:
            out[_t(lot_id)] = out.get(_t(lot_id), Decimal(0)) + Decimal(_t(delta))
        return out

    def add_ingredients(
        self, uow: UnitOfWork, administration_id: str, amounts: Mapping[str, tuple[Decimal, str]]
    ) -> None:
        for ingredient_id, (value, unit) in sorted(amounts.items()):
            uow.connection.execute(
                "INSERT INTO medicines_admin_ingredient (administration_id, ingredient_id, accounting_decimal, unit_ucum) "
                "VALUES (?, ?, ?, ?)",
                (administration_id, ingredient_id, str(value), unit),
            )

    def ingredients(self, view: View, administration_id: str) -> Mapping[str, tuple[Decimal, str]]:
        rows = view.connection.rows(
            "SELECT ingredient_id, accounting_decimal, unit_ucum FROM medicines_admin_ingredient WHERE administration_id=?",
            (administration_id,),
            limit=64,
        )
        return {_t(r[0]): (Decimal(_t(r[1])), _t(r[2])) for r in rows}

    def correction(self, view: View, administration_id: str) -> str | None:
        rows = view.connection.rows(
            "SELECT reason_text FROM medicines_correction WHERE administration_id=?",
            (administration_id,),
            limit=1,
        )
        return _t(rows[0][0]) if rows else None

    def add_clock_check(self, uow: UnitOfWork, check: ClockCheck) -> int:
        cursor = uow.connection.execute(
            f"INSERT INTO medicines_clock_check ({_CLOCK.removeprefix('seq, ')}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                check.kind,
                instant(check.at),
                check.monotonic_ns,
                check.process_epoch,
                check.reference_seq,
                check.wall_delta_ms,
                check.monotonic_delta_ms,
                check.correction_version,
            ),
        )
        if cursor.last_row_id is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "clock evidence not stored", retryable=False)
        return cursor.last_row_id

    def last_clock_check(self, view: View, kind: str, process_epoch: str | None = None) -> ClockCheck | None:
        if process_epoch is None:
            rows = view.connection.rows(
                f"SELECT {_CLOCK} FROM medicines_clock_check WHERE kind=? ORDER BY seq DESC LIMIT 1",
                (kind,),
                limit=1,
            )
        else:
            rows = view.connection.rows(
                f"SELECT {_CLOCK} FROM medicines_clock_check WHERE kind=? AND process_epoch=? ORDER BY seq DESC LIMIT 1",
                (kind, process_epoch),
                limit=1,
            )
        return _check(rows[0]) if rows else None

    def add_correction(
        self, uow: UnitOfWork, administration_id: str, reason: str, by_id: str, key: str
    ) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_correction (administration_id, reason_text, by_id, at_utc, command_key) "
            "VALUES (?, ?, ?, ?, ?)",
            (administration_id, reason, by_id, instant(uow.now), key),
        )
