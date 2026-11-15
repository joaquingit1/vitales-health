"""SYSTEM 5.7: bounded access to catchup tables only, using the caller's snapshot or transaction."""

import json
from datetime import datetime
from typing import cast

from crew_health.catchup.application.ports import QuarantineRecord
from crew_health.catchup.application.repository import Message
from crew_health.catchup.application.settings import MAX_LINES_CEILING
from crew_health.catchup.domain.compose import Composition
from crew_health.catchup.domain.errors import CatchUpUnavailable, CompositionTooLarge
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


def instant(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def _message(row: tuple[SqlValue, ...]) -> Message:
    return Message(
        str(row[0]),
        str(row[1]),
        int(cast(int, row[2])),
        datetime.fromisoformat(str(row[3])),
        str(row[4]),
        bool(row[5]),
        str(row[6]),
        None if row[7] is None else str(row[7]),
        None if row[8] is None else datetime.fromisoformat(str(row[8])),
        None if row[9] is None else str(row[9]),
    )


class SqliteCatchUpRepository:
    def __init__(self, max_lines: int) -> None:
        self._max_lines = max_lines

    def rows(self, view: ReadSnapshot, *, limit: int) -> tuple[Message, ...]:
        return tuple(
            _message(row)
            for row in view.connection.rows(
                "SELECT * FROM catchup_message ORDER BY queued_at, ground_message_id LIMIT ?",
                (limit,),
                limit=limit,
            )
        )

    def message(self, view: ReadSnapshot, ref_id: str) -> Message | None:
        rows = view.connection.rows(
            "SELECT * FROM catchup_message WHERE ground_message_id = ?", (ref_id,), limit=1
        )
        return _message(rows[0]) if rows else None

    def for_revision(self, view: ReadSnapshot, session_id: str, revision: int) -> Message | None:
        rows = view.connection.rows(
            "SELECT * FROM catchup_message WHERE help_session_id = ? AND session_revision = ?",
            (session_id, revision),
            limit=1,
        )
        return _message(rows[0]) if rows else None

    def insert(self, uow: UnitOfWork, message: Message) -> None:
        previous = self.message(uow, message.ground_message_id)
        if previous is not None:
            if (
                previous.help_session_id,
                previous.session_revision,
                previous.queued_at,
                previous.title,
                previous.simulated,
            ) != (
                message.help_session_id,
                message.session_revision,
                message.queued_at,
                message.title,
                message.simulated,
            ):
                raise CatchUpUnavailable()
            return
        uow.connection.execute(
            "INSERT INTO catchup_message VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL)",
            (
                message.ground_message_id,
                message.help_session_id,
                message.session_revision,
                instant(message.queued_at),
                message.title,
                int(message.simulated),
            ),
        )

    def lines(self, view: ReadSnapshot, ref_id: str) -> Composition:
        sections = []
        for section in ("what_happened", "what_was_done", "being_watched", "advice_welcome"):
            # Frozen content outlives the current admission limit. Aggregate one bounded section so even
            # storage.max_rows below the absolute line ceiling can read every approved line.
            rows = view.connection.rows(
                "SELECT json_group_array(text) FROM (SELECT text FROM catchup_line "
                "WHERE ground_message_id = ? AND section = ? ORDER BY idx LIMIT ?)",
                (ref_id, section, MAX_LINES_CEILING + 1),
                limit=1,
            )
            values: object = json.loads(str(rows[0][0]))
            if not isinstance(values, list) or len(values) > MAX_LINES_CEILING:
                raise CatchUpUnavailable()
            if not all(isinstance(text, str) and text for text in values):
                raise CatchUpUnavailable()
            sections.append(tuple(str(text) for text in values))
        if not sections[0]:
            raise CatchUpUnavailable()
        return Composition(*sections)

    def freeze(
        self, uow: UnitOfWork, ref_id: str, lines: Composition, pinned: str, subject_person_id: str | None
    ) -> Composition:
        message = self.message(uow, ref_id)
        if message is None:
            raise CatchUpUnavailable()
        if message.state == "frozen":
            return self.lines(uow, ref_id)
        if any(len(texts) > self._max_lines for texts in lines.sections()):
            raise CompositionTooLarge()
        for section, texts in zip(
            ("what_happened", "what_was_done", "being_watched", "advice_welcome"),
            lines.sections(),
            strict=True,
        ):
            for idx, text in enumerate(texts):
                uow.connection.execute(
                    "INSERT INTO catchup_line VALUES (?, ?, ?, ?)", (ref_id, section, idx, text)
                )
        uow.connection.execute(
            "UPDATE catchup_message SET state = 'frozen', pinned_versions = ?, composed_at = ?, "
            "subject_person_id = ? WHERE ground_message_id = ?",
            (pinned, instant(uow.now), subject_person_id, ref_id),
        )
        return lines

    def quarantine(self, uow: UnitOfWork, record: QuarantineRecord, event_type: str) -> None:
        uow.connection.execute(
            "INSERT OR IGNORE INTO catchup_quarantine VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.event_id,
                record.subscriber,
                event_type,
                record.reason,
                record.attempts,
                instant(record.quarantined_at),
            ),
        )

    def quarantined(self, view: ReadSnapshot, *, limit: int) -> tuple[QuarantineRecord, ...]:
        rows = view.connection.rows(
            "SELECT event_id, subscriber, reason, attempts, quarantined_at FROM catchup_quarantine "
            "ORDER BY quarantined_at, event_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(
            QuarantineRecord(
                str(r[0]), str(r[1]), str(r[2]), int(cast(int, r[3])), datetime.fromisoformat(str(r[4]))
            )
            for r in rows
        )
