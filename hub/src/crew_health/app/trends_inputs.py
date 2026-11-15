# mypy: disable-error-code="type-abstract"
# The E01 registry keys providers by their Protocol class (SYSTEM 5), which mypy reports as type-abstract.
"""E14's input ports bound to their providers on the served hub (SYSTEM 4.2 and 4.4).

E14 declares structural copies of the ports it consumes so it never imports another module. Here the composition
binds them: `ObservationReader` is records' `RecordsQueries` (its `accepted` page is the protocol itself, R-E01-17),
and `SensorSeriesReader` is E23's accepted series, copied point for point into E14's own value types.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from crew_health.app.assembly import RecordHttpServices
from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.sensors.application import ports as sensor_ports
from crew_health.trends.application import ports as trend_ports


class SensorSeries:
    """E14's SensorSeriesReader over E23's: the same accepted points, never resampled or filled."""

    def __init__(self, source: sensor_ports.SensorSeriesReader) -> None:
        self._source = source

    def accepted(self, channel_id: str, *, until: datetime, limit: int) -> trend_ports.SeriesSlice:
        found = self._source.accepted(channel_id, until=until, limit=limit)
        return trend_ports.SeriesSlice(
            tuple(
                trend_ports.SeriesPoint(p.observed_at, p.value, p.quality_status, p.origin_mode)
                for p in found.points
            ),
            found.truncated,
        )


def compose(registry: ModuleRegistry) -> None:
    """Binds each E14 input whose provider is installed; an absent provider leaves E14's own unavailable answer."""
    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    observations = cast(type[object], trend_ports.ObservationReader)
    if observations not in providers and RecordHttpServices in providers:

        def reader(resolver: Resolver) -> object:
            return resolver.require(RecordHttpServices).queries

        registry.provide(observations, reader, requires=(RecordHttpServices,))
    series = cast(type[object], trend_ports.SensorSeriesReader)
    source = cast(type[object], sensor_ports.SensorSeriesReader)
    if series not in providers and source in providers:

        def sensor(resolver: Resolver) -> object:
            return SensorSeries(cast(sensor_ports.SensorSeriesReader, resolver.require(source)))

        registry.provide(series, sensor, requires=(source,))
