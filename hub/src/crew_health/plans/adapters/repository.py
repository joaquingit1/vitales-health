"""Bounded SQLite persistence of the plan aggregate and durable escalation intents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal, cast

from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork
from crew_health.plans.application.ports import EscalationIntent as EscalationIntent
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.model import StageId
from crew_health.plans.domain.run import (
    CloseDisposition,
    Closure,
    Escalation,
    LinkAtOpen,
    OriginMode,
    PlanRun,
    RunRecheck,
    RunState,
    RunStep,
    StepState,
)

type View = ReadSnapshot | UnitOfWork

# These columns are the migration's stable storage shape, never derived from user input.
_RUN_COLUMNS = (
    "plan_run_id, revision, plan_id, plan_version, subject_person_id, episode_id, help_session_id, "
    "environment_id, origin_mode, opened_release_id, state, opened_by_kind, opened_by_id, opened_at, "
    "handoff_id, link_status, link_gap_kind, link_next_contact_at, close_disposition, close_note, "
    "close_by_kind, close_by_id, close_at"
)
_INTENT_COLUMNS = (
    "escalation_id, plan_run_id, step_id, occurrence, expected_run_revision, subject_person_id, "
    "flow_id, pinned_release_id, origin_mode, client_time, session_json, parent_key, parent_actor, "
    "state, help_session_id"
)


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StorageUnavailable("corrupt")
    return value


def _integer(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise StorageUnavailable("corrupt")
    return value


def _optional_text(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _at(value: SqlValue) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value))
        require_utc(result)
        return result
    except ValueError:
        raise StorageUnavailable("corrupt") from None


def _optional_at(value: SqlValue) -> datetime | None:
    return None if value is None else _at(value)


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    require_utc(value)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _actor(kind: SqlValue, actor_id: SqlValue) -> Actor:
    text = _text(kind)
    if text not in ("person", "station", "ground", "operator", "adapter", "engine", "assistant"):
        raise StorageUnavailable("corrupt")
    return Actor(
        cast(Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"], text),
        _text(actor_id),
    )


class SqlitePlanRuns:
    def __init__(self, settings: PlansSettings) -> None:
        self.settings = settings

    def insert(self, uow: UnitOfWork, run: PlanRun, plan_version: str) -> None:
        uow.connection.execute(
            "INSERT INTO plans_run (" + _RUN_COLUMNS + ") VALUES (" + ",".join("?" for _ in range(23)) + ")",
            self._run_values(run, plan_version),
        )
        self._save_children(uow, run)

    def save(self, uow: UnitOfWork, run: PlanRun) -> None:
        version = self.version(uow, run.plan_run_id)
        if version is None:
            raise StorageUnavailable("corrupt")
        values = self._run_values(run, version)
        assignments = ",".join(name.strip() + "=?" for name in _RUN_COLUMNS.split(",")[1:])
        result = uow.connection.execute(
            "UPDATE plans_run SET " + assignments + " WHERE plan_run_id=?", values[1:] + values[:1]
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")
        self._save_children(uow, run)

    @staticmethod
    def _run_values(run: PlanRun, version: str) -> tuple[SqlValue, ...]:
        close = run.close
        return (
            run.plan_run_id,
            run.revision,
            run.plan_id,
            version,
            run.subject_person_id,
            run.episode_id,
            run.help_session_id,
            run.environment_id,
            run.origin_mode,
            run.opened_release_id,
            run.state,
            run.opened_by.kind,
            run.opened_by.id,
            _instant(run.opened_at),
            run.handoff_id,
            run.link_state_at_open.status,
            run.link_state_at_open.gap_kind,
            _instant(run.link_state_at_open.next_contact_at),
            close.disposition if close else None,
            close.note if close else None,
            close.by.kind if close else None,
            close.by.id if close else None,
            _instant(close.at) if close else None,
        )

    def _save_children(self, uow: UnitOfWork, run: PlanRun) -> None:
        for name, version in run.pins:
            uow.connection.execute(
                "INSERT INTO plans_run_pin VALUES (?,?,?) ON CONFLICT (plan_run_id,name) DO NOTHING",
                (run.plan_run_id, name, version),
            )
        for ordinal, step in enumerate(run.steps):
            self._save_step(uow, run.plan_run_id, ordinal, step)
        for recheck in run.rechecks:
            self._save_recheck(uow, run.plan_run_id, recheck)
        for escalation in run.escalations:
            uow.connection.execute(
                "INSERT INTO plans_run_escalation VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (plan_run_id,help_session_id) DO UPDATE SET "
                "session_closed=excluded.session_closed, escalation_id=excluded.escalation_id",
                (
                    run.plan_run_id,
                    escalation.step_id,
                    escalation.help_session_id,
                    _instant(escalation.at),
                    int(escalation.session_closed),
                    escalation.escalation_id,
                ),
            )

    @staticmethod
    def _save_step(uow: UnitOfWork, run_id: str, ordinal: int, step: RunStep) -> None:
        uow.connection.execute(
            "INSERT INTO plans_run_step VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (plan_run_id,step_id) DO UPDATE SET "
            "state=excluded.state,by_kind=excluded.by_kind,by_id=excluded.by_id,at=excluded.at,"
            "substitute_step_id=excluded.substitute_step_id,guided_check_id=excluded.guided_check_id,"
            "not_available_reason=excluded.not_available_reason,runs=excluded.runs,"
            "recorded_revision=excluded.recorded_revision",
            (
                run_id,
                step.step_id,
                ordinal,
                step.stage_id,
                int(step.substitute_only),
                step.principal_step_id,
                step.state,
                step.by.kind if step.by else None,
                step.by.id if step.by else None,
                _instant(step.at),
                step.substitute_step_id,
                step.guided_check_id,
                step.not_available_reason,
                step.runs,
                step.recorded_revision,
            ),
        )
        if step.state == "done" and step.runs >= 1 and step.at is not None and step.by is not None:
            # Append only: an occurrence already recorded is never rewritten (review item 1).
            uow.connection.execute(
                "INSERT INTO plans_run_recheck_entry VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (plan_run_id,step_id,occurrence) DO NOTHING",
                (
                    run_id,
                    step.step_id,
                    step.runs,
                    _instant(step.at),
                    step.by.kind,
                    step.by.id,
                    step.guided_check_id,
                    step.recorded_revision,
                ),
            )
        for observation_id in step.observation_ids:
            uow.connection.execute(
                "INSERT OR IGNORE INTO plans_run_step_observation VALUES (?,?,?,?)",
                (run_id, step.step_id, step.runs, observation_id),
            )
        for supply_use_id in step.supply_use_ids:
            uow.connection.execute(
                "INSERT OR IGNORE INTO plans_run_step_supply_use VALUES (?,?,?,?)",
                (run_id, step.step_id, step.runs, supply_use_id),
            )

    @staticmethod
    def _save_recheck(uow: UnitOfWork, run_id: str, recheck: RunRecheck) -> None:
        uow.connection.execute(
            "INSERT INTO plans_run_recheck VALUES (?,?,?,?) ON CONFLICT (plan_run_id,step_id) "
            "DO UPDATE SET due_at=excluded.due_at, done_at=excluded.done_at",
            (run_id, recheck.step_id, _instant(recheck.due_at), _instant(recheck.done_at)),
        )
        uow.connection.execute(
            "DELETE FROM plans_run_recheck_observation WHERE plan_run_id=? AND step_id=?",
            (run_id, recheck.step_id),
        )
        for observation_id in recheck.observation_ids:
            uow.connection.execute(
                "INSERT INTO plans_run_recheck_observation VALUES (?,?,?)",
                (run_id, recheck.step_id, observation_id),
            )

    def load(self, view: View, plan_run_id: str) -> PlanRun | None:
        # A primary-key query can return at most one row.
        rows = view.connection.rows(
            "SELECT " + _RUN_COLUMNS + " FROM plans_run WHERE plan_run_id=?", (plan_run_id,), limit=1
        )
        if not rows:
            return None
        row = rows[0]
        close = None
        if row[22] is not None:
            close = Closure(
                cast(CloseDisposition, _text(row[18])),
                _optional_text(row[19]),
                _actor(row[20], row[21]),
                _at(row[22]),
            )
        return PlanRun(
            _text(row[0]),
            _integer(row[1]),
            _text(row[2]),
            self._pins(view, plan_run_id),
            _text(row[9]),
            _text(row[4]),
            _optional_text(row[5]),
            _optional_text(row[6]),
            _text(row[7]),
            cast(OriginMode, _text(row[8])),
            _actor(row[11], row[12]),
            _at(row[13]),
            self._steps(view, plan_run_id),
            self._rechecks(view, plan_run_id),
            self._escalations(view, plan_run_id),
            _optional_text(row[14]),
            LinkAtOpen(
                cast(Literal["up", "delayed", "down"], _text(row[15])),
                _optional_text(row[16]),
                _optional_at(row[17]),
            ),
            close,
            cast(RunState, _text(row[10])),
        )

    @staticmethod
    def _pins(view: View, run_id: str) -> tuple[tuple[str, str], ...]:
        # The spec pins exactly five configuration names.
        return tuple(
            (_text(r[0]), _text(r[1]))
            for r in view.connection.rows(
                "SELECT name,version FROM plans_run_pin WHERE plan_run_id=? ORDER BY name", (run_id,), limit=5
            )
        )

    def _steps(self, view: View, run_id: str) -> tuple[RunStep, ...]:
        rows = view.connection.rows(
            "SELECT step_id,stage_id,substitute_only,principal_step_id,state,by_kind,by_id,at,"
            "substitute_step_id,guided_check_id,runs,recorded_revision,not_available_reason "
            "FROM plans_run_step WHERE plan_run_id=? ORDER BY ordinal",
            (run_id,),
            limit=self.settings.max_steps_per_plan,
        )
        return tuple(self._step(view, run_id, row) for row in rows)

    def _step(self, view: View, run_id: str, row: tuple[SqlValue, ...]) -> RunStep:
        step_id, runs = _text(row[0]), _integer(row[10])
        observations = view.connection.rows(
            "SELECT observation_id FROM plans_run_step_observation "
            "WHERE plan_run_id=? AND step_id=? AND occurrence=? ORDER BY rowid",
            (run_id, step_id, runs),
            limit=self.settings.max_observations_per_record,
        )
        uses = self._supply_use_ids(view, run_id, step_id, runs)
        return RunStep(
            step_id,
            cast(StageId, _text(row[1])),
            bool(_integer(row[2])),
            _optional_text(row[3]),
            cast(StepState, _text(row[4])),
            None if row[5] is None else _actor(row[5], row[6]),
            _optional_at(row[7]),
            _optional_text(row[8]),
            _optional_text(row[9]),
            tuple(_text(r[0]) for r in observations),
            uses,
            runs,
            None if row[11] is None else _integer(row[11]),
            _optional_text(row[12]),
        )

    def _supply_use_ids(self, view: View, run_id: str, step_id: str, occurrence: int) -> tuple[str, ...]:
        parameters: tuple[SqlValue, ...] = (run_id, step_id, occurrence)
        count_rows = view.connection.rows(
            "SELECT count(*) FROM plans_run_step_supply_use "
            "WHERE plan_run_id=? AND step_id=? AND occurrence=?",
            parameters,
            limit=1,
        )  # Count aggregate returns exactly one row.
        count = _integer(count_rows[0][0])
        # Every supply reference originates in bounded plan content and consumes at least one byte.
        if count > self.settings.max_document_bytes:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "plans_supply_reference_bound")
        page = self.settings.max_steps_per_plan
        result: list[str] = []
        for offset in range(0, count, page):
            result.extend(
                _text(row[0])
                for row in view.connection.rows(
                    "SELECT supply_use_id FROM plans_run_step_supply_use "
                    "WHERE plan_run_id=? AND step_id=? AND occurrence=? ORDER BY rowid LIMIT ? OFFSET ?",
                    parameters + (page, offset),
                    limit=page,
                )
            )
        return tuple(result)

    def _rechecks(self, view: View, run_id: str) -> tuple[RunRecheck, ...]:
        rows = view.connection.rows(
            "SELECT r.step_id,r.due_at,r.done_at FROM plans_run_recheck r "
            "JOIN plans_run_step s ON s.plan_run_id=r.plan_run_id AND s.step_id=r.step_id "
            "WHERE r.plan_run_id=? ORDER BY s.ordinal",
            (run_id,),
            limit=self.settings.max_steps_per_plan,
        )
        result: list[RunRecheck] = []
        for row in rows:
            step_id = _text(row[0])
            observations = view.connection.rows(
                "SELECT observation_id FROM plans_run_recheck_observation "
                "WHERE plan_run_id=? AND step_id=? ORDER BY rowid",
                (run_id, step_id),
                limit=self.settings.max_observations_per_record,
            )
            result.append(
                RunRecheck(
                    step_id,
                    _optional_at(row[1]),
                    _optional_at(row[2]),
                    tuple(_text(r[0]) for r in observations),
                )
            )
        return tuple(result)

    def _escalations(self, view: View, run_id: str) -> tuple[Escalation, ...]:
        return tuple(
            Escalation(_text(r[0]), _text(r[1]), _at(r[2]), bool(_integer(r[3])), _optional_text(r[4]))
            for r in view.connection.rows(
                "SELECT step_id,help_session_id,at,session_closed,escalation_id "
                "FROM plans_run_escalation WHERE plan_run_id=? ORDER BY rowid",
                (run_id,),
                limit=self.settings.max_escalations_per_run,
            )
        )

    @staticmethod
    def version(view: View, plan_run_id: str) -> str | None:
        rows = view.connection.rows(
            "SELECT plan_version FROM plans_run WHERE plan_run_id=?", (plan_run_id,), limit=1
        )  # Primary-key query, bounded to one row.
        return None if not rows else _text(rows[0][0])

    def list_open(
        self, view: View, subject_person_id: str, after: str | None, limit: int
    ) -> tuple[PlanRun, ...]:
        bounded = min(limit, self.settings.page_limit_max)
        if bounded < 1:
            raise ValueError("page limit must be positive")
        rows = view.connection.rows(
            "SELECT plan_run_id FROM plans_run WHERE subject_person_id=? AND state<>'closed' "
            "AND (? IS NULL OR plan_run_id>?) ORDER BY plan_run_id LIMIT ?",
            (subject_person_id, after, after, bounded),
            limit=bounded,
        )
        return tuple(run for row in rows if (run := self.load(view, _text(row[0]))) is not None)

    @staticmethod
    def open_for(view: View, plan_id: str, subject: str, episode_or_session: str) -> str | None:
        rows = view.connection.rows(
            "SELECT plan_run_id FROM plans_run WHERE plan_id=? AND subject_person_id=? "
            "AND coalesce(episode_id,help_session_id)=? AND state<>'closed'",
            (plan_id, subject, episode_or_session),
            limit=1,
        )  # The unique partial index limits this query to one row.
        return None if not rows else _text(rows[0][0])

    @staticmethod
    def pin_plan(uow: UnitOfWork, version: str, plan_id: str, plan_json: str) -> None:
        digest = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        uow.connection.execute(
            "INSERT OR IGNORE INTO plans_parsed_plan VALUES (?,?,?,?)", (version, plan_id, plan_json, digest)
        )

    @staticmethod
    def pinned_plan_json(view: View, version: str, plan_id: str) -> tuple[str, str] | None:
        rows = view.connection.rows(
            "SELECT plan_json,sha256 FROM plans_parsed_plan WHERE plan_version=? AND plan_id=?",
            (version, plan_id),
            limit=1,
        )  # Composite primary-key lookup, at most one row.
        return None if not rows else (_text(rows[0][0]), _text(rows[0][1]))

    @staticmethod
    def pin_context(uow: UnitOfWork, plan_run_id: str, context_json: str) -> None:
        digest = hashlib.sha256(context_json.encode("utf-8")).hexdigest()
        uow.connection.execute(
            "INSERT OR IGNORE INTO plans_run_context VALUES (?,?,?)",
            (plan_run_id, context_json, digest),
        )

    @staticmethod
    def pinned_context(view: View, plan_run_id: str) -> tuple[str, str] | None:
        rows = view.connection.rows(
            "SELECT context_json,sha256 FROM plans_run_context WHERE plan_run_id=?",
            (plan_run_id,),
            limit=1,
        )  # Primary-key lookup, bounded to one row.
        return None if not rows else (_text(rows[0][0]), _text(rows[0][1]))

    @staticmethod
    def insert_intent(uow: UnitOfWork, intent: EscalationIntent) -> None:
        uow.connection.execute(
            "INSERT INTO plans_escalation_intent (" + _INTENT_COLUMNS + ") "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent.escalation_id,
                intent.plan_run_id,
                intent.step_id,
                intent.occurrence,
                intent.expected_run_revision,
                intent.subject_person_id,
                intent.flow_id,
                intent.pinned_release_id,
                intent.origin_mode,
                intent.client_time,
                intent.session_json,
                intent.parent_key,
                intent.parent_actor,
                intent.state,
                intent.help_session_id,
            ),
        )

    @staticmethod
    def intent(view: View, escalation_id: str) -> EscalationIntent | None:
        rows = view.connection.rows(
            "SELECT " + _INTENT_COLUMNS + " FROM plans_escalation_intent WHERE escalation_id=?",
            (escalation_id,),
            limit=1,
        )  # Intent primary-key lookup, at most one row.
        return None if not rows else _intent(rows[0])

    def pending_intents(
        self, view: View, plan_run_id: str, step_id: str | None = None
    ) -> tuple[EscalationIntent, ...]:
        return tuple(
            _intent(row)
            for row in view.connection.rows(
                "SELECT " + _INTENT_COLUMNS + " FROM plans_escalation_intent "
                "WHERE plan_run_id=? AND state='pending' AND (? IS NULL OR step_id=?) ORDER BY occurrence",
                (plan_run_id, step_id, step_id),
                limit=self.settings.max_escalations_per_run,
            )
        )

    @staticmethod
    def intent_count(view: View, plan_run_id: str) -> int:
        rows = view.connection.rows(
            "SELECT count(*) FROM plans_escalation_intent WHERE plan_run_id=?",
            (plan_run_id,),
            limit=1,
        )  # Aggregate query, exactly one row regardless of history length.
        return _integer(rows[0][0])

    @staticmethod
    def next_occurrence(view: View, plan_run_id: str, step_id: str) -> int:
        rows = view.connection.rows(
            "SELECT coalesce(max(occurrence),0)+1 FROM plans_escalation_intent "
            "WHERE plan_run_id=? AND step_id=?",
            (plan_run_id, step_id),
            limit=1,
        )  # Aggregate query, exactly one row regardless of history length.
        return _integer(rows[0][0])

    @staticmethod
    def record_intent(uow: UnitOfWork, escalation_id: str, help_session_id: str) -> None:
        result = uow.connection.execute(
            "UPDATE plans_escalation_intent SET state='recorded',help_session_id=? WHERE escalation_id=?",
            (help_session_id, escalation_id),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    def runs_with_open_escalation(self, view: View, help_session_id: str) -> tuple[PlanRun, ...]:
        counts = view.connection.rows(
            "SELECT count(*) FROM plans_run_escalation WHERE help_session_id=? AND session_closed=0",
            (help_session_id,),
            limit=1,
        )  # Count aggregate returns exactly one row.
        count = _integer(counts[0][0])
        page = self.settings.page_limit_max
        # Shared-session reconciliation has a finite aggregate ceiling, separate from its per-query page.
        if count > self.settings.max_plans_per_document * page:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "plans_shared_session_run_bound")
        result: list[PlanRun] = []
        for offset in range(0, count, page):
            rows = view.connection.rows(
                "SELECT plan_run_id FROM plans_run_escalation WHERE help_session_id=? "
                "AND session_closed=0 ORDER BY plan_run_id LIMIT ? OFFSET ?",
                (help_session_id, page, offset),
                limit=page,
            )
            for row in rows:
                run = self.load(view, _text(row[0]))
                if run is None:
                    raise StorageUnavailable("corrupt")
                result.append(run)
        return tuple(result)


def _intent(row: tuple[SqlValue, ...]) -> EscalationIntent:
    return EscalationIntent(
        _text(row[0]),
        _text(row[1]),
        _text(row[2]),
        _integer(row[3]),
        _integer(row[4]),
        _text(row[5]),
        _optional_text(row[6]),
        _text(row[7]),
        cast(OriginMode, _text(row[8])),
        _text(row[9]),
        _text(row[10]),
        _text(row[11]),
        _text(row[12]),
        cast(Literal["pending", "recorded"], _text(row[13])),
        _optional_text(row[14]),
    )
