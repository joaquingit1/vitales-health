"""SQLite repository of consent grants over the S5.7 StorageSession (E02 5). Grants are never deleted."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files

from crew_health.consent.domain.grants import (
    GRANT_STATES,
    ORIGIN_MODES,
    Grant,
    GrantRequest,
    GrantState,
    Issue,
    OriginMode,
)
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.storage import SqlValue, StorageSession

_COLUMNS = (
    "grant_id, revision, granted_by, subject_person_id, recipient, mission_id, origin_mode, object_ref, purpose, "
    "actions, fields, policy_version, valid_from, valid_until, issued_at, issued_epoch, issued_monotonic_ns, "
    "schedule_generation, state, ended_at"
)


def migration_sql() -> str:
    return (
        files("crew_health.consent.adapters")
        .joinpath("migrations/0001_consent.sql")
        .read_text(encoding="utf-8")
    )


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _corrupt() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise _corrupt()
    return value


def _when(value: SqlValue) -> datetime:
    return datetime.strptime(_text(value), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _integer(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise _corrupt()
    return value


def _names(value: SqlValue) -> frozenset[str]:
    decoded: object = json.loads(_text(value))
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise _corrupt()
    return frozenset(item for item in decoded if isinstance(item, str))


def _origin(value: SqlValue) -> OriginMode:
    for origin in ORIGIN_MODES:
        if origin == value:
            return origin
    raise _corrupt()


def _state(value: SqlValue) -> GrantState:
    for state in GRANT_STATES:
        if state == value:
            return state
    raise _corrupt()


def _grant(row: tuple[SqlValue, ...]) -> Grant:
    request = GrantRequest(
        recipient=_text(row[4]),
        mission_id=_text(row[5]),
        origin_mode=_origin(row[6]),
        object_ref=_text(row[7]),
        purpose=_text(row[8]),
        actions=_names(row[9]),
        fields=_names(row[10]),
        valid_from=_when(row[12]),
        valid_until=None if row[13] is None else _when(row[13]),
    )
    return Grant(
        grant_id=_text(row[0]),
        revision=_integer(row[1]),
        granted_by=_text(row[2]),
        subject_person_id=_text(row[3]),
        request=request,
        policy_version=_text(row[11]),
        issue=Issue(_when(row[14]), _text(row[15]), _integer(row[16])),
        schedule_generation=None if row[17] is None else _integer(row[17]),
        state=_state(row[18]),
        ended_at=None if row[19] is None else _when(row[19]),
    )


def _sorted_json(values: frozenset[str]) -> str:
    return json.dumps(sorted(values), separators=(",", ":"))


class SqliteConsentStore:
    def insert(self, connection: StorageSession, grant: Grant) -> None:
        value = grant.request
        connection.execute(
            f"INSERT INTO consent_grant ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                grant.grant_id,
                grant.revision,
                grant.granted_by,
                grant.subject_person_id,
                value.recipient,
                value.mission_id,
                value.origin_mode,
                value.object_ref,
                value.purpose,
                _sorted_json(value.actions),
                _sorted_json(value.fields),
                grant.policy_version,
                _instant(value.valid_from),
                None if value.valid_until is None else _instant(value.valid_until),
                _instant(grant.issue.at),
                grant.issue.process_epoch,
                grant.issue.monotonic_ns,
                grant.schedule_generation,
                grant.state,
                None if grant.ended_at is None else _instant(grant.ended_at),
            ),
        )

    def grant(self, connection: StorageSession, grant_id: str) -> Grant | None:
        rows = connection.rows(
            f"SELECT {_COLUMNS} FROM consent_grant WHERE grant_id = ?", (grant_id,), limit=1
        )
        return _grant(rows[0]) if rows else None

    def matching(
        self,
        connection: StorageSession,
        *,
        subject_person_id: str,
        recipient: str,
        purpose: str,
        object_ref: str,
        limit: int,
    ) -> tuple[Grant, ...]:
        rows = connection.rows(
            f"SELECT {_COLUMNS} FROM consent_grant WHERE subject_person_id = ? AND recipient = ? AND purpose = ? "
            "AND object_ref = ? AND state = 'active' ORDER BY grant_id LIMIT ?",
            (subject_person_id, recipient, purpose, object_ref, limit),
            limit=limit,
        )
        return tuple(_grant(row) for row in rows)

    def active_count(self, connection: StorageSession, subject_person_id: str) -> int:
        rows = connection.rows(
            "SELECT count(*) FROM consent_grant WHERE subject_person_id = ? AND state = 'active'",
            (subject_person_id,),
            limit=1,
        )
        return _integer(rows[0][0])

    def for_subject(
        self, connection: StorageSession, subject_person_id: str, *, limit: int
    ) -> tuple[Grant, ...]:
        rows = connection.rows(
            f"SELECT {_COLUMNS} FROM consent_grant WHERE subject_person_id = ? "
            "ORDER BY issued_at DESC, grant_id LIMIT ?",
            (subject_person_id, limit),
            limit=limit,
        )
        return tuple(_grant(row) for row in rows)

    def save_terminal(self, connection: StorageSession, grant: Grant, *, expected_revision: int) -> bool:
        if grant.ended_at is None:
            raise _corrupt()
        result = connection.execute(
            "UPDATE consent_grant SET state = ?, ended_at = ?, revision = ? "
            "WHERE grant_id = ? AND revision = ? AND state = 'active'",
            (grant.state, _instant(grant.ended_at), grant.revision, grant.grant_id, expected_revision),
        )
        return result.affected_rows == 1

    def fence_finite(self, connection: StorageSession, *, at: datetime, keep_epoch: str | None) -> int:
        """One statement fences the whole affected set, so no reader sees part of it."""
        result = connection.execute(
            "UPDATE consent_grant SET state = 'fenced', ended_at = ?, revision = revision + 1 "
            "WHERE state = 'active' AND valid_until IS NOT NULL AND (? IS NULL OR issued_epoch <> ?)",
            (_instant(at), keep_epoch, keep_epoch),
        )
        return result.affected_rows
