"""SQLite implementation of the mission store (E23 5.1) over the unit of work's StorageSession.

Every record is stored whole as JSON beside its indexed columns. A revision check on update is the second line
behind the unit of work's single writer: a stale writer changes no row and fails with STALE_VERSION.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime

from pydantic import TypeAdapter

from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.mission.application.ports import OriginMode
from crew_health.mission.application.queries import MissionIdentity
from crew_health.mission.domain.errors import IntegrityViolation, StaleVersion
from crew_health.mission.domain.events import EventRecord
from crew_health.mission.domain.overlay import OverlayRecord
from crew_health.mission.domain.profile import ProfileRecord
from crew_health.mission.domain.state_facts import FactState

type View = UnitOfWork | ReadSnapshot

_PROFILE = TypeAdapter(ProfileRecord)
_OVERLAY = TypeAdapter(OverlayRecord)
_EVENT = TypeAdapter(EventRecord)
_FACT = TypeAdapter(FactState)
_MODES: dict[str, OriginMode] = {"LIVE": "LIVE", "REPLAY": "REPLAY", "SIM": "SIM"}
_MAX_RECORDS = 100000  # row ceiling of one read; the StorageSession also bounds it by storage.max_rows


def stamp(value: datetime) -> str:
    """Fixed width UTC text so that string order is time order."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _opt(value: datetime | None) -> str | None:
    return None if value is None else stamp(value)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise IntegrityViolation("stored value is not text")
    return value


def _fact_key(fact: str, environment_id: str) -> str:
    return fact + ":" + environment_id


