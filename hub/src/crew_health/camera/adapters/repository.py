"""SQLite repository of the camera module's own tables, camera_dispatch and camera_reference (SYSTEM 4.1: a module
owns its tables)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from crew_health.camera.application.dispatch import DispatchState, Intent
from crew_health.camera.application.ports import OriginMode
from crew_health.camera.application.reference import Ticket
from crew_health.kernel.storage import SqlValue, StorageSession

_COLUMNS = (
    "correlation_id, capability_code, adapter_id, state, request_id, patient_id, sequence, acquisition_id,"
    " device_ids, required_origin, deadline, code"
)
_STATES: tuple[DispatchState, ...] = (
    "assigned",
    "ready",
    "dispatched",
    "done",
    "reporting",
    "reported",
    "dropped",
    "cancelling",
    "cancelled",
)
_ORIGINS: tuple[OriginMode, ...] = ("LIVE", "REPLAY", "SIM")


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: SqlValue) -> str | None:
    return value if isinstance(value, str) else None


def _devices(value: SqlValue) -> tuple[str, ...]:
    parsed = json.loads(value) if isinstance(value, str) else []
    return tuple(str(d) for d in parsed) if isinstance(parsed, list) else ()


def _intent(row: tuple[SqlValue, ...]) -> Intent:
    state = next(s for s in _STATES if s == row[3])
    sequence, deadline = row[6], _text(row[10])
    return Intent(
        correlation_id=str(row[0]),
        capability_code=str(row[1]),
        adapter_id=str(row[2]),
        state=state,
        request_id=_text(row[4]),
        patient_id=_text(row[5]),
        sequence=sequence if isinstance(sequence, int) else None,
        acquisition_id=_text(row[7]),
        device_ids=_devices(row[8]),
        required_origin=next((o for o in _ORIGINS if o == row[9]), None),
        deadline=None if deadline is None else datetime.fromisoformat(deadline),
        code=_text(row[11]),
    )


class SqliteDispatchStore:
    def record(
        self,
        session: StorageSession,
        *,
        correlation_id: str,
        capability_code: str,
        adapter_id: str,
        at: datetime,
    ) -> None:
        stamp = _instant(at)
        session.execute(
            "INSERT INTO camera_dispatch (correlation_id, capability_code, adapter_id, state, created_at, updated_at)"
            " VALUES (?, ?, ?, 'assigned', ?, ?) ON CONFLICT (correlation_id) DO NOTHING",
            (correlation_id, capability_code, adapter_id, stamp, stamp),
        )

    def get(self, session: StorageSession, correlation_id: str) -> Intent | None:
        rows = session.rows(
            f"SELECT {_COLUMNS} FROM camera_dispatch WHERE correlation_id = ?", (correlation_id,), limit=1
        )
        return _intent(rows[0]) if rows else None

    def for_request(self, session: StorageSession, request_id: str, *, limit: int) -> tuple[Intent, ...]:
        rows = session.rows(
            f"SELECT {_COLUMNS} FROM camera_dispatch WHERE request_id = ? ORDER BY sequence LIMIT ?",
            (request_id, limit),
            limit=limit,
        )
        return tuple(_intent(r) for r in rows)

    def open(self, session: StorageSession, *, limit: int) -> tuple[Intent, ...]:
        """Intents the worker may still be working on or that were never sent, least recently checked first."""
        rows = session.rows(
            f"SELECT {_COLUMNS} FROM camera_dispatch WHERE state IN ('ready', 'dispatched')"
            " ORDER BY updated_at, correlation_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(_intent(r) for r in rows)

    def update(self, session: StorageSession, intent: Intent, *, at: datetime) -> None:
        session.execute(
            "UPDATE camera_dispatch SET state = ?, request_id = ?, patient_id = ?, sequence = ?, acquisition_id = ?,"
            " device_ids = ?, required_origin = ?, deadline = ?, code = ?, updated_at = ? WHERE correlation_id = ?",
            (
                intent.state,
                intent.request_id,
                intent.patient_id,
                intent.sequence,
                intent.acquisition_id,
                json.dumps(list(intent.device_ids)),
                intent.required_origin,
                None if intent.deadline is None else _instant(intent.deadline),
                intent.code,
                _instant(at),
                intent.correlation_id,
            ),
        )


class SqliteReferenceStore:
    def open(self, session: StorageSession, ticket: Ticket, *, at: datetime) -> None:
        session.execute(
            "INSERT INTO camera_reference (ticket_id, record_id, persist, observation_id, opened_at)"
            " VALUES (?, ?, ?, NULL, ?) ON CONFLICT (ticket_id) DO NOTHING",
            (ticket.ticket_id, ticket.record_id, 1 if ticket.persist else 0, _instant(at)),
        )

    def get(self, session: StorageSession, ticket_id: str) -> Ticket | None:
        rows = session.rows(
            "SELECT ticket_id, record_id, persist, observation_id FROM camera_reference WHERE ticket_id = ?",
            (ticket_id,),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return Ticket(str(row[0]), str(row[1]), row[2] == 1, _text(row[3]))

    def recorded(self, session: StorageSession, ticket_id: str, observation_id: str, *, at: datetime) -> None:
        """Written once: a recorded reading is never replaced or removed."""
        session.execute(
            "UPDATE camera_reference SET observation_id = ?, recorded_at = ? WHERE ticket_id = ?"
            " AND observation_id IS NULL",
            (observation_id, _instant(at), ticket_id),
        )
