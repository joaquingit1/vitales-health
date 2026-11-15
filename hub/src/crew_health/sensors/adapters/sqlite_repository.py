"""SQLite implementation of the sensor store (E23 5.1) over the unit of work's StorageSession.

Accepted readings are append only (a trigger refuses UPDATE and DELETE) and unique per channel and adapter
sequence. Every write declares its replication to E01 as a safe reference, so the unit of work's verification of
undeclared changes holds; the reading is the one record that may carry an export projection (`sensors/reading/1`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.replication import ProjectionKey
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.sensors.application.store import (
    BatchRow,
    ChannelState,
    EnvironmentRow,
    EvaluationInputRow,
    EvaluationRow,
    ReadingRecord,
    RegistryChannel,
    ScheduleFact,
    StatusRow,
    View,
)
from crew_health.sensors.domain.channel import ChannelFacts, Origin
from crew_health.sensors.domain.errors import SensorDomainError
from crew_health.sensors.domain.freshness import TimeFacts

READING_PROJECTION = ProjectionKey("sensors", "reading", 1)
LOCAL_AUDIENCE = "sensors.local"
_CHANNEL_COLUMNS = (
    "channel_id, sensor_type_id, hazard_id, environment_ids, location, location_label, adapter_id, origin_mode, "
    "input_unit, expected_interval_seconds, stale_after_seconds, source_label, dataset, display_note, trusted, "
    "health_url, registry_version, freshness, has_history, last_received_at, last_observed_at, window_end, "
    "reference_at, reference_state, reference_corrected_at, reference_uncertainty_ms, quiet_anchor_at, "
    "quiet_anchor_environment, stale_since, next_stale_check_at, clock_state"
)
_READING_COLUMNS = (
    "reading_id, channel_id, adapter_id, adapter_sequence, batch_id, value_text, unit, canonical_text, "
    "original_value, original_unit, conversion, mapping_version, aggregation_kind, aggregation_window, "
    "observed_at, window_start, received_at, origin_mode, quality_status, time_state, corrected_at, "
    "uncertainty_ms, segment_id, source_clock_version, replay, provenance_trace, instant_conflict, "
    "body_sha256, capture_release_id, rev"
)


class StoredSensorError(SensorDomainError):
    """A stored value that breaks its own schema: an integrity fault, never repaired by a default."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("INTEGRITY_VIOLATION", detail)


def stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _opt(value: datetime | None) -> str | None:
    return None if value is None else stamp(value)


def _when(value: SqlValue) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StoredSensorError("instant is not text")
    return datetime.fromisoformat(value)


def _need_when(value: SqlValue) -> datetime:
    found = _when(value)
    if found is None:
        raise StoredSensorError("required instant is null")
    return found


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StoredSensorError("value is not text")
    return value


def _opt_text(value: SqlValue) -> str | None:
    return None if value is None else _text(value)


