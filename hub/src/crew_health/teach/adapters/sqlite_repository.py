"""SQLite storage of guided checks and coaching samples (E30 5.1) through the unit of work's StorageSession. A save
names the revision it read; zero affected rows is STALE_VERSION, so two clients never both commit a change."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.teach.application.ports import StoredSample
from crew_health.teach.domain.agreement import exact_text, parse_exact
from crew_health.teach.domain.capture import Beat, CountWindow
from crew_health.teach.domain.guided_check import Attempt, AttemptStatus, CameraResult, GuidedCheck, State

CHECK_COLUMNS = (
    "guided_check_id, revision, skill_id, check_id, environment_id, helper_id, subject_id, measured_record_id, "
    "count_origin, plan_run_id, plan_step_id, episode_id, role_assignment_id, state, teach_step_no, "
    "steps_done_json, current_attempt_no, helper_answer, answer_id, previous_answer_id, camera_json, result, "
    "difference_exact, count_exact, result_text, observation_id, experimental, pins_json, config_release_id, "
    "created_at, created_by_session"
)
ATTEMPT_COLUMNS = (
    "guided_check_id, attempt_no, started_utc, started_elapsed_ns, generation, count_seconds, opened_client_time, "
    "status, flags_json, camera_ticket_id, camera_unavailable_reason, count_exact, result, difference_exact, "
    "observation_id, finished_at"
)
SAMPLE_COLUMNS = "sample_id, help_session_id, skill_id, check_id, at, rate_per_min, reason, in_band, direction, text, experimental"
ACTIVE_STATES = ("teaching", "counting", "retry")
MAX_BEATS_READ = (
    6000  # bound of one attempt's beats: teach.max_count_seconds 600 times max_beats_per_second 10
)
BEATS_PAGE = 500  # one read stays under storage.max_rows (registered maximum 1000)


def _at(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _when(value: SqlValue) -> datetime:
    return datetime.fromisoformat(_text(value))


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return value


def _opt(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return value


def _exact(value: Fraction | None) -> str | None:
    return None if value is None else exact_text(value)


def _fraction(value: SqlValue) -> Fraction | None:
    return None if value is None else parse_exact(_text(value))


def _camera_json(camera: CameraResult | None) -> str | None:
    if camera is None:
        return None
    return json.dumps(
        {
            "observation_id": camera.observation_id,
            "value": _exact(camera.value),
            "unit": camera.unit,
            "reason": camera.reason,
            "origin_mode": camera.origin_mode,
        },
        sort_keys=True,
    )


def _camera(value: SqlValue) -> CameraResult | None:
    if value is None:
        return None
    data = cast(dict[str, str | None], json.loads(_text(value)))
    raw = data["value"]
    return CameraResult(
        data["observation_id"],
        None if raw is None else parse_exact(raw),
        data["unit"],
        data["reason"],
        _text(data["origin_mode"]),
    )


class SqliteGuidedChecks:
    """GuidedCheckRepository over E01's unit of work connection."""

    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheck | None:
        db = view.connection
        rows = db.rows(
            f"SELECT {CHECK_COLUMNS} FROM teach_guided_check WHERE guided_check_id = ?",
            (guided_check_id,),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        attempt = None if row[16] is None else self._attempt(db, guided_check_id, _int(row[16]))
        steps = cast(list[list[object]], json.loads(_text(row[15])))
        return GuidedCheck(
            guided_check_id=_text(row[0]),
            revision=_int(row[1]),
            skill_id=_text(row[2]),
            check_id=_text(row[3]),
            environment_id=_opt(row[4]),
            helper_id=_text(row[5]),
            subject_id=_text(row[6]),
            measured_record_id=_text(row[7]),
            count_origin=_text(row[8]),
            plan_run_id=_opt(row[9]),
            plan_step_id=_opt(row[10]),
            episode_id=_opt(row[11]),
            role_assignment_id=_opt(row[12]),
            state=cast(State, _text(row[13])),
            teach_step_no=_int(row[14]),
            steps_done=[(cast(int, n), datetime.fromisoformat(cast(str, at))) for n, at in steps],
            attempt=attempt,
            helper_answer=_opt(row[17]),
            answer_id=_opt(row[18]),
            previous_answer_id=_opt(row[19]),
            camera=_camera(row[20]),
            result=_opt(row[21]),
            difference_exact=_fraction(row[22]),
            count_exact=_fraction(row[23]),
            result_text=_opt(row[24]),
            observation_id=_opt(row[25]),
            experimental=row[26] == 1,
            pins=cast(dict[str, str], json.loads(_text(row[27]))),
            config_release_id=_text(row[28]),
            created_at=_when(row[29]),
            created_by_session=_text(row[30]),
        )

    def _attempt(self, db: StorageSession, guided_check_id: str, attempt_no: int) -> Attempt:
        rows = db.rows(
            f"SELECT {ATTEMPT_COLUMNS} FROM teach_attempt WHERE guided_check_id = ? AND attempt_no = ?",
            (guided_check_id, attempt_no),
            limit=1,
        )
        if not rows:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        a = rows[0]
        window = CountWindow(_int(a[1]), _when(a[2]), _int(a[3]), _text(a[4]), _int(a[5]), _when(a[6]))
        beats: list[tuple[SqlValue, ...]] = []
        while len(beats) < MAX_BEATS_READ:
            after = -1 if not beats else _int(beats[-1][0])
            page = db.rows(
                "SELECT sequence, elapsed_ns, at_utc, body_sha256 FROM teach_beat"
                " WHERE guided_check_id = ? AND attempt_no = ? AND sequence > ? ORDER BY sequence",
                (guided_check_id, attempt_no, after),
                limit=BEATS_PAGE,
            )
            beats.extend(page)
            if len(page) < BEATS_PAGE:
                break
        return Attempt(
            window=window,
            beats={_int(b[0]): Beat(_int(b[0]), _int(b[1]), _when(b[2]), _text(b[3])) for b in beats},
            status=cast(AttemptStatus, _text(a[7])),
            flags=set(cast(list[str], json.loads(_text(a[8])))),
            camera_ticket_id=_opt(a[9]),
            camera_unavailable_reason=_opt(a[10]),
            count_exact=_fraction(a[11]),
            result=_opt(a[12]),
            difference_exact=_fraction(a[13]),
            observation_id=_opt(a[14]),
            finished_at=None if a[15] is None else _when(a[15]),
        )

    @staticmethod
    def _values(check: GuidedCheck) -> tuple[SqlValue, ...]:
        steps = json.dumps([[n, _at(at)] for n, at in check.steps_done])
        attempt_no = None if check.attempt is None else check.attempt.window.attempt_no
        return (
            check.guided_check_id,
            check.revision,
            check.skill_id,
            check.check_id,
            check.environment_id,
            check.helper_id,
            check.subject_id,
            check.measured_record_id,
            check.count_origin,
            check.plan_run_id,
            check.plan_step_id,
            check.episode_id,
            check.role_assignment_id,
            check.state,
            check.teach_step_no,
            steps,
            attempt_no,
            check.helper_answer,
            check.answer_id,
            check.previous_answer_id,
            _camera_json(check.camera),
            check.result,
            _exact(check.difference_exact),
            _exact(check.count_exact),
            check.result_text,
            check.observation_id,
            1 if check.experimental else 0,
            json.dumps(check.pins, sort_keys=True),
            check.config_release_id,
            _at(check.created_at),
            check.created_by_session,
        )

    def add(self, uow: UnitOfWork, check: GuidedCheck) -> None:
        marks = ", ".join("?" * 31)
        uow.connection.execute(
            f"INSERT INTO teach_guided_check ({CHECK_COLUMNS}) VALUES ({marks})", self._values(check)
        )
        self._attempt_rows(uow.connection, check)

    def save(self, uow: UnitOfWork, check: GuidedCheck, *, expected_revision: int) -> None:
        names = [c.strip() for c in CHECK_COLUMNS.split(",")]
        assignments = ", ".join(f"{name} = ?" for name in names[1:])
        values = self._values(check)
        result = uow.connection.execute(
            f"UPDATE teach_guided_check SET {assignments} WHERE guided_check_id = ? AND revision = ?",
            (*values[1:], check.guided_check_id, expected_revision),
        )
        if result.affected_rows != 1:
            raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
        self._attempt_rows(uow.connection, check)

    @staticmethod
    def _attempt_rows(db: StorageSession, check: GuidedCheck) -> None:
        attempt = check.attempt
        if attempt is None:
            return
        w = attempt.window
        values: tuple[SqlValue, ...] = (
            check.guided_check_id,
            w.attempt_no,
            _at(w.started_utc),
            w.started_elapsed_ns,
            w.generation,
            w.count_seconds,
            _at(w.opened_client_time),
            attempt.status,
            json.dumps(sorted(attempt.flags)),
            attempt.camera_ticket_id,
            attempt.camera_unavailable_reason,
            _exact(attempt.count_exact),
            attempt.result,
            _exact(attempt.difference_exact),
            attempt.observation_id,
            None if attempt.finished_at is None else _at(attempt.finished_at),
        )
        names = [c.strip() for c in ATTEMPT_COLUMNS.split(",")]
        updates = ", ".join(f"{n} = excluded.{n}" for n in names[2:])
        db.execute(
            f"INSERT INTO teach_attempt ({ATTEMPT_COLUMNS}) VALUES ({', '.join('?' * 16)})"
            f" ON CONFLICT (guided_check_id, attempt_no) DO UPDATE SET {updates}",
            values,
        )
        for beat in attempt.new_beats:
            db.execute(
                "INSERT INTO teach_beat (guided_check_id, attempt_no, sequence, elapsed_ns, at_utc, body_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    check.guided_check_id,
                    w.attempt_no,
                    beat.sequence,
                    beat.elapsed_ns,
                    _at(beat.at_utc),
                    beat.body_sha256,
                ),
            )

    def count_active(self, uow: UnitOfWork, subject_id: str | None, skill_id: str | None) -> int:
        marks = ", ".join("?" * len(ACTIVE_STATES))
        sql = f"SELECT COUNT(*) FROM teach_guided_check WHERE state IN ({marks})"
        params: tuple[SqlValue, ...] = ACTIVE_STATES
        if subject_id is not None:
            sql += " AND subject_id = ?"
            params = (*params, subject_id)
        if skill_id is not None:
            sql += " AND skill_id = ?"
            params = (*params, skill_id)
        return _int(uow.connection.rows(sql, params, limit=1)[0][0])


