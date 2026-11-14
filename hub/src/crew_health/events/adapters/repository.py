from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import SchemaRegistry

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.event_schema import Event
from crew_health.events.domain.errors import failure
from crew_health.kernel.clock import require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.kernel.uow import OutboxEvent, ReplicationChange, StoredEvent, UnitOfWork


def instant(value: datetime) -> str:
    require_utc(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def document(raw: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(raw, object_pairs_hook=_pairs)
        canonical_json(value)
        return TypeAdapter(dict[str, object]).validate_python(value)
    except (ValueError, RecursionError, ValidationError, UnicodeError) as error:
        raise failure(ErrorCode.MALFORMED_REQUEST) from error


def wire_event(value: StoredEvent) -> Event:
    return Event.model_validate(
        {
            "seq": value.seq,
            "event_id": value.event_id,
            "type": value.type.value,
            "occurred_at": instant(value.occurred_at),
            "emitted_at": instant(value.emitted_at),
            "scope": {
                "kind": value.scope.kind,
                "person_id": value.scope.person_id,
                "environment_id": value.scope.environment_id,
            },
            "config_release_id": value.config_release_id,
            "correlation_id": value.correlation_id,
            "data": document(value.data_json),
        }
    )


class EventValidator:
    def __init__(self, *, schema_directory: Path) -> None:
        paths = sorted(schema_directory.glob("*.schema.json"))
        if not paths or len(paths) > 1024:
            raise failure(ErrorCode.CONFIG_INVALID)
        schemas = {path.name: document(path.read_bytes()) for path in paths}
        if "event.schema.json" not in schemas:
            raise failure(ErrorCode.CONFIG_INVALID)
        registry: SchemaRegistry = Registry()
        registry = registry.with_resources(
            (name, Resource.from_contents(schema)) for name, schema in schemas.items()
        )
        schema = schemas["event.schema.json"]
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())

    def validate(self, value: StoredEvent) -> Event:
        try:
            event = wire_event(value)
            if not self.validator.is_valid(event.model_dump(mode="json")):
                raise failure(ErrorCode.INVALID_VALUE)
            return event
        except ValidationError as error:
            raise failure(ErrorCode.INVALID_VALUE) from error


class EventRepository:
    def __init__(self, *, settings: HubSettings, ids: IdFactory, validator: EventValidator) -> None:
        self.settings, self.ids, self.validator = settings, ids, validator
        self.codec = TypeAdapter(StoredEvent)

    @staticmethod
    def wire(value: StoredEvent) -> Event:
        return wire_event(value)

    def append(self, uow: UnitOfWork, event: OutboxEvent) -> StoredEvent:
        if len(event.data_json) > self.settings.integer("commands.max_bytes"):
            raise failure(ErrorCode.PAYLOAD_TOO_LARGE)
        if event.scope.kind == "person" and event.scope.person_id is None:
            raise failure(ErrorCode.INVALID_VALUE)
        if not event.audience.rule or not event.audience.object_ref or not event.audience.policy_version:
            raise failure(ErrorCode.INVALID_VALUE)
        seq = self.head(uow.connection) + 1
        stored = StoredEvent(
            seq,
            self.ids.new("evn"),
            event.type,
            event.occurred_at,
            uow.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )
        self.validator.validate(stored)
        raw = self.codec.dump_json(stored)
        result = uow.connection.execute(
            "INSERT INTO events_outbox(seq,event_id,event_type,emitted_at,envelope,content_hash) VALUES(?,?,?,?,?,?)",
            (
                seq,
                stored.event_id,
                stored.type.value,
                instant(stored.emitted_at),
                raw,
                hashlib.sha256(raw).hexdigest(),
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")
        uow.replicate(
            ReplicationChange(
                None,
                "events.outbox",
                stored.event_id,
                "append",
                raw,
                (),
                stored.config_release_id,
                stored.audience,
            )
        )
        return stored

    @staticmethod
    def watermark(connection: StorageSession) -> int:
        rows = connection.rows("SELECT watermark FROM events_retention WHERE singleton=1", (), limit=1)
        if not rows or type(rows[0][0]) is not int:
            raise StorageUnavailable("corrupt")
        return rows[0][0]

    @classmethod
    def head(cls, connection: StorageSession) -> int:
        rows = connection.rows("SELECT MAX(seq) FROM events_outbox", (), limit=1)
        high = rows[0][0] if rows else None
        if high is not None and type(high) is not int:
            raise StorageUnavailable("corrupt")
        return max(cls.watermark(connection), high or 0)

    def get(self, connection: StorageSession, sequence: int) -> StoredEvent | None:
        rows = connection.rows(
            "SELECT seq,event_id,event_type,emitted_at,envelope,content_hash FROM events_outbox WHERE seq=?",
            (sequence,),
            limit=1,
        )
        return self._decode(rows[0]) if rows else None

    def scan(self, connection: StorageSession, *, after: int, limit: int) -> tuple[StoredEvent, ...]:
        if not 1 <= limit <= self.settings.integer("events.page_size"):
            raise failure(ErrorCode.PAYLOAD_TOO_LARGE)
        rows = connection.rows(
            "SELECT seq,event_id,event_type,emitted_at,envelope,content_hash FROM events_outbox WHERE seq>? ORDER BY seq LIMIT ?",
            (after, limit),
            limit=limit,
        )
        return tuple(self._decode(row) for row in rows)

    def _decode(self, row: tuple[SqlValue, ...]) -> StoredEvent:
        raw = row[4]
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != row[5]:
            raise StorageUnavailable("corrupt")
        try:
            value = self.codec.validate_json(raw)
            if (
                value.seq != row[0]
                or value.event_id != row[1]
                or value.type.value != row[2]
                or instant(value.emitted_at) != row[3]
            ):
                raise StorageUnavailable("corrupt")
            self.validator.validate(value)
            return value
        except (ValidationError, ValueError) as error:
            raise StorageUnavailable("corrupt") from error

    @staticmethod
    def cursor(connection: StorageSession, subscriber: str) -> int:
        rows = connection.rows("SELECT seq FROM events_cursor WHERE subscriber=?", (subscriber,), limit=1)
        if not rows:
            return 0
        if type(rows[0][0]) is not int:
            raise StorageUnavailable("corrupt")
        return rows[0][0]

    @staticmethod
    def advance_cursor(connection: StorageSession, subscriber: str, seq: int) -> None:
        result = connection.execute(
            "INSERT INTO events_cursor(subscriber,seq) VALUES(?,?) ON CONFLICT(subscriber) DO UPDATE SET seq=excluded.seq",
            (subscriber, seq),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    @staticmethod
    def oldest_recent(connection: StorageSession, *, cutoff: datetime) -> int | None:
        rows = connection.rows(
            "SELECT MIN(seq) FROM events_outbox WHERE emitted_at>?", (instant(cutoff),), limit=1
        )
        if not rows or rows[0][0] is None:
            return None
        if type(rows[0][0]) is not int:
            raise StorageUnavailable("corrupt")
        return rows[0][0]

    @staticmethod
    def delete_through(connection: StorageSession, through: int) -> int:
        return connection.execute("DELETE FROM events_outbox WHERE seq<=?", (through,)).affected_rows
