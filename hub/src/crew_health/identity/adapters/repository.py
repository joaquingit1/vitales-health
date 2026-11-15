"""SQLite repository of the identity module over the S5.7 StorageSession (E02 5)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.resources import files

from crew_health.identity.application.ports import RosterEntry
from crew_health.identity.application.store import Principal, RosterState
from crew_health.identity.domain.limits import Bucket, BucketScope
from crew_health.identity.domain.roster import NAMESPACES, Namespace, RosterBootstrap
from crew_health.identity.domain.session import (
    END_REASONS,
    PRINCIPAL_KINDS,
    EndReason,
    PrincipalKind,
    SessionRecord,
)
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.storage import SqlValue, StorageSession

_SESSION_COLUMNS = (
    "session_id, principal_id, kind, person_id, mission_id, generation, listener, process_epoch, "
    "created_at, created_monotonic_ns, expires_at, ended_at, end_reason"
)
_PRINCIPAL_COLUMNS = "principal_id, kind, person_id, mission_id, display_name, verifier, generation, enabled"
_MEMBER_COLUMNS = "person_id, display_name, roster_index, from_day, medical_officer_rank"
_SCOPES: tuple[BucketScope, ...] = ("principal", "client", "global")


def migration_sql() -> str:
    return (
        files("crew_health.identity.adapters")
        .joinpath("migrations/0001_identity.sql")
        .read_text(encoding="utf-8")
    )


def instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_instant(value: SqlValue) -> datetime:
    if not isinstance(value, str):
        raise _corrupt()
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _corrupt() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise _corrupt()
    return value


def _optional_text(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _integer(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise _corrupt()
    return value


def _optional_integer(value: SqlValue) -> int | None:
    return None if value is None else _integer(value)


def _kind(value: SqlValue) -> PrincipalKind:
    for kind in PRINCIPAL_KINDS:
        if kind == value:
            return kind
    raise _corrupt()


def _reason(value: SqlValue) -> EndReason | None:
    if value is None:
        return None
    for reason in END_REASONS:
        if reason == value:
            return reason
    raise _corrupt()


def _namespace(value: SqlValue) -> Namespace:
    for namespace in NAMESPACES:
        if namespace == value:
            return namespace
    raise _corrupt()


def _scope(value: SqlValue) -> BucketScope:
    for scope in _SCOPES:
        if scope == value:
            return scope
    raise _corrupt()


def _session(row: tuple[SqlValue, ...]) -> SessionRecord:
    ended = row[11]
    return SessionRecord(
        session_id=_text(row[0]),
        principal_id=_text(row[1]),
        kind=_kind(row[2]),
        person_id=_optional_text(row[3]),
        mission_id=_text(row[4]),
        generation=_integer(row[5]),
        listener=_text(row[6]),
        process_epoch=_text(row[7]),
        created_at=parse_instant(row[8]),
        created_monotonic_ns=_integer(row[9]),
        expires_at=parse_instant(row[10]),
        ended_at=None if ended is None else parse_instant(ended),
        end_reason=_reason(row[12]),
    )


def _principal(row: tuple[SqlValue, ...]) -> Principal:
    return Principal(
        principal_id=_text(row[0]),
        kind=_kind(row[1]),
        person_id=_optional_text(row[2]),
        mission_id=_text(row[3]),
        display_name=_text(row[4]),
        verifier=_optional_text(row[5]),
        generation=_integer(row[6]),
        enabled=_integer(row[7]) == 1,
    )


def _member(row: tuple[SqlValue, ...]) -> RosterEntry:
    return RosterEntry(
        person_id=_text(row[0]),
        display_name=_text(row[1]),
        roster_index=_integer(row[2]),
        on_roster_from_day=_integer(row[3]),
        medical_officer_rank=_optional_integer(row[4]),
    )


def _bucket(row: tuple[SqlValue, ...]) -> Bucket:
    return Bucket(_scope(row[0]), _text(row[1]), _integer(row[2]), _text(row[3]), _integer(row[4]))


class SqliteIdentityStore:
    """Every query is bounded; the bound is asserted by the storage session (S5.7)."""

    def roster_state(self, connection: StorageSession) -> RosterState | None:
        rows = connection.rows(
            "SELECT mission_id, namespace, revision FROM identity_roster WHERE singleton = 1", (), limit=1
        )
        if not rows:
            return None
        return RosterState(_text(rows[0][0]), _namespace(rows[0][1]), _integer(rows[0][2]))

    def save_roster_state(self, connection: StorageSession, state: RosterState) -> None:
        connection.execute(
            "INSERT INTO identity_roster VALUES (1, ?, ?, ?) ON CONFLICT (singleton) "
            "DO UPDATE SET revision = excluded.revision",
            (state.mission_id, state.namespace, state.revision),
        )

    def apply_roster(
        self, connection: StorageSession, document: RosterBootstrap, *, removed_at: datetime, limit: int
    ) -> tuple[str, ...]:
        existing = connection.rows(
            "SELECT person_id, removed_at FROM identity_person ORDER BY person_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        current = {_text(row[0]) for row in existing if row[1] is None}
        removed_ids = {_text(row[0]) for row in existing if row[1] is not None}
        wanted = {member.person_id for member in document.members}
        if wanted & removed_ids:
            raise ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
        removed = sorted(current - wanted)
        stamp = instant(removed_at)
        for person_id in removed:
            connection.execute(
                "UPDATE identity_person SET medical_officer_rank = NULL, removed_at = ?, revision = revision + 1 "
                "WHERE person_id = ?",
                (stamp, person_id),
            )
            connection.execute(
                "UPDATE identity_credential SET enabled = 0 WHERE principal_id = ?", (person_id,)
            )
        for member in sorted(document.members, key=lambda value: value.person_id):
            connection.execute(
                "UPDATE identity_person SET medical_officer_rank = NULL WHERE person_id = ? AND removed_at IS NULL",
                (member.person_id,),
            )
        for member in sorted(document.members, key=lambda value: value.roster_index):
            self._upsert_member(connection, document, member.person_id)
        return (*removed, *self._apply_principals(connection, document, limit=limit))

    def _upsert_member(self, connection: StorageSession, document: RosterBootstrap, person_id: str) -> None:
        member = next(value for value in document.members if value.person_id == person_id)
        connection.execute(
            "INSERT INTO identity_person VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1) ON CONFLICT (person_id) DO UPDATE SET "
            "display_name = excluded.display_name, roster_index = excluded.roster_index, "
            "from_day = excluded.from_day, until_day = excluded.until_day, "
            "medical_officer_rank = excluded.medical_officer_rank, revision = identity_person.revision + 1",
            (
                member.person_id,
                document.mission_id,
                document.namespace,
                member.display_name,
                member.roster_index,
                member.on_roster_from_day,
                member.on_roster_until_day,
                member.medical_officer_rank,
            ),
        )
        connection.execute(
            "INSERT INTO identity_credential VALUES (?, 'crew', ?, ?, ?, NULL, 1, 1) ON CONFLICT (principal_id) "
            "DO UPDATE SET display_name = excluded.display_name",
            (member.person_id, member.person_id, document.mission_id, member.display_name),
        )

    def _apply_principals(
        self, connection: StorageSession, document: RosterBootstrap, *, limit: int
    ) -> tuple[str, ...]:
        rows = connection.rows(
            "SELECT principal_id, kind FROM identity_credential WHERE kind <> 'crew' AND enabled = 1 "
            "ORDER BY principal_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        wanted = {value.principal_id: value for value in document.principals}
        retired = sorted(
            _text(row[0])
            for row in rows
            if _text(row[0]) not in wanted or wanted[_text(row[0])].kind != row[1]
        )
        for principal_id in retired:
            connection.execute(
                "UPDATE identity_credential SET enabled = 0 WHERE principal_id = ?", (principal_id,)
            )
        for principal in sorted(document.principals, key=lambda value: value.principal_id):
            known = self.principal(connection, principal.principal_id)
            if known is not None and (known.kind != principal.kind or not known.enabled):
                raise ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
            connection.execute(
                "INSERT INTO identity_credential VALUES (?, ?, NULL, ?, ?, NULL, 1, 1) ON CONFLICT (principal_id) "
                "DO UPDATE SET display_name = excluded.display_name",
                (principal.principal_id, principal.kind, document.mission_id, principal.display_name),
            )
        return tuple(retired)

    def roster(
        self, connection: StorageSession, *, after_index: int | None, limit: int
    ) -> tuple[RosterEntry, ...]:
        rows = connection.rows(
            f"SELECT {_MEMBER_COLUMNS} FROM identity_person WHERE removed_at IS NULL AND roster_index > ? "
            "ORDER BY roster_index LIMIT ?",
            (-1 if after_index is None else after_index, limit),
            limit=limit,
        )
        return tuple(_member(row) for row in rows)

    def roster_member(self, connection: StorageSession, person_id: str) -> RosterEntry | None:
        rows = connection.rows(
            f"SELECT {_MEMBER_COLUMNS} FROM identity_person WHERE person_id = ? AND removed_at IS NULL",
            (person_id,),
            limit=1,
        )
        return _member(rows[0]) if rows else None

    def principal(self, connection: StorageSession, principal_id: str) -> Principal | None:
        rows = connection.rows(
            f"SELECT {_PRINCIPAL_COLUMNS} FROM identity_credential WHERE principal_id = ?",
            (principal_id,),
            limit=1,
        )
        return _principal(rows[0]) if rows else None

    def principal_for_kind(self, connection: StorageSession, kind: PrincipalKind) -> Principal | None:
        rows = connection.rows(
            f"SELECT {_PRINCIPAL_COLUMNS} FROM identity_credential WHERE kind = ? AND enabled = 1 "
            "ORDER BY principal_id LIMIT 2",
            (kind,),
            limit=2,
        )
        if len(rows) != 1 or kind in ("crew", "adapter"):
            return None
        return _principal(rows[0])

    def set_verifier(
        self, connection: StorageSession, principal_id: str, verifier: str, generation: int
    ) -> None:
        result = connection.execute(
            "UPDATE identity_credential SET verifier = ?, generation = ? WHERE principal_id = ? AND generation = ?",
            (verifier, generation, principal_id, generation - 1),
        )
        if result.affected_rows != 1:
            raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)

    def session_by_digest(self, connection: StorageSession, digest: str) -> SessionRecord | None:
        rows = connection.rows(
            f"SELECT {_SESSION_COLUMNS} FROM identity_session WHERE token_digest = ?", (digest,), limit=1
        )
        return _session(rows[0]) if rows else None

    def session_by_id(self, connection: StorageSession, session_id: str) -> SessionRecord | None:
        rows = connection.rows(
            f"SELECT {_SESSION_COLUMNS} FROM identity_session WHERE session_id = ?", (session_id,), limit=1
        )
        return _session(rows[0]) if rows else None

    def live_sessions(
        self, connection: StorageSession, *, principal_id: str | None, now: datetime, limit: int
    ) -> tuple[SessionRecord, ...]:
        rows = connection.rows(
            f"SELECT {_SESSION_COLUMNS} FROM identity_session WHERE ended_at IS NULL AND expires_at > ? "
            "AND (? IS NULL OR principal_id = ?) ORDER BY created_at, session_id LIMIT ?",
            (instant(now), principal_id, principal_id, limit),
            limit=limit,
        )
        return tuple(_session(row) for row in rows)

    def count_live(self, connection: StorageSession, *, now: datetime) -> int:
        rows = connection.rows(
            "SELECT count(*) FROM identity_session WHERE ended_at IS NULL AND expires_at > ?",
            (instant(now),),
            limit=1,
        )
        return _integer(rows[0][0])

    def insert_session(self, connection: StorageSession, record: SessionRecord, digest: str) -> None:
        connection.execute(
            f"INSERT INTO identity_session ({_SESSION_COLUMNS}, token_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (
                record.session_id,
                record.principal_id,
                record.kind,
                record.person_id,
                record.mission_id,
                record.generation,
                record.listener,
                record.process_epoch,
                instant(record.created_at),
                record.created_monotonic_ns,
                instant(record.expires_at),
                digest,
            ),
        )

    def end_session(
        self, connection: StorageSession, session_id: str, *, at: datetime, reason: EndReason
    ) -> bool:
        result = connection.execute(
            "UPDATE identity_session SET ended_at = ?, end_reason = ? WHERE session_id = ? AND ended_at IS NULL",
            (instant(at), reason, session_id),
        )
        return result.affected_rows == 1

    def stale_epoch_sessions(
        self, connection: StorageSession, *, process_epoch: str, limit: int
    ) -> tuple[str, ...]:
        rows = connection.rows(
            "SELECT session_id FROM identity_session WHERE ended_at IS NULL AND process_epoch <> ? "
            "ORDER BY session_id LIMIT ?",
            (process_epoch, limit),
            limit=limit,
        )
        return tuple(_text(row[0]) for row in rows)

    def bump_access_generation(self, connection: StorageSession) -> int:
        connection.execute(
            "UPDATE identity_access_generation SET generation = generation + 1 WHERE singleton = 1"
        )
        return self.access_generation(connection)

    def access_generation(self, connection: StorageSession) -> int:
        rows = connection.rows(
            "SELECT generation FROM identity_access_generation WHERE singleton = 1", (), limit=1
        )
        if not rows:
            raise _corrupt()
        return _integer(rows[0][0])

    def buckets(
        self, connection: StorageSession, keys: Sequence[tuple[BucketScope, str]]
    ) -> tuple[Bucket, ...]:
        found: list[Bucket] = []
        for scope, key in keys:
            rows = connection.rows(
                "SELECT scope, bucket_key, anchor_ns, process_epoch, attempts FROM identity_auth_bucket "
                "WHERE scope = ? AND bucket_key = ?",
                (scope, key),
                limit=1,
            )
            found.extend(_bucket(row) for row in rows)
        return tuple(found)

    def count_buckets(self, connection: StorageSession) -> int:
        rows = connection.rows("SELECT count(*) FROM identity_auth_bucket", (), limit=1)
        return _integer(rows[0][0])

    def oldest_buckets(self, connection: StorageSession, *, limit: int) -> tuple[Bucket, ...]:
        rows = connection.rows(
            "SELECT scope, bucket_key, anchor_ns, process_epoch, attempts FROM identity_auth_bucket "
            "ORDER BY attempts, anchor_ns, scope, bucket_key LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(_bucket(row) for row in rows)

    def save_bucket(self, connection: StorageSession, bucket: Bucket) -> None:
        connection.execute(
            "INSERT INTO identity_auth_bucket VALUES (?, ?, ?, ?, ?) ON CONFLICT (scope, bucket_key) DO UPDATE SET "
            "anchor_ns = excluded.anchor_ns, process_epoch = excluded.process_epoch, attempts = excluded.attempts",
            (bucket.scope, bucket.key, bucket.anchor_ns, bucket.process_epoch, bucket.attempts),
        )

    def delete_bucket(self, connection: StorageSession, bucket: Bucket) -> None:
        connection.execute(
            "DELETE FROM identity_auth_bucket WHERE scope = ? AND bucket_key = ?", (bucket.scope, bucket.key)
        )
