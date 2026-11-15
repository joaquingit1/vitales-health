"""Help repositories over the kernel StorageSession (SQLite, migrations/0001_help.sql).

A revision that moved under a save, or a row that cannot be decoded, is a storage fault that fails the command
loudly (AGENTS 6): it is never swallowed or retried here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from crew_health.help.adapters import codec
from crew_health.help.application.repositories import GroundMessageRepository, HelpSessionRepository
from crew_health.help.domain.broadcast import BroadcastView
from crew_health.help.domain.errors import HelpStorageFault
from crew_health.help.domain.ground import GroundMessage, HelpLogSnapshot
from crew_health.help.domain.session import HelpSession
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise HelpStorageFault("stored help row is not text")
    return value


def _one(view: View, sql: str, params: tuple[SqlValue, ...]) -> str | None:
    rows = view.connection.rows(sql, params, limit=2)
    if len(rows) > 1:
        raise HelpStorageFault("more than one help row for a unique key")
    return _text(rows[0][0]) if rows else None


def _turn_id(session: HelpSession) -> str | None:
    turn = session.turn
    return turn.help_turn_id if turn is not None and turn.state in ("requested", "running") else None


class SqliteHelpSessions(HelpSessionRepository):
    def get(self, uow: View, session_id: str) -> HelpSession | None:
        raw = _one(uow, "SELECT aggregate_json FROM help_session WHERE help_session_id = ?", (session_id,))
        return codec.session_from_json(raw) if raw is not None else None

    def get_open_for_subject(self, uow: UnitOfWork, person_id: str) -> HelpSession | None:
        raw = _one(
            uow,
            "SELECT aggregate_json FROM help_session WHERE subject_person_id = ? AND state = 'open'",
            (person_id,),
        )
        return codec.session_from_json(raw) if raw is not None else None

    def session_for_turn(self, uow: View, help_turn_id: str) -> HelpSession | None:
        raw = _one(uow, "SELECT aggregate_json FROM help_session WHERE turn_id = ?", (help_turn_id,))
        return codec.session_from_json(raw) if raw is not None else None

    def insert(self, uow: UnitOfWork, session: HelpSession) -> None:
        uow.connection.execute(
            "INSERT INTO help_session (help_session_id, revision, state, subject_person_id, opened_at, turn_id,"
            " aggregate_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session.help_session_id,
                session.revision,
                session.state,
                session.subject_person_id,
                session.opened_at.isoformat(),
                _turn_id(session),
                codec.session_to_json(session),
            ),
        )

    def save(self, uow: UnitOfWork, session: HelpSession, expected_revision: int) -> None:
        result = uow.connection.execute(
            "UPDATE help_session SET revision = ?, state = ?, turn_id = ?, aggregate_json = ?"
            " WHERE help_session_id = ? AND revision = ?",
            (
                session.revision,
                session.state,
                _turn_id(session),
                codec.session_to_json(session),
                session.help_session_id,
                expected_revision,
            ),
        )
        if result.affected_rows != 1:
            raise HelpStorageFault("the help session revision moved")

    def page(
        self, uow: View, state: Literal["open", "closed"] | None, after: str | None, limit: int
    ) -> Sequence[HelpSession]:
        rows = uow.connection.rows(
            "SELECT aggregate_json FROM help_session WHERE (? IS NULL OR state = ?) AND help_session_id > ?"
            " ORDER BY help_session_id",
            (state, state, after or ""),
            limit=limit,
        )
        return tuple(codec.session_from_json(_text(r[0])) for r in rows)

    def open_sessions(self, uow: View, limit: int) -> Sequence[HelpSession]:
        return self.page(uow, "open", None, limit)

    def record_action(
        self, uow: UnitOfWork, session: HelpSession, action: str, actor_ref: str, command_key: str | None
    ) -> None:
        uow.connection.execute(
            "INSERT INTO help_action (help_session_id, revision, action, actor_ref, command_key, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session.help_session_id, session.revision, action, actor_ref, command_key, uow.now.isoformat()),
        )

    def save_broadcast(self, uow: UnitOfWork, broadcast: BroadcastView) -> None:
        uow.connection.execute(
            "INSERT INTO help_broadcast (broadcast_id, help_session_id, state, started_at, view_json)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (broadcast_id) DO UPDATE SET state = excluded.state,"
            " view_json = excluded.view_json",
            (
                broadcast.broadcast_id,
                broadcast.help_session_id,
                broadcast.state,
                broadcast.started_at.isoformat(),
                codec.broadcast_to_json(broadcast),
            ),
        )

    def broadcast_for(self, uow: View, help_session_id: str) -> BroadcastView | None:
        raw = _one(uow, "SELECT view_json FROM help_broadcast WHERE help_session_id = ?", (help_session_id,))
        return codec.broadcast_from_json(raw) if raw is not None else None

    def active_broadcasts(self, uow: View, limit: int) -> Sequence[BroadcastView]:
        rows = uow.connection.rows(
            "SELECT view_json FROM help_broadcast WHERE state = 'active' ORDER BY started_at, broadcast_id",
            (),
            limit=limit,
        )
        return tuple(codec.broadcast_from_json(_text(r[0])) for r in rows)


class SqliteGroundMessages(GroundMessageRepository):
    def get(self, uow: View, message_id: str) -> GroundMessage | None:
        raw = _one(
            uow, "SELECT message_json FROM help_ground_message WHERE ground_message_id = ?", (message_id,)
        )
        return codec.message_from_json(raw) if raw is not None else None

    def insert(self, uow: UnitOfWork, message: GroundMessage) -> None:
        connection = uow.connection
        connection.execute(
            "INSERT INTO help_ground_message (ground_message_id, help_session_id, revision, state, text_sha256,"
            " message_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                message.ground_message_id,
                message.help_session_id,
                message.revision,
                message.state,
                message.text_sha256,
                codec.message_to_json(message),
            ),
        )
        connection.execute(
            "INSERT INTO help_log_snapshot (ground_message_id, help_session_id, session_revision, snapshot_json)"
            " VALUES (?, ?, ?, ?)",
            (
                message.ground_message_id,
                message.help_session_id,
                message.session_revision,
                json.dumps(codec.snapshot_to_dict(message.snapshot), sort_keys=True, ensure_ascii=False),
            ),
        )

    def save(self, uow: UnitOfWork, message: GroundMessage, expected_revision: int) -> None:
        result = uow.connection.execute(
            "UPDATE help_ground_message SET revision = ?, state = ?, message_json = ?"
            " WHERE ground_message_id = ? AND revision = ?",
            (
                message.revision,
                message.state,
                codec.message_to_json(message),
                message.ground_message_id,
                expected_revision,
            ),
        )
        if result.affected_rows != 1:
            raise HelpStorageFault("the ground message revision moved")

    def snapshot(self, uow: View, message_id: str) -> HelpLogSnapshot | None:
        raw = _one(
            uow, "SELECT snapshot_json FROM help_log_snapshot WHERE ground_message_id = ?", (message_id,)
        )
        return codec.snapshot_from_dict(json.loads(raw)) if raw is not None else None