class SqliteCoachingSamples:
    """CoachingSampleRepository: help session samples only; drill samples are never stored (4.4)."""

    def add(self, uow: UnitOfWork, sample: StoredSample, *, release_id: str) -> None:
        uow.connection.execute(
            f"INSERT INTO teach_sample ({SAMPLE_COLUMNS}, config_release_id) VALUES ({', '.join('?' * 12)})",
            (
                sample.sample_id,
                sample.help_session_id,
                sample.skill_id,
                sample.check_id,
                _at(sample.at),
                None if sample.rate_per_min is None else str(sample.rate_per_min),
                sample.reason,
                None if sample.in_band is None else int(sample.in_band),
                sample.direction,
                sample.text,
                int(sample.experimental),
                release_id,
            ),
        )

    def page(
        self, view: ReadSnapshot, help_session_id: str, *, after: tuple[datetime, str] | None, limit: int
    ) -> Sequence[StoredSample]:
        if after is None:
            rows = view.connection.rows(
                f"SELECT {SAMPLE_COLUMNS} FROM teach_sample WHERE help_session_id = ? ORDER BY at, sample_id",
                (help_session_id,),
                limit=limit,
            )
        else:
            rows = view.connection.rows(
                f"SELECT {SAMPLE_COLUMNS} FROM teach_sample WHERE help_session_id = ? AND (at, sample_id) > (?, ?)"
                " ORDER BY at, sample_id",
                (help_session_id, _at(after[0]), after[1]),
                limit=limit,
            )
        return tuple(self._sample(row) for row in rows)

    @staticmethod
    def _sample(row: tuple[SqlValue, ...]) -> StoredSample:
        return StoredSample(
            sample_id=_text(row[0]),
            help_session_id=_text(row[1]),
            skill_id=_text(row[2]),
            check_id=_text(row[3]),
            at=_when(row[4]),
            rate_per_min=None if row[5] is None else Decimal(_text(row[5])),
            reason=_opt(row[6]),
            in_band=None if row[7] is None else row[7] == 1,
            direction=_opt(row[8]),
            text=_text(row[9]),
            experimental=row[10] == 1,
        )

    def last_at(
        self, view: ReadSnapshot | UnitOfWork, help_session_id: str, check_id: str
    ) -> datetime | None:
        rows = view.connection.rows(
            "SELECT MAX(at) FROM teach_sample WHERE help_session_id = ? AND check_id = ?",
            (help_session_id, check_id),
            limit=1,
        )
        return None if not rows or rows[0][0] is None else _when(rows[0][0])