def _int(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise StoredSensorError("value is not an integer")
    return value


def _opt_int(value: SqlValue) -> int | None:
    return None if value is None else _int(value)


def _origin(value: SqlValue) -> Origin:
    text = _text(value)
    if text not in ("LIVE", "REPLAY", "SIM"):
        raise StoredSensorError("origin")
    origin: Origin = text  # narrowed above
    return origin


def declare(
    uow: UnitOfWork,
    record_type: str,
    record_id: str,
    operation: Literal["create", "version", "append"],
    *,
    export: ProjectionKey | None = None,
    payload: bytes | None = None,
) -> None:
    """Local accounting by default: a safe reference, never a field, a note or a person (S5.7, S8.3)."""
    body = payload if payload is not None else json.dumps({"id": record_id}, sort_keys=True).encode()
    uow.replicate(
        ReplicationChange(
            export,
            record_type,
            record_id,
            operation,
            body,
            (),
            uow.release.release_id,
            Audience(
                LOCAL_AUDIENCE,
                record_id,
                AudienceProjectionKey("sensors", record_type, 1),
                "1",
            ),
        )
    )


def _channel(row: tuple[SqlValue, ...]) -> ChannelState:
    environments = json.loads(_text(row[3]))
    expected = _opt_int(row[9])
    facts = ChannelFacts(
        _text(row[0]),
        _text(row[1]),
        _text(row[2]),
        _text(row[6]),
        _origin(row[7]),
        _opt_text(row[5]),
        _text(row[11]),
        _opt_text(row[12]),
        _opt_text(row[13]),
        expected,
        _int(row[10]),
        tuple(environments),
    )
    state = _opt_text(row[23])
    reference = (
        None
        if state is None
        else TimeFacts(state, _when(row[24]), _opt_int(row[25]))  # type: ignore[arg-type]
    )
    freshness = _text(row[17])
    clock = _text(row[30])
    if freshness not in ("current", "stale", "never_reported") or clock not in ("trusted", "uncertain", "unknown"):
        raise StoredSensorError("channel enum")
    return ChannelState(
        facts,
        _text(row[4]),
        _text(row[8]),
        _int(row[14]) == 1,
        _opt_text(row[15]),
        _text(row[16]),
        freshness,
        _int(row[18]) == 1,
        _when(row[19]),
        _when(row[20]),
        _when(row[21]),
        _when(row[22]),
        reference,
        _when(row[26]),
        _opt_text(row[27]),
        _when(row[28]),
        _when(row[29]),
        clock,
    )


def _reading(row: tuple[SqlValue, ...]) -> ReadingRecord:
    quality = _text(row[18])
    time_state = _text(row[19])
    if quality not in ("accepted", "rejected", "uncertain", "not_assessed") or time_state not in (
        "known",
        "replay",
        "unknown",
        "future",
    ):
        raise StoredSensorError("reading enum")
    return ReadingRecord(
        _text(row[0]),
        _text(row[1]),
        _text(row[2]),
        _int(row[3]),
        _text(row[4]),
        Decimal(_text(row[5])),
        _text(row[6]),
        Decimal(_text(row[7])),
        Decimal(_text(row[8])),
        _text(row[9]),
        _opt_text(row[10]),
        _text(row[11]),
        _text(row[12]),
        _opt_int(row[13]),
        _need_when(row[14]),
        _when(row[15]),
        _need_when(row[16]),
        _origin(row[17]),
        quality,  # type: ignore[arg-type]
        time_state,  # type: ignore[arg-type]
        _when(row[20]),
        _opt_int(row[21]),
        _opt_text(row[22]),
        _opt_int(row[23]),
        _opt_text(row[24]),
        _opt_text(row[25]),
        _int(row[26]) == 1,
        _text(row[27]),
        _text(row[28]),
        _int(row[29]),
    )


class SqliteSensorStore:
    """SensorStore over E01's StorageSession. `export_readings` turns on the `sensors/reading/1` projection."""

    def __init__(self, *, row_limit: int, page_rows: int, export_readings: bool = False) -> None:
        if not 1 <= row_limit <= 1000 or not 1 <= page_rows <= 1000:
            raise ValueError("sensor row limits outside storage.max_rows")
        self._limit = row_limit
        self._page = page_rows
        self._export = READING_PROJECTION if export_readings else None

    # --- channels -----------------------------------------------------------------------------------------

    def channels(self, view: View) -> Sequence[ChannelState]:
        rows = view.connection.rows(
            f"SELECT {_CHANNEL_COLUMNS} FROM sensors_channel ORDER BY channel_id", (), limit=self._limit
        )
        return tuple(_channel(r) for r in rows)

    def channel(self, view: View, channel_id: str) -> ChannelState | None:
        rows = view.connection.rows(
            f"SELECT {_CHANNEL_COLUMNS} FROM sensors_channel WHERE channel_id = ?", (channel_id,), limit=1
        )
        return _channel(rows[0]) if rows else None

    def register_channel(self, uow: UnitOfWork, channel: RegistryChannel) -> None:
        f = channel.facts
        existing = self.channel(uow, f.channel_id)
        static: tuple[SqlValue, ...] = (
            f.sensor_type_id,
            f.hazard_id,
            json.dumps(list(f.environment_ids)),
            channel.location,
            f.location_label,
            f.adapter_id,
            f.origin_mode,
            channel.input_unit,
            f.expected_interval_seconds,
            f.stale_after_seconds,
            f.source_label,
            f.dataset,
            f.display_note,
            1 if channel.trusted else 0,
            channel.health_url,
            channel.registry_version,
        )
        if existing is None:
            uow.connection.execute(
                "INSERT INTO sensors_channel (channel_id, sensor_type_id, hazard_id, environment_ids, location, "
                "location_label, adapter_id, origin_mode, input_unit, expected_interval_seconds, "
                "stale_after_seconds, source_label, dataset, display_note, trusted, health_url, registry_version, "
                "freshness) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'never_reported')",
                (f.channel_id, *static),
            )
            declare(uow, "sensors_channel", f.channel_id, "create")
            return
        if existing.facts.origin_mode != f.origin_mode or existing.facts.dataset != f.dataset:
            raise StoredSensorError("a channel id is never reused for another dataset or origin")
        uow.connection.execute(
            "UPDATE sensors_channel SET sensor_type_id = ?, hazard_id = ?, environment_ids = ?, location = ?, "
            "location_label = ?, adapter_id = ?, origin_mode = ?, input_unit = ?, expected_interval_seconds = ?, "
            "stale_after_seconds = ?, source_label = ?, dataset = ?, display_note = ?, trusted = ?, "
            "health_url = ?, registry_version = ? WHERE channel_id = ?",
            (*static, f.channel_id),
        )
        declare(uow, "sensors_channel", f.channel_id, "version")

    def save_channel(self, uow: UnitOfWork, channel: ChannelState) -> None:
        ref = channel.reference
        uow.connection.execute(
            "UPDATE sensors_channel SET freshness = ?, has_history = ?, last_received_at = ?, "
            "last_observed_at = ?, window_end = ?, reference_at = ?, reference_state = ?, "
            "reference_corrected_at = ?, reference_uncertainty_ms = ?, quiet_anchor_at = ?, "
            "quiet_anchor_environment = ?, stale_since = ?, next_stale_check_at = ?, clock_state = ? "
            "WHERE channel_id = ?",
            (
                channel.freshness,
                1 if channel.has_history else 0,
                _opt(channel.last_received_at),
                _opt(channel.last_observed_at),
                _opt(channel.window_end),
                _opt(channel.reference_at),
                None if ref is None else ref.state,
                None if ref is None else _opt(ref.corrected_at),
                None if ref is None else ref.uncertainty_ms,
                _opt(channel.quiet_anchor_at),
                channel.quiet_anchor_environment,
                _opt(channel.stale_since),
                _opt(channel.next_stale_check_at),
                channel.clock_state,
                channel.facts.channel_id,
            ),
        )
        declare(uow, "sensors_channel", channel.facts.channel_id, "version")

    # --- readings -----------------------------------------------------------------------------------------

    def reading_at(self, view: View, channel_id: str, sequence: int) -> ReadingRecord | None:
        rows = view.connection.rows(
            f"SELECT {_READING_COLUMNS} FROM sensors_reading WHERE channel_id = ? AND adapter_sequence = ?",
            (channel_id, sequence),
            limit=1,
        )
        return _reading(rows[0]) if rows else None

    def reading(self, view: View, reading_id: str) -> ReadingRecord | None:
        rows = view.connection.rows(
            f"SELECT {_READING_COLUMNS} FROM sensors_reading WHERE reading_id = ?", (reading_id,), limit=1
        )
        return _reading(rows[0]) if rows else None

    def insert_reading(self, uow: UnitOfWork, reading: ReadingRecord) -> int:
        done = uow.connection.execute(
            "INSERT INTO sensors_reading (reading_id, channel_id, adapter_id, adapter_sequence, batch_id, "
            "value_text, unit, canonical_text, original_value, original_unit, conversion, mapping_version, "
            "aggregation_kind, aggregation_window, observed_at, window_start, received_at, origin_mode, "
            "quality_status, time_state, corrected_at, uncertainty_ms, segment_id, source_clock_version, replay, "
            "provenance_trace, instant_conflict, body_sha256, capture_release_id) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reading.reading_id,
                reading.channel_id,
                reading.adapter_id,
                reading.adapter_sequence,
                reading.batch_id,
                str(reading.value),
                reading.unit,
                str(reading.canonical),
                str(reading.original_value),
                reading.original_unit,
                reading.conversion_json,
                reading.mapping_version,
                reading.aggregation_kind,
                reading.aggregation_window,
                stamp(reading.observed_at),
                _opt(reading.window_start),
                stamp(reading.received_at),
                reading.origin_mode,
                reading.quality_status,
                reading.time_state,
                _opt(reading.corrected_at),
                reading.uncertainty_ms,
                reading.segment_id,
                reading.source_clock_version,
                reading.replay_json,
                reading.provenance_trace,
                1 if reading.instant_conflict else 0,
                reading.body_sha256,
                reading.capture_release_id,
            ),
        )
        if done.last_row_id is None:
            raise StoredSensorError("no reading revision")
        declare(uow, "sensors_reading", reading.reading_id, "append", export=self._export)
        return done.last_row_id

    def readings_since(
        self, view: View, channel_id: str, *, since: datetime | None, up_to_rev: int | None, limit: int
    ) -> Sequence[ReadingRecord]:
        """Ascending by observed time then revision, at most `limit + 1` rows (one more means over the bound).

        Read in pages of `storage.max_rows` by keyset, so a window of many thousands of rows never needs one big
        read and never skips or repeats a row.
        """
        clauses = ["channel_id = ?", "(observed_at > ? OR (observed_at = ? AND rev > ?))"]
        if up_to_rev is not None:
            clauses.append("rev <= ?")
        sql = (
            f"SELECT {_READING_COLUMNS} FROM sensors_reading WHERE {' AND '.join(clauses)} "
            "ORDER BY observed_at, rev LIMIT ?"
        )
        floor = ("" if since is None else stamp(since), 0)
        found: list[ReadingRecord] = []
        while len(found) <= limit:
            want = min(self._page, limit + 1 - len(found))
            parameters: list[SqlValue] = [channel_id, floor[0], floor[0], floor[1]]
            if up_to_rev is not None:
                parameters.append(up_to_rev)
            parameters.append(want)
            page = [_reading(r) for r in view.connection.rows(sql, tuple(parameters), limit=want)]
            found.extend(page)
            if len(page) < want:
                break
            last = page[-1]
            floor = (stamp(last.observed_at), last.rev or 0)
        return tuple(found)

    def instant_peers(
        self, view: View, channel_id: str, corrected_at: datetime, origin: Origin, dataset: str | None
    ) -> Sequence[ReadingRecord]:
        rows = view.connection.rows(
            f"SELECT {_READING_COLUMNS} FROM sensors_reading WHERE channel_id = ? AND corrected_at = ? "
            "AND origin_mode = ? AND quality_status != 'rejected' ORDER BY rev",
            (channel_id, stamp(corrected_at), origin),
            limit=self._limit,
        )
        del dataset  # a channel id is never reused for another dataset, so the channel already fixes it
        return tuple(_reading(r) for r in rows)

    # --- evaluation ---------------------------------------------------------------------------------------

    def save_input(self, uow: UnitOfWork, row: EvaluationInputRow) -> None:
        uow.connection.execute(
            "INSERT INTO sensors_evaluation_input VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.input_ref,
                row.channel_id,
                row.profile_id,
                row.mission_id,
                row.origin_mode,
                row.dataset,
                row.partition_ref,
                row.high_rev,
                row.capture_release_id,
                row.evaluator_version,
                row.observed_json,
                _opt(row.window_end),
                row.correction_map_version,
            ),
        )
        declare(uow, "sensors_evaluation_input", row.input_ref, "append")

    def input(self, view: View, input_ref: str) -> EvaluationInputRow | None:
        rows = view.connection.rows(
            "SELECT input_ref, channel_id, profile_id, mission_id, origin_mode, dataset, partition_ref, high_rev, "
            "capture_release_id, evaluator_version, observed_json, window_end, correction_map_version "
            "FROM sensors_evaluation_input WHERE input_ref = ?",
            (input_ref,),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        return EvaluationInputRow(
            _text(r[0]),
            _text(r[1]),
            _text(r[2]),
            _text(r[3]),
            _origin(r[4]),
            _opt_text(r[5]),
            _text(r[6]),
            _int(r[7]),
            _text(r[8]),
            _text(r[9]),
            _text(r[10]),
            _when(r[11]),
            _int(r[12]),
        )

    def evaluation(self, view: View, channel_id: str, profile_id: str, release_id: str) -> EvaluationRow | None:
        rows = view.connection.rows(
            "SELECT evaluation_id, channel_id, profile_id, config_release_id, input_rev, high_water_at, status, "
            "evaluation_json FROM sensors_evaluation WHERE channel_id = ? AND profile_id = ? "
            "AND config_release_id = ?",
            (channel_id, profile_id, release_id),
            limit=1,
        )
        return _evaluation(rows[0]) if rows else None

    def evaluations_of(self, view: View, channel_id: str) -> Sequence[EvaluationRow]:
        rows = view.connection.rows(
            "SELECT evaluation_id, channel_id, profile_id, config_release_id, input_rev, high_water_at, status, "
            "evaluation_json FROM sensors_evaluation WHERE channel_id = ? ORDER BY evaluation_id",
            (channel_id,),
            limit=self._limit,
        )
        return tuple(_evaluation(r) for r in rows)

    def save_evaluation(self, uow: UnitOfWork, row: EvaluationRow) -> None:
        """Insert, or replace with a not older input revision. An older input never overwrites a newer state.

        No UPSERT: E01's replicated-table triggers use INSERT OR IGNORE, which an outer ON CONFLICT overrides."""
        held = uow.connection.rows(
            "SELECT input_rev FROM sensors_evaluation WHERE evaluation_id = ?", (row.evaluation_id,), limit=1
        )
        if not held:
            uow.connection.execute(
                "INSERT INTO sensors_evaluation VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row.evaluation_id, row.channel_id, row.profile_id, row.config_release_id, row.input_rev,
                 _opt(row.high_water_at), row.status, row.evaluation_json),
            )
        elif _int(held[0][0]) <= row.input_rev:
            uow.connection.execute(
                "UPDATE sensors_evaluation SET input_rev = ?, high_water_at = ?, status = ?, evaluation_json = ? "
                "WHERE evaluation_id = ?",
                (row.input_rev, _opt(row.high_water_at), row.status, row.evaluation_json, row.evaluation_id),
            )
        else:
            return
        declare(uow, "sensors_evaluation", row.evaluation_id, "version")

    # --- status, batches, environment ---------------------------------------------------------------------

    def status(self, view: View, sensor_type_id: str, environment_id: str) -> StatusRow | None:
        rows = view.connection.rows(
            "SELECT sensor_type_id, environment_id, revision, last_emitted_at, pending_emit_at FROM sensors_status "
            "WHERE status_key = ?",
            (sensor_type_id + ":" + environment_id,),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        return StatusRow(_text(r[0]), _text(r[1]), _int(r[2]), _when(r[3]), _when(r[4]))

    def save_status(self, uow: UnitOfWork, row: StatusRow) -> None:
        key = row.sensor_type_id + ":" + row.environment_id
        held = uow.connection.rows("SELECT 1 FROM sensors_status WHERE status_key = ?", (key,), limit=1)
        values: tuple[SqlValue, ...] = (row.revision, _opt(row.last_emitted_at), _opt(row.pending_emit_at))
        if held:
            uow.connection.execute(
                "UPDATE sensors_status SET revision = ?, last_emitted_at = ?, pending_emit_at = ? WHERE status_key = ?",
                (*values, key),
            )
        else:
            uow.connection.execute(
                "INSERT INTO sensors_status VALUES (?, ?, ?, ?, ?, ?)",
                (key, row.sensor_type_id, row.environment_id, *values),
            )
        declare(uow, "sensors_status", key, "version")

    def save_batch(self, uow: UnitOfWork, row: BatchRow) -> None:
        key = row.adapter_id + ":" + row.batch_id
        if uow.connection.rows("SELECT 1 FROM sensors_batch WHERE batch_key = ?", (key,), limit=1):
            return  # the first record of a batch id is the one kept
        uow.connection.execute(
            "INSERT INTO sensors_batch VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, row.adapter_id, row.batch_id, row.accepted, row.duplicates, row.rejected_json, stamp(row.received_at)),
        )
        declare(uow, "sensors_batch", key, "create")

    def environment(self, view: View) -> EnvironmentRow | None:
        rows = view.connection.rows(
            "SELECT profile_id, environment_id, mission_mode, activated_at FROM sensors_environment "
            "WHERE environment_key = 'current'",
            (),
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        return EnvironmentRow(_text(r[0]), _text(r[1]), _origin(r[2]), _need_when(r[3]))

    def schedule_of(self, view: View, key: str) -> ScheduleFact | None:
        return _schedule(view, key)

    def save_environment(self, uow: UnitOfWork, row: EnvironmentRow) -> None:
        held = uow.connection.rows("SELECT 1 FROM sensors_environment WHERE environment_key = 'current'", (), limit=1)
        values: tuple[SqlValue, ...] = (row.profile_id, row.environment_id, row.mission_mode, stamp(row.activated_at))
        if held:
            uow.connection.execute(
                "UPDATE sensors_environment SET profile_id = ?, environment_id = ?, mission_mode = ?, "
                "activated_at = ? WHERE environment_key = 'current'",
                values,
            )
        else:
            uow.connection.execute("INSERT INTO sensors_environment VALUES ('current', ?, ?, ?, ?)", values)
        declare(uow, "sensors_environment", "current", "version")


def _schedule(view: View, key: str) -> ScheduleFact | None:
    """E01's schedule row for a key (platform_schedule): the only way to see a job's generation and due time."""
    rows = view.connection.rows(
        "SELECT generation, due, completed FROM platform_schedule WHERE key = ?", (key,), limit=1
    )
    if not rows:
        return None
    return ScheduleFact(_int(rows[0][0]), _when(rows[0][1]), _int(rows[0][2]) == 1)


def _evaluation(r: tuple[SqlValue, ...]) -> EvaluationRow:
    return EvaluationRow(
        _text(r[0]), _text(r[1]), _text(r[2]), _text(r[3]), _int(r[4]), _when(r[5]), _text(r[6]), _text(r[7])
    )


def table_declarations() -> tuple[tuple[str, str, str], ...]:
    """(table, record type, key column) for E01's replicated-table verification (S5.7)."""
    return (
        ("sensors_channel", "sensors_channel", "channel_id"),
        ("sensors_reading", "sensors_reading", "reading_id"),
        ("sensors_evaluation", "sensors_evaluation", "evaluation_id"),
        ("sensors_evaluation_input", "sensors_evaluation_input", "input_ref"),
        ("sensors_status", "sensors_status", "status_key"),
        ("sensors_batch", "sensors_batch", "batch_key"),
        ("sensors_environment", "sensors_environment", "environment_key"),
    )
