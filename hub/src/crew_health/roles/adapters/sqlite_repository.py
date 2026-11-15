"""SQLite repository of the roles module over the kernel StorageSession (E31 design 5.1)."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from importlib.resources import files
from typing import cast

from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.roles.application.repository import ContextRow, RecomputeRow, UnfilledRow
from crew_health.roles.domain.drill import Drill, DrillState, Participation
from crew_health.roles.domain.engine import ContextKind
from crew_health.roles.domain.freshness import Practice, PracticeOrigin, PracticeSource
from crew_health.roles.domain.lifecycle import Assignment, AssignmentState, ChangedBy, ContextReason

MAX_ROWS = 512  # bounded by the roster size times the roles of one context; asserted by the callers' limits.


def migration_sql() -> str:
    return (
        files("crew_health.roles.adapters").joinpath("migrations/0001_roles.sql").read_text(encoding="utf-8")
    )


def ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def opt_ts(value: datetime | None) -> str | None:
    return None if value is None else ts(value)


def parse_ts(value: SqlValue) -> datetime:
    if not isinstance(value, str):
        raise ValueError("stored instant is not text")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def opt_parse(value: SqlValue) -> datetime | None:
    return None if value is None else parse_ts(value)


def _s(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ValueError("stored value is not text")
    return value


def _os(value: SqlValue) -> str | None:
    return None if value is None else _s(value)


def _i(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise ValueError("stored value is not an integer")
    return value


_ASSIGNMENT_COLUMNS = (
    "role_assignment_id, revision, help_session_id, plan_run_id, role_id, label, person_id, person_display_name, "
    "skill_id, task_text, reason_code, reason_text, context_reasons, state, proposed_at, accepted_at, "
    "changed_by_kind, changed_by_id, changed_by_name, changed_at, replaced_by_id, retired_at, retired_reason, "
    "acting_since, acting_ended_at, config_release_id"
)
_CURRENT = "replaced_by_id IS NULL AND retired_at IS NULL"


def _assignment(row: tuple[SqlValue, ...]) -> Assignment:
    reasons_raw = json.loads(_s(row[12]))
    reasons = tuple(ContextReason(str(r["reason_code"]), str(r["text"])) for r in reasons_raw)
    changed = None if row[16] is None else ChangedBy(_s(row[16]), _s(row[17]), _os(row[18]))
    help_id, plan_id = _os(row[2]), _os(row[3])
    kind: ContextKind = "help_session" if help_id is not None else "plan_run"
    return Assignment(
        role_assignment_id=_s(row[0]),
        revision=_i(row[1]),
        context_kind=kind,
        context_id=help_id if help_id is not None else _s(plan_id),
        role_id=_s(row[4]),
        label=_s(row[5]),
        person_id=_s(row[6]),
        person_display_name=_s(row[7]),
        skill_id=_os(row[8]),
        task_text=_os(row[9]),
        reason_code=_s(row[10]),
        reason_text=_s(row[11]),
        context_reasons=reasons,
        state=cast(AssignmentState, _s(row[13])),
        proposed_at=parse_ts(row[14]),
        accepted_at=opt_parse(row[15]),
        changed_by=changed,
        changed_at=opt_parse(row[19]),
        replaced_by_id=_os(row[20]),
        retired_at=opt_parse(row[21]),
        retired_reason=_os(row[22]),
        acting_since=opt_parse(row[23]),
        acting_ended_at=opt_parse(row[24]),
        config_release_id=_s(row[25]),
    )


_CONTEXT_COLUMNS = "context_kind, context_id, proposal_seq, subject_person_id, open, as_of, degraded_at, revision, next_check_at"


def _context(r: tuple[SqlValue, ...]) -> ContextRow:
    return ContextRow(
        cast(ContextKind, _s(r[0])),
        _s(r[1]),
        _i(r[2]),
        _os(r[3]),
        _i(r[4]) == 1,
        opt_parse(r[5]),
        opt_parse(r[6]),
        None if r[7] is None else _i(r[7]),
        opt_parse(r[8]),
    )


def _context_column(kind: ContextKind) -> str:
    return "help_session_id" if kind == "help_session" else "plan_run_id"


_PRACTICE_COLUMNS = (
    "practice_id, person_id, skill_id, source, source_ref_id, practiced_at, origin_mode, mission_id"
)


def _practice(row: tuple[SqlValue, ...]) -> Practice:
    return Practice(
        practice_id=_s(row[0]),
        person_id=_s(row[1]),
        skill_id=_s(row[2]),
        source=cast(PracticeSource, _s(row[3])),
        source_ref_id=_s(row[4]),
        practiced_at=parse_ts(row[5]),
        origin_mode=cast(PracticeOrigin, _s(row[6])),
        mission_id=_s(row[7]),
    )


class SqliteRolesRepository:
    def __init__(self, connection: StorageSession) -> None:
        self.db = connection

    # ------------------------------------------------------------------------------------------------ assignments
    def current(self, kind: ContextKind, context_id: str) -> list[Assignment]:
        rows = self.db.rows(
            f"SELECT {_ASSIGNMENT_COLUMNS} FROM roles_assignment WHERE {_context_column(kind)} = ? AND {_CURRENT} "
            "ORDER BY role_assignment_id",
            (context_id,),
            limit=MAX_ROWS,
        )
        return [_assignment(r) for r in rows]

    def get(self, role_assignment_id: str) -> Assignment | None:
        rows = self.db.rows(
            f"SELECT {_ASSIGNMENT_COLUMNS} FROM roles_assignment WHERE role_assignment_id = ?",
            (role_assignment_id,),
            limit=1,
        )
        return _assignment(rows[0]) if rows else None

    def insert(self, assignment: Assignment, proposal_seq: int) -> None:
        a = assignment
        changed = a.changed_by
        self.db.execute(
            f"INSERT INTO roles_assignment ({_ASSIGNMENT_COLUMNS}, proposal_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                a.role_assignment_id,
                a.revision,
                a.context_id if a.context_kind == "help_session" else None,
                a.context_id if a.context_kind == "plan_run" else None,
                a.role_id,
                a.label,
                a.person_id,
                a.person_display_name,
                a.skill_id,
                a.task_text,
                a.reason_code,
                a.reason_text,
                json.dumps([{"reason_code": r.reason_code, "text": r.text} for r in a.context_reasons]),
                a.state,
                ts(a.proposed_at),
                opt_ts(a.accepted_at),
                None if changed is None else changed.kind,
                None if changed is None else changed.id,
                None if changed is None else changed.display_name,
                opt_ts(a.changed_at),
                a.replaced_by_id,
                opt_ts(a.retired_at),
                a.retired_reason,
                opt_ts(a.acting_since),
                opt_ts(a.acting_ended_at),
                a.config_release_id,
                proposal_seq,
            ),
        )

    def save(self, assignment: Assignment, expected_revision: int) -> bool:
        a = assignment
        changed = a.changed_by
        result = self.db.execute(
            "UPDATE roles_assignment SET revision = ?, person_id = ?, person_display_name = ?, reason_code = ?, "
            "reason_text = ?, context_reasons = ?, state = ?, accepted_at = ?, changed_by_kind = ?, changed_by_id = ?, "
            "changed_by_name = ?, changed_at = ?, acting_since = ?, acting_ended_at = ? "
            f"WHERE role_assignment_id = ? AND revision = ? AND {_CURRENT}",
            (
                a.revision,
                a.person_id,
                a.person_display_name,
                a.reason_code,
                a.reason_text,
                json.dumps([{"reason_code": r.reason_code, "text": r.text} for r in a.context_reasons]),
                a.state,
                opt_ts(a.accepted_at),
                None if changed is None else changed.kind,
                None if changed is None else changed.id,
                None if changed is None else changed.display_name,
                opt_ts(a.changed_at),
                opt_ts(a.acting_since),
                opt_ts(a.acting_ended_at),
                a.role_assignment_id,
                expected_revision,
            ),
        )
        return result.affected_rows == 1

    def retire(
        self,
        role_assignment_id: str,
        *,
        at: datetime,
        reason: str,
        replaced_by_id: str | None,
        expected_revision: int,
    ) -> bool:
        ended = "acting_ended_at = CASE WHEN acting_since IS NOT NULL AND acting_ended_at IS NULL THEN ? ELSE acting_ended_at END"
        if replaced_by_id is not None:
            result = self.db.execute(
                f"UPDATE roles_assignment SET replaced_by_id = ?, revision = revision + 1, {ended} "
                f"WHERE role_assignment_id = ? AND revision = ? AND {_CURRENT}",
                (replaced_by_id, ts(at), role_assignment_id, expected_revision),
            )
        else:
            result = self.db.execute(
                f"UPDATE roles_assignment SET retired_at = ?, retired_reason = ?, revision = revision + 1, {ended} "
                f"WHERE role_assignment_id = ? AND revision = ? AND {_CURRENT}",
                (ts(at), reason, ts(at), role_assignment_id, expected_revision),
            )
        return result.affected_rows == 1

    def current_for_person(self, person_id: str, limit: int) -> list[Assignment]:
        rows = self.db.rows(
            f"SELECT {_ASSIGNMENT_COLUMNS} FROM roles_assignment WHERE person_id = ? AND {_CURRENT} "
            "ORDER BY role_assignment_id LIMIT ?",
            (person_id, limit),
            limit=limit,
        )
        return [_assignment(r) for r in rows]

    def current_medical_officer(self, person_id: str, limit: int) -> list[Assignment]:
        rows = self.db.rows(
            f"SELECT {_ASSIGNMENT_COLUMNS} FROM roles_assignment WHERE person_id = ? AND role_id = 'medical_officer' "
            f"AND {_CURRENT} ORDER BY role_assignment_id LIMIT ?",
            (person_id, limit),
            limit=limit,
        )
        return [_assignment(r) for r in rows]

    def current_of_contexts(
        self, kind: ContextKind, context_ids: Collection[str], limit: int
    ) -> list[Assignment]:
        ids = sorted(context_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        rows = self.db.rows(
            f"SELECT {_ASSIGNMENT_COLUMNS} FROM roles_assignment WHERE {_context_column(kind)} IN ({marks}) "
            f"AND {_CURRENT} ORDER BY role_assignment_id LIMIT ?",
            (*ids, limit),
            limit=limit,
        )
        return [_assignment(r) for r in rows]

    # ---------------------------------------------------------------------------------------------------- context
    def context(self, kind: ContextKind, context_id: str) -> ContextRow | None:
        rows = self.db.rows(
            f"SELECT {_CONTEXT_COLUMNS} FROM roles_context WHERE context_kind = ? AND context_id = ?",
            (kind, context_id),
            limit=1,
        )
        return _context(rows[0]) if rows else None

    def open_contexts(self, limit: int) -> list[ContextRow]:
        rows = self.db.rows(
            f"SELECT {_CONTEXT_COLUMNS} FROM roles_context WHERE open = 1 ORDER BY context_kind, context_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return [_context(r) for r in rows]

    def put_context(self, row: ContextRow) -> None:
        self.db.execute(
            f"INSERT INTO roles_context ({_CONTEXT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (context_kind, context_id) DO UPDATE SET proposal_seq = excluded.proposal_seq, "
            "subject_person_id = excluded.subject_person_id, open = excluded.open, as_of = excluded.as_of, "
            "degraded_at = excluded.degraded_at, revision = excluded.revision, next_check_at = excluded.next_check_at",
            (
                row.kind,
                row.context_id,
                row.proposal_seq,
                row.subject_person_id,
                1 if row.open else 0,
                opt_ts(row.as_of),
                opt_ts(row.degraded_at),
                row.revision,
                opt_ts(row.next_check_at),
            ),
        )

    def contexts_to_recheck(self, at: datetime, limit: int) -> list[ContextRow]:
        rows = self.db.rows(
            f"SELECT {_CONTEXT_COLUMNS} FROM roles_context WHERE open = 1 AND next_check_at IS NOT NULL "
            "AND next_check_at <= ? ORDER BY next_check_at, context_kind, context_id LIMIT ?",
            (ts(at), limit),
            limit=limit,
        )
        return [_context(r) for r in rows]

    def recompute_version(self, kind: ContextKind, context_id: str) -> int | None:
        rows = self.db.rows(
            "SELECT version FROM roles_recompute WHERE context_kind = ? AND context_id = ?",
            (kind, context_id),
            limit=1,
        )
        return _i(rows[0][0]) if rows else None

    def unfilled(self, kind: ContextKind, context_id: str) -> list[UnfilledRow]:
        rows = self.db.rows(
            "SELECT role_id, label, required FROM roles_unfilled WHERE context_kind = ? AND context_id = ? "
            "ORDER BY position",
            (kind, context_id),
            limit=MAX_ROWS,
        )
        return [UnfilledRow(_s(r[0]), _s(r[1]), _i(r[2]) == 1) for r in rows]

    def set_unfilled(self, kind: ContextKind, context_id: str, rows: Sequence[UnfilledRow]) -> None:
        self.db.execute(
            "DELETE FROM roles_unfilled WHERE context_kind = ? AND context_id = ?", (kind, context_id)
        )
        for position, row in enumerate(rows):
            self.db.execute(
                "INSERT INTO roles_unfilled (context_kind, context_id, position, role_id, label, required) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, context_id, position, row.role_id, row.label, 1 if row.required else 0),
            )

    # -------------------------------------------------------------------------------------------------- recompute
    def request_recompute(self, kind: ContextKind, context_id: str, *, reason: str, at: datetime) -> None:
        """Every request bumps `version`; `first_requested_at` stays at the first unmet request (design 5.2)."""
        self.db.execute(
            "INSERT INTO roles_recompute (context_kind, context_id, requested_at, first_requested_at, version, reason, "
            "attempts, next_attempt_at) VALUES (?, ?, ?, ?, 1, ?, 0, ?) ON CONFLICT (context_kind, context_id) DO UPDATE "
            "SET requested_at = excluded.requested_at, version = roles_recompute.version + 1, reason = excluded.reason, "
            "next_attempt_at = excluded.next_attempt_at",
            (kind, context_id, ts(at), ts(at), reason, ts(at)),
        )

    def due_recomputes(self, at: datetime, limit: int) -> list[RecomputeRow]:
        rows = self.db.rows(
            "SELECT context_kind, context_id, requested_at, attempts, version, first_requested_at FROM roles_recompute "
            "WHERE next_attempt_at <= ? ORDER BY next_attempt_at, context_kind, context_id LIMIT ?",
            (ts(at), limit),
            limit=limit,
        )
        return [
            RecomputeRow(
                cast(ContextKind, _s(r[0])), _s(r[1]), parse_ts(r[2]), _i(r[3]), _i(r[4]), parse_ts(r[5])
            )
            for r in rows
        ]

    def finish_recompute(self, row: RecomputeRow) -> None:
        """Deletes only the request version the pass read; a newer request stays for the next pass."""
        self.db.execute(
            "DELETE FROM roles_recompute WHERE context_kind = ? AND context_id = ? AND version = ?",
            (row.kind, row.context_id, row.version),
        )

    def fail_recompute(self, row: RecomputeRow, *, next_attempt_at: datetime, error: str) -> None:
        self.db.execute(
            "UPDATE roles_recompute SET attempts = attempts + 1, next_attempt_at = ?, last_error = ? "
            "WHERE context_kind = ? AND context_id = ?",
            (ts(next_attempt_at), error, row.kind, row.context_id),
        )

    # -------------------------------------------------------------------------------------------------- practices
    def add_practices(
        self, practices: Sequence[Practice], *, recorded_by: str, release_id: str, at: datetime
    ) -> int:
        added = 0
        for p in practices:
            result = self.db.execute(
                f"INSERT INTO roles_skill_record ({_PRACTICE_COLUMNS}, recorded_at, recorded_by, config_release_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source, source_ref_id, person_id, skill_id) DO NOTHING",
                (
                    p.practice_id,
                    p.person_id,
                    p.skill_id,
                    p.source,
                    p.source_ref_id,
                    ts(p.practiced_at),
                    p.origin_mode,
                    p.mission_id,
                    ts(at),
                    recorded_by,
                    release_id,
                ),
            )
            added += result.affected_rows
        return added

    def latest_practice(
        self,
        person_id: str,
        skill_id: str,
        mission_id: str,
        origin: str,
        at: datetime,
        source: str | None = None,
    ) -> Practice | None:
        clause = "" if source is None else " AND source = ?"
        params: tuple[SqlValue, ...] = (person_id, skill_id, mission_id, origin, ts(at))
        rows = self.db.rows(
            f"SELECT {_PRACTICE_COLUMNS} FROM roles_skill_record WHERE person_id = ? AND skill_id = ? AND mission_id = ? "
            f"AND origin_mode = ? AND practiced_at <= ?{clause} ORDER BY practiced_at DESC, "
            "CASE source WHEN 'drill' THEN 2 WHEN 'event' THEN 1 ELSE 0 END DESC, source_ref_id DESC LIMIT 1",
            params if source is None else (*params, source),
            limit=1,
        )
        return _practice(rows[0]) if rows else None

    def drill_practices(
        self, person_id: str, mission_id: str, origin: str, at: datetime, limit: int
    ) -> list[Practice]:
        rows = self.db.rows(
            f"SELECT {_PRACTICE_COLUMNS} FROM roles_skill_record WHERE person_id = ? AND mission_id = ? AND origin_mode = ? "
            "AND source = 'drill' AND practiced_at <= ? ORDER BY practiced_at DESC LIMIT ?",
            (person_id, mission_id, origin, ts(at), limit),
            limit=limit,
        )
        return [_practice(r) for r in rows]

    # ----------------------------------------------------------------------------------------------------- drills
    def get_drill(self, drill_id: str) -> Drill | None:
        rows = self.db.rows(
            "SELECT drill_id, revision, scenario_id, skill_ids, routine_occurrence_id, state, started_at, completed_at, "
            "abandoned_at, day_end, origin_mode, mission_id, started_by, config_release_id FROM roles_drill "
            "WHERE drill_id = ?",
            (drill_id,),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        people = self.db.rows(
            "SELECT person_id, completed FROM roles_drill_participant WHERE drill_id = ? ORDER BY position",
            (drill_id,),
            limit=MAX_ROWS,
        )
        origin = _s(r[10])
        return Drill(
            drill_id=_s(r[0]),
            revision=_i(r[1]),
            scenario_id=_s(r[2]),
            skill_ids=tuple(str(x) for x in json.loads(_s(r[3]))),
            participant_ids=tuple(_s(p[0]) for p in people),
            completed_ids=tuple(_s(p[0]) for p in people if _i(p[1]) == 1),
            routine_occurrence_id=_os(r[4]),
            state=cast(DrillState, _s(r[5])),
            started_at=parse_ts(r[6]),
            completed_at=opt_parse(r[7]),
            abandoned_at=opt_parse(r[8]),
            origin_mode="LIVE" if origin == "LIVE" else "SIM",
            mission_id=_s(r[11]),
            started_by=_s(r[12]),
            day_end=parse_ts(r[9]),
            config_release_id=_s(r[13]),
        )

    def insert_drill(self, drill: Drill, *, environment_id: str, roster_index: dict[str, int]) -> None:
        d = drill
        self.db.execute(
            "INSERT INTO roles_drill (drill_id, revision, scenario_id, skill_ids, routine_occurrence_id, state, "
            "started_at, completed_at, abandoned_at, day_end, origin_mode, mission_id, started_by, completed_by, "
            "environment_id, config_release_id) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?)",
            (
                d.drill_id,
                d.revision,
                d.scenario_id,
                json.dumps(list(d.skill_ids)),
                d.routine_occurrence_id,
                d.state,
                ts(d.started_at),
                ts(d.day_end),
                d.origin_mode,
                d.mission_id,
                d.started_by,
                environment_id,
                d.config_release_id,
            ),
        )
        for position, person_id in enumerate(d.participant_ids):
            self.db.execute(
                "INSERT INTO roles_drill_participant (drill_id, person_id, position, completed, "
                "core_fresh_at_completion, completed_at, mission_id, origin_mode) VALUES (?, ?, ?, 0, 0, NULL, ?, ?)",
                (d.drill_id, person_id, roster_index.get(person_id, position), d.mission_id, d.origin_mode),
            )

    def save_drill(
        self, drill: Drill, expected_revision: int, *, completed_by: str | None, core_fresh: dict[str, bool]
    ) -> bool:
        d = drill
        result = self.db.execute(
            "UPDATE roles_drill SET revision = ?, state = ?, completed_at = ?, abandoned_at = ?, completed_by = ? "
            "WHERE drill_id = ? AND revision = ?",
            (
                d.revision,
                d.state,
                opt_ts(d.completed_at),
                opt_ts(d.abandoned_at),
                completed_by,
                d.drill_id,
                expected_revision,
            ),
        )
        if result.affected_rows != 1:
            return False
        for person_id in d.completed_ids:
            self.db.execute(
                "UPDATE roles_drill_participant SET completed = 1, core_fresh_at_completion = ?, completed_at = ? "
                "WHERE drill_id = ? AND person_id = ?",
                (1 if core_fresh.get(person_id, False) else 0, opt_ts(d.completed_at), d.drill_id, person_id),
            )
        return True

    def running_drills(self, limit: int) -> list[Drill]:
        rows = self.db.rows(
            "SELECT drill_id FROM roles_drill WHERE state = 'running' ORDER BY started_at, drill_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        found = [self.get_drill(_s(r[0])) for r in rows]
        return [d for d in found if d is not None]

    def participations(self, person_id: str, mission_id: str, origin: str, limit: int) -> list[Participation]:
        rows = self.db.rows(
            "SELECT drill_id, completed_at, core_fresh_at_completion FROM roles_drill_participant WHERE person_id = ? "
            "AND mission_id = ? AND origin_mode = ? AND completed = 1 ORDER BY completed_at, drill_id LIMIT ?",
            (person_id, mission_id, origin, limit),
            limit=limit,
        )
        return [Participation(_s(r[0]), person_id, parse_ts(r[1]), _i(r[2]) == 1) for r in rows]