class SqliteMissionStore:
    """MissionStore of use_cases.py and MissionReader of queries.py."""

    def __init__(self, *, row_limit: int) -> None:
        if not 1 <= row_limit <= _MAX_RECORDS:
            raise ValueError("mission row limit outside its bound")
        self._limit = row_limit

    # --- identity -----------------------------------------------------------------------------------------

    def identity(self, view: View) -> MissionIdentity | None:
        rows = view.connection.rows(
            "SELECT mission_id, mission_mode, planned_length_days, site_node_id, created_at "
            "FROM mission_identity WHERE singleton = 1",
            (),
            limit=1,
        )
        if not rows:
            return None
        mission_id, mode, days, site, created = rows[0]
        mission_mode = _MODES.get(_text(mode))
        if mission_mode is None:
            raise IntegrityViolation("stored mission identity")
        if days is not None and not isinstance(days, int):
            raise IntegrityViolation("stored mission identity")
        return MissionIdentity(
            _text(mission_id),
            mission_mode,
            days,
            _text(site),
            datetime.fromisoformat(_text(created)),
        )

    def create_identity(self, uow: UnitOfWork, identity: MissionIdentity) -> None:
        uow.connection.execute(
            "INSERT INTO mission_identity VALUES (1, ?, ?, ?, ?, ?)",
            (
                identity.mission_id,
                identity.mission_mode,
                identity.planned_length_days,
                identity.site_node_id,
                stamp(identity.created_at),
            ),
        )

    # --- profiles -----------------------------------------------------------------------------------------

    def profile(self, view: View, profile_id: str) -> ProfileRecord | None:
        rows = view.connection.rows(
            "SELECT record_json FROM mission_environment WHERE profile_id = ?", (profile_id,), limit=1
        )
        return _PROFILE.validate_json(_text(rows[0][0])) if rows else None

    def profiles(self, view: View) -> Sequence[ProfileRecord]:
        rows = view.connection.rows(
            "SELECT record_json FROM mission_environment ORDER BY proposed_at, profile_id",
            (),
            limit=self._limit,
        )
        return tuple(_PROFILE.validate_json(_text(row[0])) for row in rows)

    def save_profile(self, uow: UnitOfWork, record: ProfileRecord) -> None:
        body = _PROFILE.dump_json(record).decode()
        columns: tuple[SqlValue, ...] = (
            record.revision,
            record.environment_id,
            record.state,
            record.reason,
            _opt(record.effective_at),
            stamp(record.proposed_at),
            _opt(record.activated_at),
            _opt(record.superseded_at),
            body,
        )
        if record.revision == 1:
            uow.connection.execute(
                "INSERT INTO mission_environment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.profile_id, *columns),
            )
            return
        done = uow.connection.execute(
            "UPDATE mission_environment SET revision = ?, environment_id = ?, state = ?, reason = ?, "
            "effective_at = ?, proposed_at = ?, activated_at = ?, superseded_at = ?, record_json = ? "
            "WHERE profile_id = ? AND revision = ?",
            (*columns, record.profile_id, record.revision - 1),
        )
        if done.affected_rows != 1:
            raise StaleVersion(record.profile_id)

    # --- overlays -----------------------------------------------------------------------------------------

    def overlay(self, view: View, overlay_id: str) -> OverlayRecord | None:
        rows = view.connection.rows(
            "SELECT record_json FROM mission_overlay WHERE overlay_id = ?", (overlay_id,), limit=1
        )
        return _OVERLAY.validate_json(_text(rows[0][0])) if rows else None

    def overlays_of(self, view: View, person_ids: Collection[str]) -> Sequence[OverlayRecord]:
        found: list[OverlayRecord] = []
        for person in sorted(set(person_ids)):
            rows = view.connection.rows(
                "SELECT record_json FROM mission_overlay WHERE person_id = ? ORDER BY started_at, overlay_id",
                (person,),
                limit=self._limit,
            )
            found.extend(_OVERLAY.validate_json(_text(row[0])) for row in rows)
            if len(found) > self._limit:
                raise IntegrityViolation("overlay rows above the bound")
        return tuple(found)

    def save_overlay(self, uow: UnitOfWork, record: OverlayRecord) -> None:
        body = _OVERLAY.dump_json(record).decode()
        columns: tuple[SqlValue, ...] = (
            record.revision,
            record.person_id,
            record.kind,
            record.state,
            _opt(record.started_at),
            body,
        )
        if record.revision == 1:
            uow.connection.execute(
                "INSERT INTO mission_overlay VALUES (?, ?, ?, ?, ?, ?, ?)", (record.overlay_id, *columns)
            )
            return
        done = uow.connection.execute(
            "UPDATE mission_overlay SET revision = ?, person_id = ?, kind = ?, state = ?, started_at = ?, "
            "record_json = ? WHERE overlay_id = ? AND revision = ?",
            (*columns, record.overlay_id, record.revision - 1),
        )
        if done.affected_rows != 1:
            raise StaleVersion(record.overlay_id)

    # --- events -------------------------------------------------------------------------------------------

    def events(
        self, view: View, *, since: datetime, after_event_id: str | None, limit: int
    ) -> Sequence[EventRecord]:
        """Ascending by occurred_at then event_id, from `since`, strictly after the cursor event."""
        floor = (stamp(since), "")
        if after_event_id is not None:
            rows = view.connection.rows(
                "SELECT occurred_at FROM mission_event WHERE event_id = ?", (after_event_id,), limit=1
            )
            if rows:
                floor = max(floor, (_text(rows[0][0]), after_event_id))
        found = view.connection.rows(
            "SELECT record_json FROM mission_event WHERE occurred_at > ? OR (occurred_at = ? AND event_id > ?) "
            "ORDER BY occurred_at, event_id LIMIT ?",
            (floor[0], floor[0], floor[1], limit),
            limit=limit,
        )
        return tuple(_EVENT.validate_json(_text(row[0])) for row in found)

    def events_before(self, view: View, *, before_event_id: str | None, limit: int) -> Sequence[EventRecord]:
        """Newest first, strictly before the cursor event: the list operation's order."""
        key: tuple[str, str] | None = None
        if before_event_id is not None:
            rows = view.connection.rows(
                "SELECT occurred_at FROM mission_event WHERE event_id = ?", (before_event_id,), limit=1
            )
            key = (_text(rows[0][0]), before_event_id) if rows else None
        parameters: tuple[SqlValue, ...] = () if key is None else (key[0], key[0], key[1])
        clause = "" if key is None else "WHERE occurred_at < ? OR (occurred_at = ? AND event_id < ?) "
        found = view.connection.rows(
            "SELECT record_json FROM mission_event "
            + clause
            + "ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
            (*parameters, limit),
            limit=limit,
        )
        return tuple(_EVENT.validate_json(_text(row[0])) for row in found)

    def add_event(self, uow: UnitOfWork, record: EventRecord) -> None:
        uow.connection.execute(
            "INSERT INTO mission_event VALUES (?, ?, ?)",
            (record.event_id, stamp(record.occurred_at), _EVENT.dump_json(record).decode()),
        )

    # --- facts --------------------------------------------------------------------------------------------

    def fact_states(self, view: View) -> Mapping[str, FactState]:
        rows = view.connection.rows(
            "SELECT record_json FROM mission_state_fact ORDER BY fact_key", (), limit=self._limit
        )
        states: dict[str, FactState] = {}
        for row in rows:
            state = _FACT.validate_json(_text(row[0]))
            held = states.get(state.fact)
            if held is None or (state.cause_at, state.cause_event_id) > (held.cause_at, held.cause_event_id):
                states[state.fact] = state  # the same fact in two environments: the newer cause is the state
        return states

    def save_fact(self, uow: UnitOfWork, fact: FactState) -> None:
        key = _fact_key(fact.fact, fact.environment_id)
        body = _FACT.dump_json(fact).decode()
        if uow.connection.rows("SELECT 1 FROM mission_state_fact WHERE fact_key = ?", (key,), limit=1):
            uow.connection.execute(
                "UPDATE mission_state_fact SET record_json = ? WHERE fact_key = ?", (body, key)
            )
        else:  # no UPSERT: E01's replicated-table triggers use INSERT OR IGNORE, which an outer ON CONFLICT overrides
            uow.connection.execute(
                "INSERT INTO mission_state_fact VALUES (?, ?, ?, ?)",
                (key, fact.fact, fact.environment_id, body),
            )


def table_declarations() -> tuple[tuple[str, str, str], ...]:
    """(table, record_type, key column) for E01's replicated-table verification (S5.7)."""
    return (
        ("mission_identity", "mission_identity", "mission_id"),
        ("mission_environment", "mission_environment", "profile_id"),
        ("mission_overlay", "mission_overlay", "overlay_id"),
        ("mission_event", "mission_event", "event_id"),
        ("mission_state_fact", "mission_state_fact", "fact_key"),
    )
