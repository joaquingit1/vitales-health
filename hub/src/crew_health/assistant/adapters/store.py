from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from crew_health.assistant.application.dto import Assessment, Conversation, RuntimeRecord, Work
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork

_CONVERSATION = TypeAdapter(Conversation)
_WORK = TypeAdapter(Work)
_RUNTIME = TypeAdapter(RuntimeRecord)


class Store:
    def __init__(self, *, max_rows: int, max_body_bytes: int) -> None:
        if type(max_rows) is not int or type(max_body_bytes) is not int or min(max_rows, max_body_bytes) < 1:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "assistant storage bounds invalid")
        self.max_rows, self.max_body_bytes = max_rows, max_body_bytes

    def _bounded(self, raw: bytes) -> bytes:
        if len(raw) > self.max_body_bytes:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant storage bound exceeded")
        return raw

    def _rows(self, view: View, sql: str, values: tuple[SqlValue, ...]) -> tuple[tuple[SqlValue, ...], ...]:
        rows = view.connection.rows(sql, values, limit=self.max_rows + 1)
        if len(rows) > self.max_rows:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant page bound exceeded")
        return rows

    def create_conversation(self, uow: UnitOfWork, value: Conversation) -> None:
        raw = self._bounded(_CONVERSATION.dump_json(value))
        uow.connection.execute(
            "INSERT INTO assistant_conversation VALUES (?,?,?,?,?,?,?)",
            (
                value.conversation_id,
                value.person_id,
                value.context_kind,
                value.context_ref_id,
                value.state,
                value.revision,
                raw,
            ),
        )

    def conversation(self, view: View, ref: str) -> Conversation | None:
        rows = view.connection.rows(
            "SELECT body FROM assistant_conversation WHERE conversation_id=?", (ref,), limit=1
        )
        if not rows:
            return None
        try:
            return _CONVERSATION.validate_json(self._bounded(_blob(rows[0][0])), strict=True)
        except ValidationError as error:
            raise StorageUnavailable("corrupt") from error

    def open_conversation(self, view: View, person: str, kind: str, ref: str | None) -> Conversation | None:
        rows = view.connection.rows(
            "SELECT conversation_id FROM assistant_conversation WHERE person_id=? AND context_kind=? AND context_ref_id IS ? AND state='open'",
            (person, kind, ref),
            limit=1,
        )
        return self.conversation(view, _text(rows[0][0])) if rows else None

    def append_message(self, uow: UnitOfWork, body: Mapping[str, object]) -> None:
        raw = self._bounded(canonical_json(dict(body)))
        conversation = _text_value(body.get("conversation_id"))
        current = self.conversation(uow, conversation)
        if current is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "conversation not found")
        seq = uow.connection.rows(
            "SELECT coalesce(max(seq),0)+1 FROM assistant_message WHERE conversation_id=?",
            (conversation,),
            limit=1,
        )[0][0]
        uow.connection.execute(
            "INSERT INTO assistant_message VALUES (?,?,?,?)",
            (_text_value(body.get("message_id")), conversation, seq, raw),
        )
        updated = replace(current, revision=current.revision + 1)
        result = uow.connection.execute(
            "UPDATE assistant_conversation SET revision=?,body=? WHERE conversation_id=? AND revision=?",
            (
                updated.revision,
                self._bounded(_CONVERSATION.dump_json(updated)),
                conversation,
                current.revision,
            ),
        )
        _changed(result.affected_rows)

    def messages(self, view: View, conversation_id: str) -> tuple[Mapping[str, object], ...]:
        rows = self._rows(
            view,
            "SELECT body FROM assistant_message WHERE conversation_id=? ORDER BY seq",
            (conversation_id,),
        )
        return tuple(self.object(_blob(row[0])) for row in rows)

    def create_work(self, uow: UnitOfWork, value: Work) -> None:
        raw = self._bounded(_WORK.dump_json(value))
        uow.connection.execute(
            "INSERT INTO assistant_turn VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                value.turn_id,
                value.task_id,
                value.conversation_id,
                value.episode_id,
                value.kind,
                value.state.value,
                value.priority,
                value.created_at.isoformat(),
                value.continuation_key,
                raw,
            ),
        )
        self._work_history(uow, value, raw)

    def work(self, view: View, turn_id: str) -> Work | None:
        rows = view.connection.rows("SELECT body FROM assistant_turn WHERE turn_id=?", (turn_id,), limit=1)
        if not rows:
            return None
        try:
            return _WORK.validate_json(self._bounded(_blob(rows[0][0])), strict=True)
        except ValidationError as error:
            raise StorageUnavailable("corrupt") from error

    def active(self, view: View, conversation_id: str) -> Work | None:
        rows = view.connection.rows(
            "SELECT turn_id FROM assistant_turn WHERE conversation_id=? AND state IN ('queued','running','committing')",
            (conversation_id,),
            limit=1,
        )
        return self.work(view, _text(rows[0][0])) if rows else None

    def update_work(self, uow: UnitOfWork, before: Work, after: Work) -> None:
        immutable = replace(
            after,
            state=before.state,
            preemptions=before.preemptions,
            steps_used=before.steps_used,
            error_code=before.error_code,
        )
        if immutable != before:
            raise ProblemError(ErrorCode.IDENTITY_MISMATCH, "assistant work identity changed")
        raw = self._bounded(_WORK.dump_json(after))
        changed = uow.connection.execute(
            "UPDATE assistant_turn SET state=?,body=? WHERE turn_id=? AND body=?",
            (after.state.value, raw, before.turn_id, self._bounded(_WORK.dump_json(before))),
        )
        _changed(changed.affected_rows)
        self._work_history(uow, after, raw)

    def _work_history(self, uow: UnitOfWork, value: Work, raw: bytes) -> None:
        uow.connection.execute(
            "INSERT INTO assistant_turn_history SELECT ?,coalesce(max(seq),0)+1,?,?,? FROM assistant_turn_history WHERE turn_id=?",
            (value.turn_id, value.state.value, uow.now.isoformat(), raw, value.turn_id),
        )

    def pending(self, view: View) -> tuple[Work, ...]:
        rows = self._rows(
            view,
            "SELECT body FROM assistant_turn WHERE state IN ('queued','running','committing') ORDER BY priority,created_at,turn_id",
            (),
        )
        try:
            return tuple(_WORK.validate_json(self._bounded(_blob(row[0])), strict=True) for row in rows)
        except ValidationError as error:
            raise StorageUnavailable("corrupt") from error

    def runtime(self, view: View) -> RuntimeRecord | None:
        rows = view.connection.rows("SELECT body FROM assistant_runtime_state WHERE id=1", (), limit=1)
        if not rows:
            return None
        try:
            return _RUNTIME.validate_json(self._bounded(_blob(rows[0][0])), strict=True)
        except ValidationError as error:
            raise StorageUnavailable("corrupt") from error

    def set_runtime(self, uow: UnitOfWork, value: RuntimeRecord) -> None:
        raw = self._bounded(_RUNTIME.dump_json(value))
        uow.connection.execute(
            "INSERT INTO assistant_runtime_state(id,body) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,version=version+1",
            (raw,),
        )
        self._runtime_history(uow, value, raw)

    def _runtime_history(self, uow: UnitOfWork, value: RuntimeRecord, raw: bytes) -> None:
        uow.connection.execute(
            "INSERT INTO assistant_runtime_history SELECT coalesce(max(seq),0)+1,?,?,? FROM assistant_runtime_history",
            (value.runtime.state.value, uow.now.isoformat(), raw),
        )

    def runtime_version(self, view: View) -> int:
        rows = view.connection.rows("SELECT version FROM assistant_runtime_state WHERE id=1", (), limit=1)
        if not rows:
            return 0
        value = rows[0][0]
        if type(value) is not int or value < 1:
            raise StorageUnavailable("corrupt")
        return value

    def replace_runtime(self, uow: UnitOfWork, value: RuntimeRecord, *, expected_version: int) -> bool:
        if type(expected_version) is not int or expected_version < 1:
            raise ProblemError(ErrorCode.INVALID_VALUE, "runtime revision invalid")
        raw = self._bounded(_RUNTIME.dump_json(value))
        changed = uow.connection.execute(
            "UPDATE assistant_runtime_state SET body=?,version=version+1 WHERE id=1 AND version=?",
            (raw, expected_version),
        )
        if changed.affected_rows == 0:
            return False
        self._runtime_history(uow, value, raw)
        return True

    def create_assessment(
        self, uow: UnitOfWork, value: Assessment, refs: Sequence[tuple[str, int, str, str]]
    ) -> None:
        self._bounded(value.body)
        if len(refs) > self.max_rows:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant support bound exceeded")
        uow.connection.execute(
            "INSERT INTO assistant_assessment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                value.assessment_id,
                value.episode_id,
                value.person_id,
                value.body,
                value.completeness,
                value.stop_reason,
                value.config_release_id,
                value.turn_id,
                value.job_id,
                value.created_at.isoformat(),
                value.validation_status,
                value.cause_ref,
                value.review_id,
                value.revision,
                value.origin_mode,
            ),
        )
        for section, index, kind, ref in dict.fromkeys(refs):
            uow.connection.execute(
                "INSERT INTO assistant_claim_ref VALUES (?,?,?,?,?)",
                (value.assessment_id, section, index, kind, ref),
            )
        self._assessment_history(
            uow, value.assessment_id, value.validation_status, value.cause_ref, value.review_id
        )

    def assessment(self, view: View, ref: str) -> Assessment | None:
        rows = view.connection.rows(
            "SELECT * FROM assistant_assessment WHERE assessment_id=?", (ref,), limit=1
        )
        if not rows:
            return None
        r = rows[0]
        return Assessment(
            _text(r[0]),
            _text(r[1]),
            _text(r[2]),
            self._bounded(_blob(r[3])),
            cast(Literal["complete", "incomplete"], _text(r[4])),
            _optional(r[5]),
            _text(r[6]),
            _text(r[7]),
            _text(r[8]),
            datetime.fromisoformat(_text(r[9])),
            cast(Literal["draft", "stale", "reviewed"], _text(r[10])),
            _optional(r[11]),
            _optional(r[12]),
            cast(int, r[13]),
            origin_mode=cast(Literal["LIVE", "REPLAY", "SIM"], _text(r[14])),
        )

    def mark_assessment(
        self, uow: UnitOfWork, ref: str, *, state: str, cause: str | None = None, review: str | None = None
    ) -> None:
        changed = uow.connection.execute(
            "UPDATE assistant_assessment SET validation_status=?,cause_ref=?,review_id=coalesce(?,review_id),revision=revision+1 WHERE assessment_id=?",
            (state, cause, review, ref),
        )
        _changed(changed.affected_rows)
        self._assessment_history(uow, ref, state, cause, review)

    def _assessment_history(
        self, uow: UnitOfWork, ref: str, state: str, cause: str | None, review: str | None
    ) -> None:
        uow.connection.execute(
            "INSERT INTO assistant_assessment_history SELECT ?,coalesce(max(seq),0)+1,?,?,?,? FROM assistant_assessment_history WHERE assessment_id=?",
            (ref, state, cause, review, uow.now.isoformat(), ref),
        )

    def citing(self, view: View, episode: str, refs: Sequence[str]) -> tuple[str, ...]:
        if len(refs) > self.max_rows:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant reference bound exceeded")
        found: set[str] = set()
        for ref in dict.fromkeys(refs):
            found.update(
                _text(r[0])
                for r in self._rows(
                    view,
                    "SELECT DISTINCT a.assessment_id FROM assistant_assessment a JOIN assistant_claim_ref c USING(assessment_id) WHERE a.episode_id=? AND c.ref_id=? ORDER BY a.assessment_id",
                    (episode, ref),
                )
            )
            if len(found) > self.max_rows:
                raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "assistant reference bound exceeded")
        return tuple(sorted(found))

    def assessment_ids(self, view: View, episode: str) -> tuple[str, ...]:
        return tuple(
            _text(r[0])
            for r in self._rows(
                view,
                "SELECT assessment_id FROM assistant_assessment WHERE episode_id=? ORDER BY created_at,assessment_id",
                (episode,),
            )
        )

    def object(self, raw: bytes) -> Mapping[str, object]:
        self._bounded(raw)
        try:
            result: object = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise StorageUnavailable("corrupt") from error
        if not isinstance(result, dict):
            raise StorageUnavailable("corrupt")
        return cast(dict[str, object], result)


def _blob(value: SqlValue) -> bytes:
    if not isinstance(value, bytes):
        raise StorageUnavailable("corrupt")
    return value


def _text(value: SqlValue) -> str:
    return _text_value(value)


def _text_value(value: object) -> str:
    if not isinstance(value, str):
        raise StorageUnavailable("corrupt")
    return value


def _optional(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _changed(count: int) -> None:
    if count != 1:
        raise ProblemError(ErrorCode.STALE_VERSION, "assistant projection changed")
