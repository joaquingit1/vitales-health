from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from crew_health.contracts_gen.enums import ErrorCode, EventType, Role
from crew_health.contracts_gen.schemas.event_page_schema import EventPage
from crew_health.contracts_gen.schemas.event_schema import Event
from crew_health.events.domain.cursor import cursor
from crew_health.events.domain.errors import failure
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import (
    AudienceRule,
    LocalProjectionRegistration,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
)


class SessionAuthorizer(Protocol):
    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool: ...


class EventStore(Protocol):
    @staticmethod
    def watermark(connection: StorageSession) -> int: ...
    @staticmethod
    def head(connection: StorageSession) -> int: ...
    def get(self, connection: StorageSession, sequence: int) -> StoredEvent | None: ...
    def scan(self, connection: StorageSession, *, after: int, limit: int) -> tuple[StoredEvent, ...]: ...
    @staticmethod
    def wire(value: StoredEvent) -> Event: ...
    @staticmethod
    def cursor(connection: StorageSession, subscriber: str) -> int | None: ...
    @staticmethod
    def advance_cursor(connection: StorageSession, subscriber: str, seq: int) -> None: ...
    @staticmethod
    def oldest_recent(connection: StorageSession, *, cutoff: datetime) -> int | None: ...
    @staticmethod
    def delete_through(connection: StorageSession, through: int) -> int: ...


def _has_pointer(value: Mapping[str, object], pointer: str) -> bool:
    if not pointer.startswith("/") or len(pointer) > 1048576:
        return False
    parts = pointer[1:].split("/")
    if len(parts) > 64:
        return False
    selected: object = value
    for part in parts:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(selected, dict):
            if key not in selected:
                return False
            selected = selected[key]
        elif isinstance(selected, list) and key.isdecimal() and len(key) <= 19 and int(key) < len(selected):
            selected = selected[int(key)]
        else:
            return False
        if selected is None and part != parts[-1]:
            return False
    return True


class EventDelivery:
    def __init__(
        self,
        *,
        repository: EventStore,
        settings: HubSettings,
        snapshots: ReadSnapshotFactory,
        authorizer: SessionAuthorizer,
        projections: Sequence[LocalProjectionRegistration],
        audiences: Mapping[str, AudienceRule],
    ) -> None:
        if len(projections) > settings.integer("registry.max_hooks") or len(audiences) > settings.integer(
            "registry.max_hooks"
        ):
            raise failure(ErrorCode.CONFIG_INVALID)
        self.repository, self.settings, self.snapshots, self.authorizer = (
            repository,
            settings,
            snapshots,
            authorizer,
        )
        self.projections = tuple(projections)
        self.audiences = dict(audiences)
        self._registrations = {registration.key: registration for registration in projections}
        if len(self._registrations) != len(projections):
            raise failure(ErrorCode.CONFIG_INVALID)
        self._validators = {}
        for registration in projections:
            schema = TypeAdapter(dict[str, object]).validate_json(registration.payload_schema_json)
            Draft202012Validator.check_schema(schema)
            self._validators[registration.key] = Draft202012Validator(schema)

    def require_current(self, snapshot: ReadSnapshot, session: SessionContext) -> None:
        if not self.authorizer.alive(snapshot, session):
            raise failure(ErrorCode.UNAUTHENTICATED)

    @staticmethod
    def _scope_admits(session: SessionContext, value: StoredEvent) -> bool:
        if value.scope.kind == "person" and (
            session.kind != "crew" or session.person_id is None or session.person_id != value.scope.person_id
        ):
            return False
        if value.scope.kind == "ground" and session.kind != "ground":
            return False
        if value.scope.kind == "operator" and session.kind not in ("operator", "editor"):
            return False
        if (
            value.type == EventType.SKILL_PRACTICED
            and value.scope.kind == "crew"
            and Role.CREW_MEDICAL_OFFICER not in session.roles
        ):
            return False
        return True

    def _visible(self, snapshot: ReadSnapshot, session: SessionContext, value: StoredEvent) -> bool:
        registration = self._registrations.get(value.audience.projection)
        rule = self.audiences.get(value.audience.rule)
        if registration is None or rule is None:
            return False
        if not self._scope_admits(session, value):
            return False
        data = self.repository.wire(value).data
        validator = self._validators[registration.key]
        if not validator.is_valid(data) or not all(
            _has_pointer(data, pointer) for pointer in registration.required_field_pointers
        ):
            return False
        return rule.admits(snapshot, session, value.audience) and registration.rule.admits(
            snapshot, session, value.audience
        )

    def current_event(self, session: SessionContext, sequence: int) -> Event | None:
        with self.snapshots.snapshot() as snapshot:
            self.require_current(snapshot, session)
            value = self.repository.get(snapshot.connection, sequence)
            if value is None or not self._visible(snapshot, session, value):
                return None
            return self.repository.wire(value)

    def page(
        self, session: SessionContext, *, after: str | None = None, types: frozenset[EventType] | None = None
    ) -> EventPage:
        with self.snapshots.snapshot() as snapshot:
            self.require_current(snapshot, session)
            position = cursor(after, retained=self.repository.watermark(snapshot.connection))
            scanned = self.repository.scan(
                snapshot.connection, after=position, limit=self.settings.integer("events.page_size")
            )
            high = scanned[-1].seq if scanned else position
            events = [
                self.repository.wire(value)
                for value in scanned
                if (types is None or value.type in types) and self._visible(snapshot, session, value)
            ]
            return EventPage(
                events=events,
                next_cursor=str(high),
                has_more=self.repository.head(snapshot.connection) > high,
            )
