"""SQLite storage of the participation module (E22 5.1). Every statement touches a `participation_` table only.

Rows hold ids, instants, day numbers, flags and enumerated words: no value, answer, quality or free text."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.participation.application.store import IntakeResult, IntentRow, ProjectionState
from crew_health.participation.domain.crew_goal import CrewConsent
from crew_health.participation.domain.derivation import AwardEvent
from crew_health.participation.domain.types import (
    CompletionFact,
    CompletionRecord,
    ViewOrigin,
)


def instant(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_instant(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ValueError("participation row: expected text")
    return value


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise ValueError("participation row: expected integer")
    return value


class SqliteParticipationStore:
    """Stateless: the transaction or snapshot connection is passed to every call."""

    @staticmethod
    def enqueue(
        conn: StorageSession,
        *,
        key: str,
        payload: str,
        payload_sha256: str,
        origin_mode: str,
        patient_id: str,
        at: datetime,
    ) -> IntakeResult:
        stamp = instant(at)
        done = conn.execute(
            "INSERT INTO participation_intent (intent_key, kind, payload, payload_sha256, origin_mode,"
            " patient_id, enqueued_at) VALUES (?, 'completion', ?, ?, ?, ?, ?)"
            " ON CONFLICT (intent_key) DO NOTHING",
            (key, payload, payload_sha256, origin_mode, patient_id, stamp),
        )
        if done.affected_rows == 1:
            conn.execute(
                "INSERT INTO participation_projection (intent_seq, state, attempts, last_error_code, updated_at)"
                " SELECT intent_seq, 'pending', 0, NULL, ? FROM participation_intent WHERE intent_key = ?",
                (stamp, key),
            )
            return "queued"
        stored = conn.rows(
            "SELECT payload_sha256 FROM participation_intent WHERE intent_key = ?", (key,), limit=1
        )
        if stored and _text(stored[0][0]) == payload_sha256:
            return "duplicate"
        conn.execute(
            "INSERT INTO participation_intent_conflict (intent_key, payload_sha256, seen_at) VALUES (?, ?, ?)",
            (key, payload_sha256, stamp),
        )
        return "conflict"

    @staticmethod
    def pending(conn: StorageSession, limit: int) -> tuple[IntentRow, ...]:
        rows = conn.rows(
            "SELECT i.intent_seq, i.intent_key, i.payload, p.attempts FROM participation_projection p"
            " JOIN participation_intent i ON i.intent_seq = p.intent_seq"
            " WHERE p.state = 'pending' ORDER BY i.intent_seq LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(IntentRow(_int(r[0]), _text(r[1]), _text(r[2]), _int(r[3])) for r in rows)

    @staticmethod
    def intent_state(conn: StorageSession, key: str) -> ProjectionState | None:
        rows = conn.rows(
            "SELECT p.state FROM participation_projection p JOIN participation_intent i"
            " ON i.intent_seq = p.intent_seq WHERE i.intent_key = ?",
            (key,),
            limit=1,
        )
        if not rows:
            return None
        state = _text(rows[0][0])
        if state not in ("pending", "applied", "nothing", "parked"):
            raise ValueError("participation row: unknown projection state")
        return state  # type: ignore[return-value]

    @staticmethod
    def count_state(conn: StorageSession, state: ProjectionState, patient_id: str | None) -> int:
        if patient_id is None:
            rows = conn.rows(
                "SELECT COUNT(*) FROM participation_projection WHERE state = ?", (state,), limit=1
            )
        else:
            rows = conn.rows(
                "SELECT COUNT(*) FROM participation_projection p JOIN participation_intent i"
                " ON i.intent_seq = p.intent_seq WHERE p.state = ? AND i.patient_id = ?",
                (state, patient_id),
                limit=1,
            )
        return _int(rows[0][0])

    @staticmethod
    def mark(
        conn: StorageSession, seq: int, state: ProjectionState, attempts: int, error: str | None, at: datetime
    ) -> None:
        conn.execute(
            "UPDATE participation_projection SET state = ?, attempts = ?, last_error_code = ?, updated_at = ?"
            " WHERE intent_seq = ?",
            (state, attempts, error, instant(at), seq),
        )

    @staticmethod
    def requeue_parked(conn: StorageSession, at: datetime) -> int:
        done = conn.execute(
            "UPDATE participation_projection SET state = 'pending', attempts = 0, last_error_code = NULL,"
            " updated_at = ? WHERE state = 'parked'",
            (instant(at),),
        )
        return done.affected_rows

    @staticmethod
    def insert_completion(conn: StorageSession, record: CompletionRecord, intent_seq: int) -> None:
        basis = record.basis
        if basis.origin_mode == "REPLAY":
            raise ValueError("a REPLAY basis creates no completion")
        conn.execute(
            "INSERT INTO participation_completion (completion_id, intent_seq, idempotency_key, patient_id,"
            " mission_id, origin_mode, basis, basis_ref, actor_person_id, cause_kind, cause_ref, occurred_at,"
            " original_offset, mission_clock_version, mission_day, completion_rule_version, status,"
            " status_reason, lineage_id, generation, replaces_completion_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (completion_id) DO NOTHING",
            (
                record.completion_id,
                intent_seq,
                record.intent_key,
                basis.patient_id,
                basis.mission_id,
                basis.origin_mode,
                basis.basis,
                basis.basis_ref,
                basis.actor_person_id,
                basis.cause_kind,
                basis.cause_ref,
                instant(basis.occurred_at),
                basis.original_offset,
                record.mission_clock_version,
                record.mission_day,
                record.completion_rule_version,
                record.status,
                record.status_reason,
                record.lineage_id,
                record.generation,
                record.replaces_completion_id,
            ),
        )

    @staticmethod
    def facts(
        conn: StorageSession,
        *,
        mission_id: str,
        origin: ViewOrigin,
        patient_id: str,
        limit: int,
    ) -> tuple[CompletionFact, ...]:
        """One accepted completion per mission day: the least (occurred_at, completion_id), which is the one a
        threshold is attributed to (E22 3.2). Ordered by day."""
        rows = conn.rows(
            "SELECT c.completion_id, c.patient_id, c.mission_day, c.occurred_at FROM participation_completion c"
            " WHERE c.mission_id = ? AND c.origin_mode = ? AND c.patient_id = ? AND c.status = 'accepted'"
            " AND c.completion_id = (SELECT d.completion_id FROM participation_completion d"
            " WHERE d.mission_id = c.mission_id AND d.origin_mode = c.origin_mode AND d.patient_id = c.patient_id"
            " AND d.status = 'accepted' AND d.mission_day = c.mission_day"
            " ORDER BY d.occurred_at, d.completion_id LIMIT 1) ORDER BY c.mission_day",
            (mission_id, origin, patient_id),
            limit=limit + 1,
        )
        if len(rows) > limit:
            raise OverflowError("participation: completions of one person exceed the bound")
        return tuple(
            CompletionFact(_text(r[0]), _text(r[1]), _int(r[2]), parse_instant(_text(r[3]))) for r in rows
        )

    @staticmethod
    def checked_days(
        conn: StorageSession,
        *,
        mission_id: str,
        origin: ViewOrigin,
        first_day: int,
        last_day: int,
        limit: int,
    ) -> Mapping[str, frozenset[int]]:
        rows = conn.rows(
            "SELECT DISTINCT patient_id, mission_day FROM participation_completion"
            " WHERE mission_id = ? AND origin_mode = ? AND status = 'accepted'"
            " AND mission_day BETWEEN ? AND ? ORDER BY patient_id, mission_day",
            (mission_id, origin, first_day, last_day),
            limit=limit + 1,
        )
        if len(rows) > limit:
            raise OverflowError("participation: crew goal window exceeds the bound")
        days: dict[str, set[int]] = {}
        for patient, day in rows:
            days.setdefault(_text(patient), set()).add(_int(day))
        return {person: frozenset(values) for person, values in days.items()}

    @staticmethod
    def ledger(
        conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin
    ) -> tuple[AwardEvent, ...]:
        rows = conn.rows(
            "SELECT kind, threshold, patch_design_id, reached_by_completion_id, reached_on_mission_day,"
            " derivation_version FROM participation_award"
            " WHERE patient_id = ? AND mission_id = ? AND origin_mode = ? ORDER BY seq",
            (patient_id, mission_id, origin),
            limit=1000,
        )
        events = []
        for r in rows:
            kind = _text(r[0])
            if kind not in ("earned", "removed"):
                raise ValueError("participation row: unknown award kind")
            events.append(
                AwardEvent(kind, _int(r[1]), _text(r[2]), _text(r[3]), _int(r[4]), _text(r[5]))  # type: ignore[arg-type]
            )
        return tuple(events)

    @staticmethod
    def append_award(
        conn: StorageSession,
        *,
        award_id: str,
        patient_id: str,
        mission_id: str,
        origin: ViewOrigin,
        event: AwardEvent,
        at: datetime,
    ) -> None:
        conn.execute(
            "INSERT INTO participation_award (award_id, kind, patient_id, mission_id, origin_mode, threshold,"
            " patch_design_id, reached_by_completion_id, reached_on_mission_day, derivation_version, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                award_id,
                event.kind,
                patient_id,
                mission_id,
                origin,
                event.threshold,
                event.patch_design_id,
                event.completion_id,
                event.mission_day,
                event.derivation_version,
                instant(at),
            ),
        )

    @staticmethod
    def consent(conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin) -> CrewConsent:
        """The latest revision; a missing row is revision 0 and disabled (D10)."""
        rows = conn.rows(
            "SELECT enabled, effective_from_day, revision FROM participation_goal_consent"
            " WHERE patient_id = ? AND mission_id = ? AND origin_mode = ? ORDER BY revision DESC LIMIT 1",
            (patient_id, mission_id, origin),
            limit=1,
        )
        if not rows:
            return CrewConsent()
        enabled, day, revision = rows[0]
        return CrewConsent(_int(enabled) == 1, None if day is None else _int(day), _int(revision))

    @staticmethod
    def consents(
        conn: StorageSession, mission_id: str, origin: ViewOrigin, limit: int
    ) -> Mapping[str, CrewConsent]:
        rows = conn.rows(
            "SELECT c.patient_id, c.enabled, c.effective_from_day, c.revision FROM participation_goal_consent c"
            " WHERE c.mission_id = ? AND c.origin_mode = ? AND c.revision = (SELECT MAX(d.revision)"
            " FROM participation_goal_consent d WHERE d.patient_id = c.patient_id AND d.mission_id = c.mission_id"
            " AND d.origin_mode = c.origin_mode) ORDER BY c.patient_id",
            (mission_id, origin),
            limit=limit + 1,
        )
        if len(rows) > limit:
            raise OverflowError("participation: consent rows exceed the bound")
        return {
            _text(r[0]): CrewConsent(_int(r[1]) == 1, None if r[2] is None else _int(r[2]), _int(r[3]))
            for r in rows
        }

    @staticmethod
    def append_consent(
        conn: StorageSession,
        *,
        patient_id: str,
        mission_id: str,
        origin: ViewOrigin,
        consent: CrewConsent,
        at: datetime,
        policy_version: str,
    ) -> None:
        conn.execute(
            "INSERT INTO participation_goal_consent (patient_id, mission_id, origin_mode, revision, enabled,"
            " effective_from_day, effective_at, policy_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                patient_id,
                mission_id,
                origin,
                consent.revision,
                1 if consent.enabled else 0,
                consent.effective_from_day,
                instant(at),
                policy_version,
            ),
        )

    @staticmethod
    def consent_effective_at(
        conn: StorageSession, patient_id: str, mission_id: str, origin: ViewOrigin
    ) -> str | None:
        rows = conn.rows(
            "SELECT effective_at FROM participation_goal_consent WHERE patient_id = ? AND mission_id = ?"
            " AND origin_mode = ? ORDER BY revision DESC LIMIT 1",
            (patient_id, mission_id, origin),
            limit=1,
        )
        return _text(rows[0][0]) if rows else None
