from datetime import datetime
from typing import Literal

from crew_health.clock.domain.corrections import ClockState, instant
from crew_health.clock.domain.errors import ClockHistoryConflict, InvalidClockValue, MissingMissionClock
from crew_health.clock.domain.met import validate_version
from crew_health.kernel.clock import ClockCorrection, ClockSegment, Hlc, require_utc
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StorageSession


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StorageUnavailable("corrupt")
    return value


def _integer(value: SqlValue) -> int:
    if type(value) is not int:
        raise StorageUnavailable("corrupt")
    return value


def _date(value: SqlValue) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value))
        require_utc(result)
        return result
    except ValueError as error:
        raise StorageUnavailable("corrupt") from error


def _optional_date(value: SqlValue) -> datetime | None:
    return None if value is None else _date(value)


def _segment(row: tuple[SqlValue, ...]) -> ClockSegment:
    if len(row) != 6:
        raise StorageUnavailable("corrupt")
    return ClockSegment(
        _text(row[0]),
        _text(row[1]),
        _text(row[2]),
        None if row[3] is None else _text(row[3]),
        _integer(row[4]),
        _integer(row[5]),
    )


def _correction(row: tuple[SqlValue, ...]) -> ClockCorrection:
    if len(row) != 10:
        raise StorageUnavailable("corrupt")
    source: Literal["operator", "measured", "ground"]
    if row[9] == "operator":
        source = "operator"
    elif row[9] == "measured":
        source = "measured"
    elif row[9] == "ground":
        source = "ground"
    else:
        raise StorageUnavailable("corrupt")
    return ClockCorrection(
        _text(row[0]),
        _text(row[1]),
        _text(row[2]),
        _integer(row[3]),
        _date(row[4]),
        _optional_date(row[5]),
        _integer(row[6]),
        _integer(row[7]),
        _date(row[8]),
        source,
    )


def _mission(row: tuple[SqlValue, ...]) -> MissionClockVersion:
    if len(row) != 6:
        raise StorageUnavailable("corrupt")
    value = MissionClockVersion(
        _text(row[0]), _integer(row[1]), _date(row[2]), _date(row[3]), _optional_date(row[4]), _text(row[5])
    )
    validate_version(value)
    return value


