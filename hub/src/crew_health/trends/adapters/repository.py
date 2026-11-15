"""SQLite storage for E14 inputs and validated worker outcomes only."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import TypeAdapter, ValidationError

from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.units import Unit
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.trends.application import ports
from crew_health.trends.application.forecast import ForecastInputRecord, ForecastOutcomeRecord, key_bytes


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StorageUnavailable("corrupt")
    return value


def _integer(value: SqlValue) -> int:
    if type(value) is not int:
        raise StorageUnavailable("corrupt")
    return value


def _instant(value: SqlValue) -> datetime:
    parsed = datetime.fromisoformat(_text(value))
    require_utc(parsed)
    return parsed


def _utc_text(value: datetime) -> str:
    require_utc(value)
    return value.isoformat().replace("+00:00", "Z")


class SqliteForecastRepository:
    def latest(self, view: ReadSnapshot | UnitOfWork, key: ports.SeriesKey) -> ForecastInputRecord | None:
        rows = view.connection.rows(
            "SELECT * FROM trends_forecast_input WHERE series_key=? ORDER BY generation DESC LIMIT 1",
            (key_bytes(key).decode("utf-8"),),
            limit=1,
        )
        return self._input(rows[0]) if rows else None

    def input(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastInputRecord | None:
        rows = view.connection.rows(
            "SELECT * FROM trends_forecast_input WHERE input_ref=?", (input_ref,), limit=1
        )
        return self._input(rows[0]) if rows else None

    def _input(self, row: tuple[SqlValue, ...]) -> ForecastInputRecord:
        try:
            key = TypeAdapter(ports.SeriesKey).validate_python(
                {
                    "person_id": row[2],
                    "mission_id": row[3],
                    "origin_mode": row[4],
                    "code": row[5],
                }
            )
            series = TypeAdapter(tuple[ports.DailyValue, ...]).validate_json(_text(row[9]))
            ids = TypeAdapter(tuple[str, ...]).validate_json(_text(row[10]), strict=True)
            return ForecastInputRecord(
                _text(row[0]),
                key,
                _integer(row[6]),
                _text(row[7]),
                Unit(_text(row[8])),
                series,
                ids,
                _text(row[11]),
                None if row[12] is None else _text(row[12]),
                _instant(row[13]),
            )
        except (ValidationError, ValueError, IndexError) as error:
            raise StorageUnavailable("corrupt") from error

    def insert(self, uow: UnitOfWork, row: ForecastInputRecord) -> None:
        result = uow.connection.execute(
            "INSERT INTO trends_forecast_input VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row.input_ref,
                key_bytes(row.key).decode("utf-8"),
                row.key.person_id,
                row.key.mission_id,
                row.key.origin_mode,
                row.key.code,
                row.generation,
                row.fingerprint,
                row.unit.code,
                TypeAdapter(tuple[ports.DailyValue, ...]).dump_json(row.series).decode("utf-8"),
                TypeAdapter(tuple[str, ...]).dump_json(row.observation_ids).decode("utf-8"),
                row.release_id,
                row.task_id,
                _utc_text(row.created_at),
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")

    def result(self, view: ReadSnapshot | UnitOfWork, input_ref: str) -> ForecastOutcomeRecord | None:
        rows = view.connection.rows(
            "SELECT * FROM trends_forecast_result WHERE input_ref=?", (input_ref,), limit=1
        )
        return self._result(rows[0]) if rows else None

    def _result(self, row: tuple[SqlValue, ...]) -> ForecastOutcomeRecord:
        try:
            outcome: ports.WorkerForecast | ports.WorkerUnavailable
            if row[3] == "unavailable":
                outcome = ports.WorkerUnavailable(_text(row[4]))
            elif row[3] == "forecast":
                outcome = ports.WorkerForecast(
                    _text(row[5]),
                    _text(row[6]),
                    _text(row[7]),
                    _text(row[8]),
                    Decimal(_text(row[9])),
                    TypeAdapter(tuple[ports.ForecastStep, ...]).validate_json(_text(row[10])),
                )
            else:
                raise StorageUnavailable("corrupt")
            return ForecastOutcomeRecord(
                _text(row[0]), _text(row[1]), _integer(row[2]), outcome, _instant(row[11])
            )
        except (ValidationError, ValueError, IndexError, InvalidOperation) as error:
            raise StorageUnavailable("corrupt") from error

    def save_result(self, uow: UnitOfWork, row: ForecastOutcomeRecord) -> None:
        value = row.outcome
        forecast_value = value if isinstance(value, ports.WorkerForecast) else None
        result = uow.connection.execute(
            "INSERT INTO trends_forecast_result VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row.input_ref,
                row.task_id,
                row.task_generation,
                "forecast" if forecast_value is not None else "unavailable",
                value.reason if isinstance(value, ports.WorkerUnavailable) else None,
                forecast_value.model_id if forecast_value is not None else None,
                forecast_value.model_revision if forecast_value is not None else None,
                forecast_value.model_sha256 if forecast_value is not None else None,
                forecast_value.policy_version if forecast_value is not None else None,
                str(forecast_value.interval_level) if forecast_value is not None else None,
                TypeAdapter(tuple[ports.ForecastStep, ...]).dump_json(forecast_value.steps).decode("utf-8")
                if forecast_value is not None
                else None,
                _utc_text(row.created_at),
            ),
        )
        if result.affected_rows != 1:
            raise StorageUnavailable("corrupt")
