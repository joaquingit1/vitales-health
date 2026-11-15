"""SQLite stores of the warnings engine (E24 5.1) over the unit of work's StorageSession.

Every read is bounded by an explicit limit the caller takes from hub settings; a storage or integrity error is never
caught here, so it fails the command loudly (AGENTS 6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.rules.application import codec
from crew_health.rules.domain.dwell import DwellMarker
from crew_health.rules.domain.errors import InvariantViolation
from crew_health.rules.domain.inputs import MissionEventInput
from crew_health.rules.domain.occurrences import Occurrence
from crew_health.rules.domain.vocabulary import SPENT_CLEAR_REASONS, ConditionKind, OriginMode
from crew_health.rules.domain.warning import TriggerRef, Warning

type Conn = StorageSession

_SPENT_REASONS = ",".join("'" + r.value + "'" for r in sorted(SPENT_CLEAR_REASONS, key=str))


def _s(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise InvariantViolation("stored column is not text")
    return value


def _i(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise InvariantViolation("stored column is not an integer")
    return value


class WarningStore:
    """Local warnings only (origin = this node)."""

    def get(self, c: Conn, warning_id: str) -> Warning | None:
        rows = c.rows("SELECT body_json FROM rules_warning WHERE warning_id = ?", (warning_id,), limit=1)
        return codec.loads(_s(rows[0][0])) if rows else None

    def active(self, c: Conn, *, limit: int) -> tuple[Warning, ...]:
        rows = c.rows(
            "SELECT body_json FROM rules_warning WHERE state IN ('raised','acknowledged') "
            "ORDER BY warning_id LIMIT ?",
            (limit + 1,),
            limit=limit + 1,
        )
        if len(rows) > limit:
            raise InvariantViolation("active warnings exceed rules.active_scan_max")
        return tuple(codec.loads(_s(r[0])) for r in rows)

    def page(
        self, c: Conn, *, state: str, after: tuple[int, str, str] | None, limit: int
    ) -> tuple[Warning, ...]:
        where = "state IN ('raised','acknowledged')" if state == "active" else "1 = 1"
        params: tuple[SqlValue, ...] = ()
        if after is not None:
            where += " AND (headline_rank, raised_at, warning_id) > (?, ?, ?)"
            params = (after[0], after[1], after[2])
        rows = c.rows(
            f"SELECT body_json FROM rules_warning WHERE {where} "  # noqa: S608 - fixed fragments only
            "ORDER BY headline_rank, raised_at, warning_id LIMIT ?",
            (*params, limit),
            limit=limit,
        )
        return tuple(codec.loads(_s(r[0])) for r in rows)

    def spent(
        self, c: Conn, kind: ConditionKind, refs: Sequence[str]
    ) -> frozenset[tuple[str, str, TriggerRef]]:
        found: set[tuple[str, str, TriggerRef]] = set()
        for ref in sorted(set(refs)):
            rows = c.rows(
                "SELECT definition_id, scope_key FROM rules_warning WHERE trigger_kind = ? AND trigger_ref = ? "
                "AND (state IN ('resolved','dismissed') OR "
                f"(state = 'cleared' AND clear_reason IN ({_SPENT_REASONS}))) LIMIT 200",  # noqa: S608
                (kind.value, ref),
                limit=200,
            )
            found.update((_s(r[0]), _s(r[1]), TriggerRef(kind, ref)) for r in rows)
        return frozenset(found)

    def insert(self, c: Conn, w: Warning) -> None:
        c.execute(
            "INSERT INTO rules_warning (warning_id, revision, origin_node_id, definition_id, config_release_id, "
            "rule_fingerprint, content_hash, trigger_kind, trigger_ref, level, raised_by, sensor_type_id, "
            "channel_id, scope, scope_key, person_id, environment_id, origin_mode, insight_id, headline_rank, "
            "state, raised_at, cleared_at, clear_reason, steps_json, evidence_json, body_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            self._columns(w),
        )

    def update(self, c: Conn, w: Warning, *, expected_revision: int) -> bool:
        """False when another writer moved the revision: the caller answers STALE_VERSION."""
        if w.revision != expected_revision + 1:
            raise InvariantViolation("a warning changes one revision at a time")
        result = c.execute(
            "UPDATE rules_warning SET revision = ?, state = ?, cleared_at = ?, clear_reason = ?, body_json = ? "
            "WHERE warning_id = ? AND revision = ?",
            (
                w.revision,
                w.state.value,
                None if w.cleared_at is None else codec.instant(w.cleared_at),
                None if w.clear_reason is None else w.clear_reason.value,
                codec.dumps(w),
                w.warning_id,
                expected_revision,
            ),
        )
        return result.affected_rows == 1

    @staticmethod
    def _columns(w: Warning) -> tuple[SqlValue, ...]:
        return (
            w.warning_id, w.revision, w.origin_node_id, w.definition_id, w.config_release_id,
            w.rule_fingerprint, w.content_hash, w.trigger.kind.value, w.trigger.ref, w.level.value,
            w.raised_by.value, w.sensor_type_id, w.channel_id, w.scope.value, w.scope_key, w.person_id,
            w.environment_id.value, w.origin_mode.value, w.insight_id, w.headline.rank, w.state.value,
            codec.instant(w.raised_at), None if w.cleared_at is None else codec.instant(w.cleared_at),
            None if w.clear_reason is None else w.clear_reason.value,
            json.dumps(list(w.steps), ensure_ascii=False),
            json.dumps([codec.evidence_raw(e) for e in w.evidence], sort_keys=True, ensure_ascii=False),
            codec.dumps(w),
        )  # fmt: skip


def _occurrence(r: tuple[SqlValue, ...]) -> Occurrence:
    return Occurrence(
        _s(r[0]), _s(r[1]), _s(r[2]), _s(r[3]), codec.parse_instant(r[4]), None if r[5] is None else _s(r[5])
    )


class OccurrenceStore:
    _COLS = (
        "COALESCE(warning_id,''), definition_id, scope_key, trigger_ref, occurred_at, resolved_by_event_id"
    )

    def for_warnings(
        self, c: Conn, warning_ids: Sequence[str], *, limit: int
    ) -> dict[str, tuple[Occurrence, ...]]:
        found: dict[str, tuple[Occurrence, ...]] = {}
        for wid in sorted(warning_ids):
            rows = c.rows(
                f"SELECT {self._COLS} FROM rules_occurrence WHERE warning_id = ? AND kind = 'trigger' "  # noqa: S608
                "ORDER BY occurred_at, trigger_ref LIMIT ?",
                (wid, limit),
                limit=limit,
            )
            found[wid] = tuple(_occurrence(r) for r in rows)
        return found

    def latest_clears(self, c: Conn, *, limit: int) -> dict[tuple[str, str], datetime]:
        rows = c.rows(
            "SELECT definition_id, scope_key, MAX(occurred_at) FROM rules_occurrence WHERE kind = 'clear' "
            "GROUP BY definition_id, scope_key LIMIT ?",
            (limit,),
            limit=limit,
        )
        return {(_s(r[0]), _s(r[1])): codec.parse_instant(r[2]) for r in rows}

    def record(
        self, c: Conn, *, definition_id: str, scope_key: str, trigger_ref: str, warning_id: str | None,
        kind: str, occurred_at: datetime, recorded_at: datetime,
    ) -> bool:  # fmt: skip
        """False for a duplicate: a repeated delivery changes nothing (3.9)."""
        result = c.execute(
            "INSERT OR IGNORE INTO rules_occurrence VALUES (?,?,?,?,?,?,?,NULL)",
            (definition_id, scope_key, trigger_ref, warning_id, kind, codec.instant(occurred_at),
             codec.instant(recorded_at)),
        )  # fmt: skip
        return result.affected_rows == 1

    def resolve_before(self, c: Conn, warning_id: str, at: datetime, clear_event_id: str) -> int:
        return c.execute(
            "UPDATE rules_occurrence SET resolved_by_event_id = ? WHERE warning_id = ? AND kind = 'trigger' "
            "AND resolved_by_event_id IS NULL AND occurred_at < ?",
            (clear_event_id, warning_id, codec.instant(at)),
        ).affected_rows


class DwellStore:
    def all(self, c: Conn, *, limit: int) -> dict[str, DwellMarker]:
        rows = c.rows(
            "SELECT warning_id, channel_id, rule_fingerprint, holding_since, last_window_end, segment_key "
            "FROM rules_dwell ORDER BY warning_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return {
            _s(r[0]): DwellMarker(_s(r[0]), _s(r[1]), _s(r[2]), codec.parse_instant(r[3]),
                                  codec.parse_instant(r[4]), _s(r[5]))
            for r in rows
        }  # fmt: skip

    def put(self, c: Conn, m: DwellMarker) -> None:
        c.execute("DELETE FROM rules_dwell WHERE warning_id = ?", (m.warning_id,))
        c.execute(
            "INSERT INTO rules_dwell VALUES (?,?,?,?,?,?)",
            (m.warning_id, m.channel_id, m.rule_fingerprint, m.segment_key, codec.instant(m.holding_since),
             codec.instant(m.last_window_end)),
        )  # fmt: skip

    def delete(self, c: Conn, warning_id: str) -> None:
        c.execute("DELETE FROM rules_dwell WHERE warning_id = ?", (warning_id,))

    def delete_for_channel(self, c: Conn, channel_id: str) -> None:
        c.execute("DELETE FROM rules_dwell WHERE channel_id = ?", (channel_id,))

    def delete_not_in(self, c: Conn, channels: Sequence[str]) -> int:
        """Startup (5.3): markers whose channel is not current are void."""
        total = 0
        for row in c.rows("SELECT DISTINCT channel_id FROM rules_dwell LIMIT 10000", (), limit=10000):
            if _s(row[0]) not in channels:
                total += c.execute(
                    "DELETE FROM rules_dwell WHERE channel_id = ?", (_s(row[0]),)
                ).affected_rows
        return total


class MissionEventStore:
    def add(self, c: Conn, e: MissionEventInput) -> None:
        c.execute(
            "INSERT OR IGNORE INTO rules_mission_event VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e.event_id, e.event_type, e.label, codec.instant(e.occurred_at),
             None if e.occurred_corrected_at is None else codec.instant(e.occurred_corrected_at),
             codec.instant(e.recorded_at), e.origin_mode.value, e.environment_id,
             json.dumps(list(e.person_ids)), e.location),
        )  # fmt: skip

    def latest(self, c: Conn, event_types: Sequence[str], *, per_type: int) -> tuple[MissionEventInput, ...]:
        found: list[MissionEventInput] = []
        for kind in sorted(set(event_types)):
            rows = c.rows(
                "SELECT event_id, event_type, label, occurred_at, occurred_corrected_at, recorded_at, origin_mode, "
                "environment_id, person_ids_json, location FROM rules_mission_event WHERE event_type = ? "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
                (kind, per_type),
                limit=per_type,
            )
            for r in rows:
                ids = json.loads(_s(r[8]))
                found.append(
                    MissionEventInput(
                        _s(r[0]), _s(r[1]), _s(r[2]), codec.parse_instant(r[3]),
                        None if r[4] is None else codec.parse_instant(r[4]), codec.parse_instant(r[5]),
                        OriginMode(_s(r[6])), _s(r[7]), tuple(str(x) for x in ids),
                        None if r[9] is None else _s(r[9]),
                    )
                )  # fmt: skip
        return tuple(found)


class DeadlineStore:
    def put(
        self,
        c: Conn,
        key: str,
        kind: str,
        due_at: datetime | None,
        elapsed: int | None,
        now: datetime,
        generation: int,
    ) -> None:
        c.execute("DELETE FROM rules_deadline WHERE deadline_key = ?", (key,))
        c.execute(
            "INSERT INTO rules_deadline VALUES (?,?,?,?,?,?)",
            (
                key,
                kind,
                None if due_at is None else codec.instant(due_at),
                elapsed,
                codec.instant(now),
                generation,
            ),
        )

    def delete(self, c: Conn, key: str) -> None:
        c.execute("DELETE FROM rules_deadline WHERE deadline_key = ?", (key,))

    def get(self, c: Conn, key: str) -> tuple[str, datetime | None, int | None, datetime, int] | None:
        rows = c.rows(
            "SELECT kind, due_at, elapsed_seconds, anchored_at, generation FROM rules_deadline WHERE deadline_key = ?",
            (key,),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        return (_s(r[0]), None if r[1] is None else codec.parse_instant(r[1]),
                None if r[2] is None else _i(r[2]), codec.parse_instant(r[3]), _i(r[4]))  # fmt: skip

    def page(self, c: Conn, *, after: str, limit: int) -> tuple[str, ...]:
        rows = c.rows(
            "SELECT deadline_key FROM rules_deadline WHERE deadline_key > ? ORDER BY deadline_key LIMIT ?",
            (after, limit),
            limit=limit,
        )
        return tuple(_s(r[0]) for r in rows)


class FactStore:
    def put(self, c: Conn, key: str, observed_at: datetime, body: dict[str, object]) -> bool:
        """False when an equal or newer observation is stored: an older delivery never overwrites (D3)."""
        rows = c.rows("SELECT observed_at FROM rules_fact WHERE fact_key = ?", (key,), limit=1)
        if rows and codec.parse_instant(rows[0][0]) >= observed_at:
            return False
        c.execute("DELETE FROM rules_fact WHERE fact_key = ?", (key,))
        c.execute(
            "INSERT INTO rules_fact VALUES (?,?,?)",
            (key, codec.instant(observed_at), json.dumps(body, sort_keys=True)),
        )
        return True

    def get(self, c: Conn, key: str) -> dict[str, object] | None:
        rows = c.rows("SELECT body_json FROM rules_fact WHERE fact_key = ?", (key,), limit=1)
        if not rows:
            return None
        loaded = json.loads(_s(rows[0][0]))
        return loaded if isinstance(loaded, dict) else None


class PendingStore:
    def count(self, c: Conn) -> int:
        return _i(c.rows("SELECT COUNT(*) FROM rules_pending", (), limit=1)[0][0])

    def any(self, c: Conn) -> bool:
        return bool(c.rows("SELECT 1 FROM rules_pending LIMIT 1", (), limit=1))

    def next_seq(self, c: Conn) -> int:
        return _i(c.rows("SELECT COALESCE(MAX(input_seq), 0) + 1 FROM rules_pending", (), limit=1)[0][0])

    def record(
        self, c: Conn, *, pending_id: str, kind: str, ref: str, coalesce_key: str | None, input_json: str,
        code: str, now: datetime, max_rows: int, state: str = "pending",
    ) -> None:  # fmt: skip
        if coalesce_key is not None:
            c.execute("DELETE FROM rules_pending WHERE coalesce_key = ?", (coalesce_key,))
        if self.count(c) >= max_rows:
            c.execute("DELETE FROM rules_pending WHERE state = 'overflow'")
            state, kind, ref, coalesce_key, input_json = "overflow", "overflow", "overflow", None, "{}"
        c.execute(
            "INSERT INTO rules_pending VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pending_id, kind, ref, coalesce_key, input_json, self.next_seq(c), codec.instant(now), 0, code,
             codec.instant(now), state),
        )  # fmt: skip

    def due(self, c: Conn, now: datetime, limit: int) -> tuple[tuple[str, str, str, str, int], ...]:
        rows = c.rows(
            "SELECT pending_id, input_kind, input_ref, input_json, attempts FROM rules_pending "
            "WHERE state IN ('pending','overflow') AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY input_seq LIMIT ?",
            (codec.instant(now), limit),
            limit=limit,
        )
        return tuple((_s(r[0]), _s(r[1]), _s(r[2]), _s(r[3]), _i(r[4])) for r in rows)

    def done(self, c: Conn, pending_id: str) -> None:
        c.execute("DELETE FROM rules_pending WHERE pending_id = ?", (pending_id,))

    def failed(self, c: Conn, pending_id: str, code: str, next_at: datetime | None, *, stuck: bool) -> None:
        c.execute(
            "UPDATE rules_pending SET attempts = attempts + 1, last_code = ?, next_attempt_at = ?, state = ? "
            "WHERE pending_id = ?",
            (
                code,
                None if next_at is None else codec.instant(next_at),
                "stuck" if stuck else "pending",
                pending_id,
            ),
        )

    def quarantine(self, c: Conn, pending_id: str) -> bool:
        return (
            c.execute(
                "UPDATE rules_pending SET state = 'quarantined' WHERE pending_id = ? AND state = 'stuck'",
                (pending_id,),
            ).affected_rows
            == 1
        )

    def blocking(self, c: Conn) -> bool:
        """Clears stay frozen while any row is not quarantined (5.5)."""
        return bool(c.rows("SELECT 1 FROM rules_pending WHERE state <> 'quarantined' LIMIT 1", (), limit=1))


class ActionResultStore:
    def get(self, c: Conn, actor_ref: str, key: str) -> tuple[str, str, datetime] | None:
        rows = c.rows(
            "SELECT warning_id, body_json, recorded_at FROM rules_action_result WHERE actor_ref = ? AND command_key = ?",
            (actor_ref, key),
            limit=1,
        )
        return (_s(rows[0][0]), _s(rows[0][1]), codec.parse_instant(rows[0][2])) if rows else None

    def put(self, c: Conn, actor_ref: str, key: str, warning_id: str, body: str, at: datetime) -> None:
        c.execute(
            "INSERT INTO rules_action_result VALUES (?,?,?,?,?)",
            (actor_ref, key, warning_id, body, codec.instant(at)),
        )


class ReplicaStore:
    def apply(
        self, c: Conn, *, warning_id: str, origin_node_id: str, origin_hlc: str, revision: int, scope: str,
        person_id: str | None, warning_json: str, now: datetime,
    ) -> bool:  # fmt: skip
        """Append only by revision: an older or duplicate version changes nothing (INV-18)."""
        rows = c.rows(
            "SELECT revision FROM rules_warning_replica WHERE warning_id = ?", (warning_id,), limit=1
        )
        if rows and _i(rows[0][0]) >= revision:
            return False
        c.execute("DELETE FROM rules_warning_replica WHERE warning_id = ?", (warning_id,))
        c.execute(
            "INSERT INTO rules_warning_replica VALUES (?,?,?,?,?,?,?,?)",
            (
                warning_id,
                origin_node_id,
                origin_hlc,
                revision,
                scope,
                person_id,
                warning_json,
                codec.instant(now),
            ),
        )
        return True

    def page(self, c: Conn, *, limit: int) -> tuple[tuple[str, str, str, str | None, str], ...]:
        rows = c.rows(
            "SELECT warning_id, origin_node_id, origin_hlc, person_id, warning_json FROM rules_warning_replica "
            "ORDER BY warning_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(
            (_s(r[0]), _s(r[1]), _s(r[2]), None if r[3] is None else _s(r[3]), _s(r[4])) for r in rows
        )


def table_declarations() -> tuple[tuple[str, str, str], ...]:
    """(table, record_type, key column) for E01's replicated-table verification (S5.7)."""
    return (("rules_warning", "warning", "warning_id"),)