class SqlClockRepository:
    def __init__(self, *, settings: HubSettings, node_id: str, incarnation_id: str) -> None:
        if not node_id or not incarnation_id:
            raise InvalidClockValue()
        self.settings, self.node_id, self.incarnation_id = settings, node_id, incarnation_id

    def segment(self, connection: StorageSession, source: str, segment: str) -> ClockSegment | None:
        rows = connection.rows(
            "SELECT source_clock_id,physical_clock_id,segment_id,predecessor_id,first_sequence,capture_offset_version FROM clock_segment WHERE source_clock_id=? AND segment_id=?",
            (source, segment),
            limit=1,
        )
        return _segment(rows[0]) if rows else None

    def latest_segment(self, connection: StorageSession, source: str) -> ClockSegment | None:
        rows = connection.rows(
            "SELECT source_clock_id,physical_clock_id,segment_id,predecessor_id,first_sequence,capture_offset_version FROM clock_segment WHERE source_clock_id=? ORDER BY first_sequence DESC LIMIT 1",
            (source,),
            limit=1,
        )
        return _segment(rows[0]) if rows else None

    def for_sequence(
        self, connection: StorageSession, *, source_clock_id: str, sequence: int
    ) -> ClockSegment | None:
        if not source_clock_id or type(sequence) is not int or sequence < 0:
            raise InvalidClockValue()
        rows = connection.rows(
            "SELECT source_clock_id,physical_clock_id,segment_id,predecessor_id,first_sequence,capture_offset_version FROM clock_segment WHERE source_clock_id=? AND first_sequence<=? ORDER BY first_sequence DESC LIMIT 1",
            (source_clock_id, sequence),
            limit=1,
        )
        return _segment(rows[0]) if rows else None

    def append_segment(self, connection: StorageSession, value: ClockSegment) -> bool:
        if (
            not value.source_clock_id
            or not value.segment_id
            or not value.physical_clock_id
            or type(value.first_sequence) is not int
            or not 0 <= value.first_sequence <= 9223372036854775807
            or type(value.capture_offset_version) is not int
            or value.capture_offset_version < 0
        ):
            raise InvalidClockValue()
        existing = self.segment(connection, value.source_clock_id, value.segment_id)
        if existing is not None:
            if existing != value:
                raise ClockHistoryConflict()
            return False
        previous = self.latest_segment(connection, value.source_clock_id)
        if previous is None and value.predecessor_id is not None:
            raise InvalidClockValue()
        if previous is not None and (
            value.predecessor_id != previous.segment_id or value.first_sequence <= previous.first_sequence
        ):
            raise InvalidClockValue()
        result = connection.execute(
            "INSERT INTO clock_segment(source_clock_id,physical_clock_id,segment_id,predecessor_id,first_sequence,capture_offset_version) VALUES(?,?,?,?,?,?)",
            (
                value.source_clock_id,
                value.physical_clock_id,
                value.segment_id,
                value.predecessor_id,
                value.first_sequence,
                value.capture_offset_version,
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")
        return True

    def correction(
        self, connection: StorageSession, *, source: str, segment: str, version: int
    ) -> ClockCorrection | None:
        rows = connection.rows(
            "SELECT correction_id,source_clock_id,segment_id,version,valid_from,valid_until,offset_ms,uncertainty_ms,recorded_at,source FROM clock_correction WHERE source_clock_id=? AND segment_id=? AND version=?",
            (source, segment, version),
            limit=1,
        )
        return _correction(rows[0]) if rows else None

    def selected_correction(
        self, connection: StorageSession, *, source: str, segment: str, raw: datetime
    ) -> ClockCorrection | None:
        rows = connection.rows(
            "SELECT correction_id,source_clock_id,segment_id,version,valid_from,valid_until,offset_ms,uncertainty_ms,recorded_at,source FROM clock_correction WHERE source_clock_id=? AND segment_id=? AND valid_from<=? AND (valid_until IS NULL OR ?<valid_until) ORDER BY version DESC LIMIT 1",
            (source, segment, instant(raw), instant(raw)),
            limit=1,
        )
        return _correction(rows[0]) if rows else None

    def previous_offset(self, connection: StorageSession, value: ClockCorrection) -> int:
        rows = connection.rows(
            "SELECT offset_ms FROM clock_correction WHERE source_clock_id=? AND segment_id=? ORDER BY version DESC LIMIT 1",
            (value.source_clock_id, value.segment_id),
            limit=1,
        )
        return _integer(rows[0][0]) if rows else 0

    def append_correction(self, connection: StorageSession, value: ClockCorrection) -> None:
        head = connection.rows(
            "SELECT version FROM clock_correction ORDER BY version DESC LIMIT 1", (), limit=1
        )
        if head and value.version <= _integer(head[0][0]):
            raise ClockHistoryConflict()
        if self.segment(connection, value.source_clock_id, value.segment_id) is None:
            raise InvalidClockValue()
        result = connection.execute(
            "INSERT INTO clock_correction(correction_id,source_clock_id,segment_id,version,valid_from,valid_until,offset_ms,uncertainty_ms,recorded_at,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                value.correction_id,
                value.source_clock_id,
                value.segment_id,
                value.version,
                instant(value.valid_from),
                None if value.valid_until is None else instant(value.valid_until),
                value.offset_ms,
                value.uncertainty_ms,
                instant(value.recorded_at),
                value.source,
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    def history(
        self, connection: StorageSession, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]:
        if (
            type(after_version) is not int
            or after_version < 0
            or type(limit) is not int
            or not 1 <= limit <= self.settings.integer("clock.history_page_size")
        ):
            raise InvalidClockValue()
        rows = connection.rows(
            "SELECT correction_id,source_clock_id,segment_id,version,valid_from,valid_until,offset_ms,uncertainty_ms,recorded_at,source FROM clock_correction WHERE version>? ORDER BY version LIMIT ?",
            (after_version, limit),
            limit=limit,
        )
        return tuple(_correction(row) for row in rows)

    @staticmethod
    def hub_capture_version(connection: StorageSession, segment: ClockSegment) -> int:
        rows = connection.rows(
            "SELECT version FROM clock_hub_capture WHERE source_clock_id=? AND segment_id=?",
            (segment.source_clock_id, segment.segment_id),
            limit=1,
        )
        return _integer(rows[0][0]) if rows else 0

    def set_hub_capture(self, connection: StorageSession, correction: ClockCorrection) -> None:
        accepted = self.correction(
            connection,
            source=correction.source_clock_id,
            segment=correction.segment_id,
            version=correction.version,
        )
        if accepted != correction or correction.source != "operator":
            raise InvalidClockValue()
        result = connection.execute(
            "INSERT INTO clock_hub_capture(source_clock_id,segment_id,version) VALUES(?,?,?) "
            "ON CONFLICT(source_clock_id,segment_id) DO UPDATE SET version=excluded.version",
            (correction.source_clock_id, correction.segment_id, correction.version),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    @staticmethod
    def state(connection: StorageSession) -> ClockState | None:
        rows = connection.rows(
            "SELECT last_committed_at,frozen,correction_version,minimum_revision FROM clock_state WHERE singleton=1",
            (),
            limit=1,
        )
        if not rows:
            return None
        return ClockState(
            _date(rows[0][0]), _integer(rows[0][1]) == 1, _integer(rows[0][2]), _integer(rows[0][3])
        )

    @staticmethod
    def set_state(connection: StorageSession, value: ClockState) -> None:
        result = connection.execute(
            "INSERT INTO clock_state VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET last_committed_at=excluded.last_committed_at,frozen=excluded.frozen,correction_version=excluded.correction_version,minimum_revision=excluded.minimum_revision",
            (
                instant(value.last_committed_at),
                int(value.frozen),
                value.correction_version,
                value.minimum_revision,
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    @staticmethod
    def correction_head(connection: StorageSession) -> int:
        rows = connection.rows(
            "SELECT version FROM clock_correction ORDER BY version DESC LIMIT 1", (), limit=1
        )
        return _integer(rows[0][0]) if rows else 0

    def hlc(self, connection: StorageSession) -> Hlc | None:
        rows = connection.rows(
            "SELECT physical_ms,logical FROM clock_hlc WHERE node_id=? ORDER BY physical_ms DESC,logical DESC LIMIT 1",
            (self.node_id,),
            limit=1,
        )
        return (
            Hlc(_integer(rows[0][0]), _integer(rows[0][1]), self.node_id, self.incarnation_id)
            if rows
            else None
        )

    def store_hlc(self, connection: StorageSession, value: Hlc) -> None:
        result = connection.execute(
            "INSERT INTO clock_hlc(node_id,incarnation_id,physical_ms,logical) VALUES(?,?,?,?) ON CONFLICT(node_id,incarnation_id) DO UPDATE SET physical_ms=excluded.physical_ms,logical=excluded.logical",
            (value.node_id, value.incarnation_id, value.physical_ms, value.logical),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    @staticmethod
    def mission_version(
        connection: StorageSession, *, at: datetime, version: str | None = None
    ) -> MissionClockVersion:
        if version is None:
            rows = connection.rows(
                "SELECT clock_version,generation,epoch,effective_from,effective_until,day_label_template FROM clock_mission_version WHERE effective_from<=? AND (effective_until IS NULL OR ?<effective_until) ORDER BY generation DESC LIMIT 1",
                (instant(at), instant(at)),
                limit=1,
            )
        else:
            rows = connection.rows(
                "SELECT clock_version,generation,epoch,effective_from,effective_until,day_label_template FROM clock_mission_version WHERE clock_version=?",
                (version,),
                limit=1,
            )
        if not rows:
            raise MissingMissionClock()
        return _mission(rows[0])

    @staticmethod
    def append_mission_version(connection: StorageSession, value: MissionClockVersion) -> None:
        validate_version(value)
        rows = connection.rows(
            "SELECT generation FROM clock_mission_version ORDER BY generation DESC LIMIT 1", (), limit=1
        )
        if rows and value.generation <= _integer(rows[0][0]):
            raise ClockHistoryConflict()
        result = connection.execute(
            "INSERT INTO clock_mission_version(clock_version,generation,epoch,effective_from,effective_until,day_label_template) VALUES(?,?,?,?,?,?)",
            (
                value.clock_version,
                value.generation,
                instant(value.epoch),
                instant(value.effective_from),
                None if value.effective_until is None else instant(value.effective_until),
                value.day_label_template,
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")
