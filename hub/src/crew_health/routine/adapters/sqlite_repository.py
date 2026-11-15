from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.adapters import codec
from crew_health.routine.application.store_port import BarrierAsk, TargetKind, View
from crew_health.routine.domain.activation import CadenceActivation
from crew_health.routine.domain.bindings import OverlayInput
from crew_health.routine.domain.errors import StaleVersion
from crew_health.routine.domain.instances import QuestionInstance
from crew_health.routine.domain.occurrence import Occurrence, TransitionRecord
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.vocabulary import MemberBasis, Overlay


def migration_sql() -> str:
    return Path(__file__).with_name("migrations").joinpath("0001_routine.sql").read_text(encoding="utf-8")


def _columns(table: str) -> tuple[str, ...]:
    if table == "routine_cadence_activation":
        return (
            "activation_id",
            "revision",
            "binding_id",
            "item_id",
            "person_id",
            "cadence_json",
            "started_at",
            "ends_kind",
            "ends_at",
            "state",
            "ended_at",
            "end_reason",
            "source_kind",
            "source_ref",
            "source_at",
            "source_origin_mode",
            "config_release_id",
        )
    if table == "routine_occurrence":
        return (
            "occurrence_id",
            "revision",
            "person_id",
            "item_id",
            "environment_id",
            "mission_day",
            "cause_kind",
            "cause_key",
            "periodic",
            "override_id",
            "binding_id",
            "started_by",
            "insight_id",
            "state",
            "due_from",
            "base_due_until",
            "due_until",
            "due_kind",
            "later_until",
            "completed_at",
            "outcome_json",
            "skip_json",
            "miss_counted",
            "end_reason",
            "guidance_release_id",
            "config_release_id",
            "origin_mode",
            "clock_version",
            "created_at",
        )
    if table == "routine_override":
        return (
            "override_id",
            "revision",
            "item_id",
            "person_id",
            "audience",
            "binding_id",
            "trigger_index",
            "trigger_text",
            "started_by",
            "source_kind",
            "source_ref",
            "new_cadence_json",
            "cadence_text",
            "reason_text",
            "state",
            "started_at",
            "ends_kind",
            "ends_at",
            "ends_text",
            "stop_role",
            "ended_at",
            "end_reason",
            "basis",
            "source_ids_json",
            "config_release_id",
            "member_ids_json",
            "member_basis_json",
            "source_origin_mode",
            "source_at",
            "source_chain_json",
            "effect_json",
            "awaiting_disposition",
            "note",
        )
    if table == "routine_question_instance":
        return (
            "instance_id",
            "override_id",
            "person_id",
            "question_set_id",
            "slot",
            "question_ids_json",
            "due_from",
            "due_until",
            "answers_json",
            "state",
            "revision",
        )
    raise ValueError("unknown routine table")


def _rows(
    view: View,
    table: str,
    where: str,
    params: tuple[SqlValue, ...],
    limit: int,
    *,
    offset: int = 0,
    order: str = "",
) -> tuple[tuple[SqlValue, ...], ...]:
    # Table, columns, predicates and order clauses are compiled adapter literals, never input.
    columns = ",".join(_columns(table))
    return view.connection.rows(
        f"SELECT {columns} FROM {table} WHERE {where}{order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
        limit=limit,
    )


def _insert(uow: UnitOfWork, table: str, values: tuple[SqlValue, ...], extra: tuple[str, ...] = ()) -> bool:
    columns = (*_columns(table), *extra)
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    if uow.connection.execute(sql, values).affected_rows == 1:
        return True
    if not _duplicate(uow, table, values):
        raise StorageUnavailable("corrupt")
    return False


def _duplicate(view: View, table: str, values: tuple[SqlValue, ...]) -> bool:
    params: tuple[SqlValue, ...]
    if table == "routine_occurrence":
        where = "occurrence_id=? OR (person_id=? AND item_id=? AND mission_day=? AND cause_key=?) OR (cause_kind='assistant' AND insight_id=?)"
        params = (values[0], values[2], values[3], values[5], values[7], values[12])
    elif table == "routine_question_instance":
        where = "instance_id=? OR (override_id=? AND person_id=? AND question_set_id=? AND slot=?)"
        params = values[:5]
    elif table == "routine_cadence_activation":
        where, params = "activation_id=?", values[:1]
    else:
        where, params = "override_id=?", values[:1]
    return bool(_rows(view, table, where, params, 1))


