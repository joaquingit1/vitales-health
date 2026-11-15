"""SQLite store of artifacts, pharmacy bundles and potency assessment runs (E32 5.2). Artifacts are content
addressed and re-hashed on every read; pruning is the only delete and needs its prune row first (the migration's
trigger aborts any other delete). Every read is bounded; SQLite never does decimal arithmetic."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal

from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.medicines.adapters.sqlite import instant, parse_instant
from crew_health.medicines.application.ports import ArtifactCorrupt, RunResult, RunRow, RunStart

type View = UnitOfWork | ReadSnapshot
TERMINAL = ("completed", "failed", "timed_out", "interrupted")
_RUN_COLUMNS = "run_id, mission_id, origin_mode, trigger, task_id, process_epoch, at_utc, by_id, command_key"
_SELECT_RUN = (
    "SELECT r.run_id, r.mission_id, r.origin_mode, r.trigger, r.task_id, r.process_epoch, r.at_utc, r.by_id, "
    "r.command_key, s.as_of_utc, s.bundle_sha256, s.inputs_sha256 "
    "FROM medicines_assessment_run r LEFT JOIN medicines_assessment_start s ON s.run_id = r.run_id"
)


def _t(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ArtifactCorrupt("medicines run row is malformed")
    return value


def _run(r: tuple[SqlValue, ...]) -> RunRow:
    start = (
        None
        if r[9] is None
        else RunStart(as_of=parse_instant(_t(r[9])), bundle_sha256=_t(r[10]), inputs_sha256=_t(r[11]))
    )
    return RunRow(
        run_id=_t(r[0]),
        mission_id=_t(r[1]),
        origin_mode=_t(r[2]),
        trigger=_t(r[3]),
        task_id=_t(r[4]),
        process_epoch=_t(r[5]),
        at=parse_instant(_t(r[6])),
        by_id=_t(r[7]),
        command_key=None if r[8] is None else _t(r[8]),
        start=start,
    )


class SqlitePotencyStore:
    """Implements medicines.application.ports.PotencyStore."""

    def __init__(self, *, max_rows: int) -> None:
        self.max_rows = max_rows

    def put_artifact(self, uow: UnitOfWork, kind: str, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        uow.connection.execute(
            "INSERT INTO medicines_artifact (sha256, kind, bytes, size_bytes, at_utc) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (sha256) DO NOTHING",
            (sha, kind, data, len(data), instant(uow.now)),
        )
        return sha

    def artifact(self, view: View, sha256: str) -> bytes | None:
        rows = view.connection.rows("SELECT bytes FROM medicines_artifact WHERE sha256=?", (sha256,), limit=1)
        if not rows:
            return None
        data = rows[0][0]
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != sha256:
            raise ArtifactCorrupt(sha256)
        return data

    def has_artifact(self, view: View, sha256: str) -> bool:
        return bool(
            view.connection.rows("SELECT 1 FROM medicines_artifact WHERE sha256=?", (sha256,), limit=1)
        )

    def prune_artifact(self, uow: UnitOfWork, sha256: str, run_id: str) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_artifact_prune (sha256, run_id, at_utc) VALUES (?, ?, ?) "
            "ON CONFLICT (sha256, run_id) DO NOTHING",
            (sha256, run_id, instant(uow.now)),
        )
        uow.connection.execute("DELETE FROM medicines_artifact WHERE sha256=?", (sha256,))

    def add_bundle(self, uow: UnitOfWork, bundle_sha256: str, by_id: str) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_bundle (bundle_sha256, imported_at_utc, by_id) VALUES (?, ?, ?) "
            "ON CONFLICT (bundle_sha256) DO NOTHING",
            (bundle_sha256, instant(uow.now), by_id),
        )

    def has_bundle(self, view: View, bundle_sha256: str) -> bool:
        return bool(
            view.connection.rows(
                "SELECT 1 FROM medicines_bundle WHERE bundle_sha256=?", (bundle_sha256,), limit=1
            )
        )

    def activate_bundle(self, uow: UnitOfWork, bundle_sha256: str, by_id: str) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_bundle_activation (bundle_sha256, at_utc, by_id) VALUES (?, ?, ?)",
            (bundle_sha256, instant(uow.now), by_id),
        )

    def active_bundle(self, view: View) -> str | None:
        rows = view.connection.rows(
            "SELECT bundle_sha256 FROM medicines_bundle_activation ORDER BY seq DESC LIMIT 1", (), limit=1
        )
        return _t(rows[0][0]) if rows else None

    def add_run(self, uow: UnitOfWork, run: RunRow) -> None:
        uow.connection.execute(
            f"INSERT INTO medicines_assessment_run ({_RUN_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.mission_id,
                run.origin_mode,
                run.trigger,
                run.task_id,
                run.process_epoch,
                instant(run.at),
                run.by_id,
                run.command_key,
            ),
        )

    def start_run(self, uow: UnitOfWork, run_id: str, start: RunStart) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_assessment_start (run_id, as_of_utc, bundle_sha256, inputs_sha256, at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, instant(start.as_of), start.bundle_sha256, start.inputs_sha256, instant(uow.now)),
        )

    def run(self, view: View, run_id: str) -> RunRow | None:
        rows = view.connection.rows(f"{_SELECT_RUN} WHERE r.run_id=?", (run_id,), limit=1)
        return _run(rows[0]) if rows else None

    def run_by_key(self, view: View, command_key: str) -> RunRow | None:
        rows = view.connection.rows(f"{_SELECT_RUN} WHERE r.command_key=?", (command_key,), limit=1)
        return _run(rows[0]) if rows else None

    def runs(self, view: View, mission_id: str, origin_mode: str, *, limit: int) -> tuple[RunRow, ...]:
        """Newest first."""
        rows = view.connection.rows(
            f"{_SELECT_RUN} WHERE r.mission_id=? AND r.origin_mode=? ORDER BY r.at_utc DESC, r.rowid DESC LIMIT ?",
            (mission_id, origin_mode, limit),
            limit=limit,
        )
        return tuple(_run(r) for r in rows)

    def add_run_event(self, uow: UnitOfWork, run_id: str, status: str, error_code: str | None) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_assessment_event (run_id, seq, status, error_code, at_utc) VALUES "
            "(?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM medicines_assessment_event WHERE run_id=?), ?, ?, ?)",
            (run_id, run_id, status, error_code, instant(uow.now)),
        )

    def run_status(self, view: View, run_id: str) -> tuple[str, str | None]:
        rows = view.connection.rows(
            "SELECT status, error_code FROM medicines_assessment_event WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
            limit=1,
        )
        if not rows:
            raise ArtifactCorrupt("a run without events: " + run_id)
        return _t(rows[0][0]), None if rows[0][1] is None else _t(rows[0][1])

    def unfinished_runs(self, view: View, *, limit: int) -> tuple[str, ...]:
        """Runs without a terminal event, oldest first (every partition: start-up marks them interrupted)."""
        rows = view.connection.rows(
            "SELECT run_id FROM medicines_assessment_run r WHERE NOT EXISTS (SELECT 1 FROM medicines_assessment_event e "
            f"WHERE e.run_id = r.run_id AND e.status IN {TERMINAL!r}) ORDER BY rowid",
            (),
            limit=limit,
        )
        return tuple(_t(r[0]) for r in rows)

    def add_result(self, uow: UnitOfWork, result: RunResult) -> None:
        uow.connection.execute(
            "INSERT INTO medicines_assessment_result (run_id, result_sha256, engine_version, summaries_json) "
            "VALUES (?, ?, ?, ?)",
            (result.run_id, result.result_sha256, result.engine_version, result.summaries_json),
        )

    def result(self, view: View, run_id: str) -> RunResult | None:
        rows = view.connection.rows(
            "SELECT run_id, result_sha256, engine_version, summaries_json FROM medicines_assessment_result WHERE run_id=?",
            (run_id,),
            limit=1,
        )
        return RunResult(*(_t(x) for x in rows[0])) if rows else None

    def latest_completed(self, view: View, mission_id: str, origin_mode: str) -> RunRow | None:
        """The completed run of the partition with the latest `as_of` (a later start breaks a tie)."""
        rows = view.connection.rows(
            f"{_SELECT_RUN} JOIN medicines_assessment_result res ON res.run_id = r.run_id "
            "WHERE r.mission_id=? AND r.origin_mode=? ORDER BY s.as_of_utc DESC, s.rowid DESC LIMIT 1",
            (mission_id, origin_mode),
            limit=1,
        )
        return _run(rows[0]) if rows else None

    def referenced_runs(self, view: View, *, limit: int) -> frozenset[str]:
        """Runs a proposal names (and so every administration confirmed from it): kept for the mission's life."""
        rows = view.connection.rows(
            "SELECT DISTINCT json_extract(body_json, '$.wire.potency.assessment_run_id') FROM medicines_record "
            "WHERE kind='proposal' AND json_extract(body_json, '$.wire.potency.assessment_run_id') IS NOT NULL",
            (),
            limit=limit,
        )
        return frozenset(_t(r[0]) for r in rows)

    def net_by_partition(self, view: View, *, limit: int) -> Mapping[tuple[str, str, str, str], Decimal]:
        """Net movements per (mission, mode, environment, lot) over every partition, for guard M-A1."""
        rows = view.connection.rows(
            "SELECT mission_id, origin_mode, environment_id, lot_id, delta_decimal FROM medicines_movement",
            (),
            limit=limit,
        )
        out: dict[tuple[str, str, str, str], Decimal] = {}
        for mission, mode, environment, lot, delta in rows:
            key = (_t(mission), _t(mode), _t(environment), _t(lot))
            out[key] = out.get(key, Decimal(0)) + Decimal(_t(delta))
        return out
