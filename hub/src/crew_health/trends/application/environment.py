"""Habitat series retain their original source dates and place context."""

from datetime import datetime

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReadSnapshot, SessionContext
from crew_health.trends.application import ports
from crew_health.trends.domain import environment, model
from crew_health.trends.settings import TrendsSettings


class TrendsEnvironment:
    def __init__(self, reader: ports.SensorSeriesReader | None, settings: TrendsSettings) -> None:
        self.reader, self.settings = reader, settings

    def series(
        self, snapshot: ReadSnapshot, session: SessionContext, channel_id: str, *, until: datetime
    ) -> ports.EnvironmentSeries | ports.Unavailable:
        # Habitat series are place context: a crew member and the shared station screen may read them (CONTRACTS
        # 1086: station reads habitat views); the assistant, ground and every other actor may not.
        if session.actor.kind not in ("person", "station"):
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        try:
            require_id(channel_id)
        except ProblemError as error:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value) from error
        if self.reader is None:
            return ports.Unavailable("E23", "sensor_series_unbound")
        result = self.reader.accepted(channel_id, until=until, limit=self.settings.environment_points_max)
        value = environment.build_series(
            channel_id,
            tuple(
                environment.SeriesPoint(p.observed_at, p.value, p.quality_status, p.origin_mode)
                for p in result.points
            ),
            truncated=result.truncated,
        )
        if isinstance(value, model.Unavailable):
            return ports.Unavailable(value.provider, value.reason)
        return ports.EnvironmentSeries(
            value.channel_id,
            value.origin_mode,
            tuple(
                ports.EnvironmentPoint(p.observed_at, p.value, p.quality_status, p.origin_mode)
                for p in value.points
            ),
            value.personal_exposure,
            value.place_context_only,
            value.original_dates_kept,
            ports.Unavailable(value.pressure_basis.provider, value.pressure_basis.reason),
        )