def _update(uow: UnitOfWork, table: str, values: tuple[SqlValue, ...], expected: int) -> None:
    columns = _columns(table)
    sql = f"UPDATE {table} SET {','.join(c + '=?' for c in columns[1:])} WHERE {columns[0]}=? AND revision=?"
    if uow.connection.execute(sql, (*values[1:], values[0], expected)).affected_rows != 1:
        rows = _rows(uow, table, columns[0] + "=?", (values[0],), 1)
        revision_index = columns.index("revision")
        raise StaleVersion(codec.integer(rows[0][revision_index]) if rows else expected)


class SqliteRoutineStore:
    def activation_from_source(
        self, view: View, binding_id: str, source_kind: str, source_ref: str, person_id: str
    ) -> CadenceActivation | None:
        rows = _rows(
            view,
            "routine_cadence_activation",
            "binding_id=? AND source_kind=? AND source_ref=? AND person_id=?",
            (binding_id, source_kind, source_ref, person_id),
            1,
        )
        return codec.read_activation(rows[0]) if rows else None

    def get_activation(self, view: View, activation_id: str) -> CadenceActivation | None:
        rows = _rows(view, "routine_cadence_activation", "activation_id=?", (activation_id,), 1)
        return codec.read_activation(rows[0]) if rows else None

    def insert_activation(self, uow: UnitOfWork, activation: CadenceActivation) -> bool:
        return _insert(uow, "routine_cadence_activation", codec.activation_values(activation))

    def update_activation(
        self, uow: UnitOfWork, activation: CadenceActivation, *, expected_revision: int
    ) -> None:
        _update(uow, "routine_cadence_activation", codec.activation_values(activation), expected_revision)

    def active_activations(
        self, view: View, limit: int, *, offset: int = 0, person_id: str | None = None
    ) -> tuple[CadenceActivation, ...]:
        where = "state='active'" + (" AND person_id=?" if person_id is not None else "")
        return tuple(
            codec.read_activation(r)
            for r in _rows(
                view,
                "routine_cadence_activation",
                where,
                () if person_id is None else (person_id,),
                limit,
                offset=offset,
                order=" ORDER BY started_at,activation_id",
            )
        )

    def remember(
        self, uow: UnitOfWork, actor_ref: str, key: str, value: Occurrence | Override, body: dict[str, object]
    ) -> None:
        kind = "occurrence" if isinstance(value, Occurrence) else "override"
        identifier = value.occurrence_id if isinstance(value, Occurrence) else value.override_id
        raw = codec.dump(body).encode("utf-8")
        uow.connection.execute(
            "INSERT OR IGNORE INTO routine_record_version VALUES (?,?,?,?)",
            (kind, identifier, value.revision, raw),
        )
        uow.connection.execute(
            "INSERT INTO routine_command_version VALUES (?,?,?,?,?)",
            (actor_ref, key, kind, identifier, value.revision),
        )

    def remembered(self, view: View, actor_ref: str, key: str) -> dict[str, object] | None:
        rows = view.connection.rows(
            "SELECT v.kind,v.record FROM routine_command_version c JOIN routine_record_version v USING(kind,record_id,revision) WHERE c.actor_ref=? AND c.command_key=?",
            (actor_ref, key),
            limit=1,
        )
        if not rows:
            return None
        _, raw = rows[0]
        if not isinstance(raw, bytes):
            raise StorageUnavailable("corrupt")
        return codec.mapping(json.loads(raw))

    def get(self, view: View, occurrence_id: str) -> Occurrence | None:
        rows = _rows(view, "routine_occurrence", "occurrence_id=?", (occurrence_id,), 1)
        return codec.read_occurrence(rows[0]) if rows else None

    def insert(self, uow: UnitOfWork, occ: Occurrence) -> bool:
        return _insert(uow, "routine_occurrence", codec.occurrence_values(occ))

    def update(self, uow: UnitOfWork, occ: Occurrence, *, expected_revision: int) -> None:
        _update(uow, "routine_occurrence", codec.occurrence_values(occ), expected_revision)

    def append_transition(self, uow: UnitOfWork, row: TransitionRecord) -> None:
        uow.connection.execute(
            "INSERT INTO routine_occurrence_transition VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row.occurrence_id,
                row.revision,
                row.from_state,
                row.to_state,
                codec.instant(row.at),
                row.actor_kind,
                row.actor_id,
                row.reason_code,
                None if row.miss_counted is None else int(row.miss_counted),
            ),
        )

    def open_for_person(
        self, view: View, person_id: str, limit: int, *, offset: int = 0
    ) -> tuple[Occurrence, ...]:
        return tuple(
            codec.read_occurrence(r)
            for r in _rows(
                view,
                "routine_occurrence",
                "person_id=? AND state IN ('not_due','due','later')",
                (person_id,),
                limit,
                offset=offset,
                order=" ORDER BY mission_day,occurrence_id",
            )
        )

    def for_person_days(
        self, view: View, person_id: str, first: int, last: int, limit: int, *, offset: int = 0
    ) -> tuple[Occurrence, ...]:
        return tuple(
            codec.read_occurrence(r)
            for r in _rows(
                view,
                "routine_occurrence",
                "person_id=? AND mission_day BETWEEN ? AND ?",
                (person_id, first, last),
                limit,
                offset=offset,
                order=" ORDER BY mission_day,occurrence_id",
            )
        )

    def latest_periodic_final_day(self, view: View, person_id: str, item_id: str) -> int | None:
        rows = view.connection.rows(
            "SELECT max(mission_day) FROM routine_occurrence WHERE person_id=? AND item_id=? AND periodic=1 AND state IN ('done','skipped','missed')",
            (person_id, item_id),
            limit=1,
        )
        return None if not rows or rows[0][0] is None else codec.integer(rows[0][0])

    def periodic_on_day(self, view: View, person_id: str, item_id: str, day: int) -> Occurrence | None:
        rows = _rows(
            view,
            "routine_occurrence",
            "person_id=? AND item_id=? AND mission_day=? AND periodic=1",
            (person_id, item_id, day),
            1,
            order=" ORDER BY occurrence_id",
        )
        return codec.read_occurrence(rows[0]) if rows else None

    def open_periodic_before(
        self, view: View, person_id: str, item_id: str, day: int, limit: int
    ) -> tuple[Occurrence, ...]:
        return tuple(
            codec.read_occurrence(r)
            for r in _rows(
                view,
                "routine_occurrence",
                "person_id=? AND item_id=? AND mission_day<? AND periodic=1 AND state IN ('not_due','due','later')",
                (person_id, item_id, day),
                limit,
                order=" ORDER BY mission_day,occurrence_id",
            )
        )

    def get_override(self, view: View, override_id: str) -> Override | None:
        rows = _rows(view, "routine_override", "override_id=?", (override_id,), 1)
        return codec.read_override(rows[0]) if rows else None

    def insert_override(self, uow: UnitOfWork, ovr: Override) -> bool:
        if not _insert(uow, "routine_override", codec.override_values(ovr)):
            return False
        for person_id in ovr.member_ids:
            uow.connection.execute(
                "INSERT INTO routine_override_member VALUES (?,?,?)",
                (ovr.override_id, person_id, ovr.member_basis[person_id]),
            )
        return True

    def update_override(self, uow: UnitOfWork, ovr: Override, *, expected_revision: int) -> None:
        _update(uow, "routine_override", codec.override_values(ovr), expected_revision)

    def active_overrides_for(
        self, view: View, person_id: str, limit: int, *, offset: int = 0
    ) -> tuple[Override, ...]:
        return tuple(
            codec.read_override(r)
            for r in _rows(
                view,
                "routine_override",
                "state='active' AND ((audience='person' AND person_id=?) OR (audience<>'person' AND override_id IN (SELECT override_id FROM routine_override_member WHERE person_id=?)))",
                (person_id, person_id),
                limit,
                offset=offset,
                order=" ORDER BY started_at,override_id",
            )
        )

    def active_overrides_from_warning(
        self, view: View, warning_id: str, limit: int, *, offset: int = 0
    ) -> tuple[Override, ...]:
        return tuple(
            codec.read_override(r)
            for r in _rows(
                view,
                "routine_override",
                "state='active' AND source_kind='warning' AND json_extract(source_chain_json,'$[#-1]')=?",
                (warning_id,),
                limit,
                offset=offset,
                order=" ORDER BY started_at,override_id",
            )
        )

    def active_overrides(self, view: View, limit: int, *, offset: int = 0) -> tuple[Override, ...]:
        return tuple(
            codec.read_override(r)
            for r in _rows(
                view,
                "routine_override",
                "state='active'",
                (),
                limit,
                offset=offset,
                order=" ORDER BY started_at,override_id",
            )
        )

    def override_members(
        self, view: View, override_id: str, limit: int
    ) -> tuple[tuple[str, MemberBasis], ...]:
        rows = view.connection.rows(
            "SELECT person_id,member_basis FROM routine_override_member WHERE override_id=? ORDER BY person_id LIMIT ?",
            (override_id, limit),
            limit=limit,
        )
        return tuple((codec.text(r[0]), cast(MemberBasis, codec.text(r[1]))) for r in rows)

    def add_target(self, uow: UnitOfWork, override_id: str, target_kind: TargetKind, target_id: str) -> None:
        uow.connection.execute(
            "INSERT OR IGNORE INTO routine_override_added VALUES (?,?,?)",
            (override_id, target_kind, target_id),
        )

    def targets_of(
        self, view: View, override_id: str, limit: int, *, offset: int = 0
    ) -> tuple[tuple[TargetKind, str], ...]:
        rows = view.connection.rows(
            "SELECT target_kind,target_id FROM routine_override_added WHERE override_id=? ORDER BY target_kind,target_id LIMIT ? OFFSET ?",
            (override_id, limit, offset),
            limit=limit,
        )
        return tuple((cast(TargetKind, codec.text(r[0])), codec.text(r[1])) for r in rows)

    def overrides_targeting(
        self, view: View, target_id: str, limit: int, *, offset: int = 0
    ) -> tuple[Override, ...]:
        return tuple(
            codec.read_override(r)
            for r in _rows(
                view,
                "routine_override",
                "override_id IN (SELECT override_id FROM routine_override_added WHERE target_id=?)",
                (target_id,),
                limit,
                offset=offset,
                order=" ORDER BY override_id",
            )
        )

    def insert_instance(
        self, uow: UnitOfWork, inst: QuestionInstance, *, carrier_day: int, carrier_occurrence_id: str | None
    ) -> bool:
        return _insert(
            uow,
            "routine_question_instance",
            (*codec.instance_values(inst), carrier_day, carrier_occurrence_id),
            ("carrier_day", "carrier_occurrence_id"),
        )

    def get_instance(self, view: View, instance_id: str) -> QuestionInstance | None:
        rows = _rows(view, "routine_question_instance", "instance_id=?", (instance_id,), 1)
        return codec.read_instance(rows[0]) if rows else None

    def update_instance(self, uow: UnitOfWork, inst: QuestionInstance, *, expected_revision: int) -> None:
        _update(uow, "routine_question_instance", codec.instance_values(inst), expected_revision)

    def open_instances_for_person(
        self, view: View, person_id: str, limit: int, *, offset: int = 0
    ) -> tuple[QuestionInstance, ...]:
        return tuple(
            codec.read_instance(r)
            for r in _rows(
                view,
                "routine_question_instance",
                "person_id=? AND state='open'",
                (person_id,),
                limit,
                offset=offset,
                order=" ORDER BY due_from,instance_id",
            )
        )

    def instances_for_occurrence(
        self, view: View, occurrence_id: str, limit: int, *, offset: int = 0
    ) -> tuple[QuestionInstance, ...]:
        return tuple(
            codec.read_instance(r)
            for r in _rows(
                view,
                "routine_question_instance",
                "carrier_occurrence_id=?",
                (occurrence_id,),
                limit,
                offset=offset,
                order=" ORDER BY due_from,instance_id",
            )
        )

    def instance_carrier(self, view: View, instance_id: str) -> tuple[int, str | None] | None:
        rows = view.connection.rows(
            "SELECT carrier_day,carrier_occurrence_id FROM routine_question_instance WHERE instance_id=?",
            (instance_id,),
            limit=1,
        )
        return (codec.integer(rows[0][0]), codec.optional_text(rows[0][1])) if rows else None

    def upsert_overlay(self, uow: UnitOfWork, overlay: OverlayInput) -> bool:
        values = (
            overlay.overlay_id,
            overlay.revision,
            overlay.person_id,
            overlay.kind,
            overlay.state,
            codec.instant(overlay.planned_start),
            codec.instant(overlay.started_at),
            codec.instant(overlay.ended_at),
        )
        return (
            uow.connection.execute(
                "INSERT INTO routine_overlay VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(overlay_id) DO UPDATE SET revision=excluded.revision,person_id=excluded.person_id,kind=excluded.kind,state=excluded.state,planned_start=excluded.planned_start,started_at=excluded.started_at,ended_at=excluded.ended_at WHERE excluded.revision>routine_overlay.revision",
                values,
            ).affected_rows
            == 1
        )

    def overlays_for(
        self,
        view: View,
        person_id: str,
        since: datetime,
        limit: int,
        *,
        offset: int = 0,
        started_only: bool = False,
        kinds: tuple[Overlay, ...] | None = None,
        exclude_overlay_id: str | None = None,
    ) -> tuple[OverlayInput, ...]:
        where = "person_id=? AND (ended_at IS NULL OR julianday(ended_at)>=julianday(?))"
        parameters: tuple[SqlValue, ...] = (person_id, codec.instant(since))
        if started_only:
            where += " AND started_at IS NOT NULL"
        if kinds is not None:
            if not kinds:
                return ()
            where += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters += kinds
        if exclude_overlay_id is not None:
            where += " AND overlay_id<>?"
            parameters += (exclude_overlay_id,)
        rows = view.connection.rows(
            "SELECT overlay_id,revision,person_id,kind,state,planned_start,started_at,ended_at FROM routine_overlay WHERE "
            + where
            + " ORDER BY started_at,overlay_id LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
            limit=limit,
        )
        return tuple(
            OverlayInput(
                codec.text(r[0]),
                codec.integer(r[1]),
                codec.text(r[2]),
                cast(Overlay, codec.text(r[3])),
                cast(Literal["planned", "active", "ended"], codec.text(r[4])),
                codec.optional_time(r[5]),
                codec.optional_time(r[6]),
                codec.optional_time(r[7]),
                None,
            )
            for r in rows
        )

    def deadline_generation(self, view: View, key: str) -> int | None:
        rows = view.connection.rows(
            "SELECT generation FROM routine_deadline WHERE deadline_key=?", (key,), limit=1
        )
        return codec.integer(rows[0][0]) if rows else None

    def set_deadline_generation(self, uow: UnitOfWork, key: str, generation: int) -> None:
        uow.connection.execute(
            "INSERT INTO routine_deadline VALUES (?,?) ON CONFLICT(deadline_key) DO UPDATE SET generation=excluded.generation",
            (key, generation),
        )

    def clock_suspect_since(self, view: View) -> datetime | None:
        rows = view.connection.rows("SELECT suspect_since FROM routine_clock_state WHERE id=1", (), limit=1)
        return codec.optional_time(rows[0][0]) if rows else None

    def set_clock_suspect(self, uow: UnitOfWork, at: datetime | None) -> None:
        uow.connection.execute(
            "INSERT INTO routine_clock_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET suspect_since=excluded.suspect_since",
            (codec.instant(at),),
        )

    def get_barrier_ask(self, view: View, person_id: str, day: int) -> BarrierAsk | None:
        rows = view.connection.rows(
            "SELECT item_ids_json,outcome,answer_id,at FROM routine_barrier_ask WHERE person_id=? AND offered_day=?",
            (person_id, day),
            limit=1,
        )
        return (
            (
                codec.load_strings(rows[0][0]),
                codec.text(rows[0][1]),
                codec.optional_text(rows[0][2]),
                codec.timestamp(rows[0][3]),
            )
            if rows
            else None
        )

    def put_barrier_ask(
        self,
        uow: UnitOfWork,
        person_id: str,
        day: int,
        item_ids: tuple[str, ...],
        outcome: Literal["pending", "answered", "shown_unanswered"],
        answer_id: str | None,
        at: datetime,
    ) -> None:
        uow.connection.execute(
            "INSERT INTO routine_barrier_ask VALUES (?,?,?,?,?,?) ON CONFLICT(person_id,offered_day) DO UPDATE SET item_ids_json=excluded.item_ids_json,outcome=excluded.outcome,answer_id=excluded.answer_id,at=excluded.at WHERE routine_barrier_ask.outcome='pending'",
            (person_id, day, codec.dump(item_ids), outcome, answer_id, codec.instant(at)),
        )

    def latest_barrier_day(self, view: View, person_id: str) -> int | None:
        rows = view.connection.rows(
            "SELECT max(offered_day) FROM routine_barrier_ask WHERE person_id=? AND outcome='answered'",
            (person_id,),
            limit=1,
        )
        return None if not rows or rows[0][0] is None else codec.integer(rows[0][0])

    def misses_since(
        self, view: View, person_id: str, first_day: int, limit: int, *, offset: int = 0
    ) -> tuple[Occurrence, ...]:
        return tuple(
            codec.read_occurrence(r)
            for r in _rows(
                view,
                "routine_occurrence",
                "person_id=? AND mission_day>=? AND state='missed'",
                (person_id, first_day),
                limit,
                offset=offset,
                order=" ORDER BY mission_day,occurrence_id",
            )
        )

    def instances_for_day(
        self, view: View, person_id: str, day: int, limit: int, *, offset: int = 0
    ) -> tuple[QuestionInstance, ...]:
        return tuple(
            codec.read_instance(r)
            for r in _rows(
                view,
                "routine_question_instance",
                "person_id=? AND carrier_day=?",
                (person_id, day),
                limit,
                offset=offset,
                order=" ORDER BY due_from,instance_id",
            )
        )

    def count_assistant_for_day(self, view: View, person_id: str, day: int) -> int:
        rows = view.connection.rows(
            "SELECT count(*) FROM routine_occurrence WHERE person_id=? AND mission_day=? AND cause_kind='assistant'",
            (person_id, day),
            limit=1,
        )
        return codec.integer(rows[0][0])

    def get_assistant_by_insight(self, view: View, insight_id: str) -> Occurrence | None:
        rows = _rows(view, "routine_occurrence", "cause_kind='assistant' AND insight_id=?", (insight_id,), 1)
        return codec.read_occurrence(rows[0]) if rows else None

    def count_open_nonassistant(self, view: View, person_id: str) -> int:
        rows = view.connection.rows(
            "SELECT count(*) FROM routine_occurrence WHERE person_id=? AND cause_kind<>'assistant' AND state IN ('not_due','due','later')",
            (person_id,),
            limit=1,
        )
        return codec.integer(rows[0][0])

    def count_active_overrides_for(self, view: View, person_id: str) -> int:
        rows = view.connection.rows(
            "SELECT count(*) FROM routine_override WHERE state='active' AND ((audience='person' AND person_id=?) OR (audience<>'person' AND override_id IN (SELECT override_id FROM routine_override_member WHERE person_id=?)))",
            (person_id, person_id),
            limit=1,
        )
        return codec.integer(rows[0][0])

    def overrides_from_warning(
        self, view: View, warning_id: str, limit: int, *, offset: int = 0
    ) -> tuple[Override, ...]:
        return tuple(
            codec.read_override(r)
            for r in _rows(
                view,
                "routine_override",
                "source_kind='warning' AND EXISTS (SELECT 1 FROM json_each(source_chain_json) WHERE value=?)",
                (warning_id,),
                limit,
                offset=offset,
                order=" ORDER BY started_at,override_id",
            )
        )
