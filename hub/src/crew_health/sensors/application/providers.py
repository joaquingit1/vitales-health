"""The ports sensors provides to other modules (E23 4.2): SensorStateQuery, SensorSeriesReader and
ProvenanceStreamsPort. Reads only; each is bound to the caller's view, and a call with none opens a snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, UnitOfWork
from crew_health.sensors.application.ports import (
    Freshness,
    HabitatStream,
    SeriesPoint,
    SeriesSlice,
)
from crew_health.sensors.application.queries import decide_now
from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.domain.errors import InvalidValue


class SensorProviders:
    def __init__(self, rt: Runtime, snapshots: ReadSnapshotFactory) -> None:
        self._rt, self._snapshots = rt, snapshots

    def freshness(self, uow: UnitOfWork, channel_id: str) -> Freshness:
        channel = self._rt.store.channel(uow, channel_id)
        if channel is None:
            raise InvalidValue("unknown channel")
        return decide_now(uow, channel, self._rt.store.environment(uow)).state

    def channels(self, uow: UnitOfWork) -> Sequence[str]:
        return tuple(c.facts.channel_id for c in self._rt.store.channels(uow))

    def type_name(self, uow: UnitOfWork, sensor_type_id: str) -> str:
        kind = self._rt.config_of(uow.release).sensor_type(sensor_type_id)
        if kind is None:
            raise InvalidValue("unknown sensor type")
        return kind.name

    def accepted(self, channel_id: str, *, until: datetime, limit: int) -> SeriesSlice:
        rt = self._rt
        if not 1 <= limit <= rt.limits.series_max_points:
            raise InvalidValue("limit outside sensors.series_max_points")
        with self._snapshots.snapshot() as view:
            channel = rt.store.channel(view, channel_id)
            if channel is None:
                raise InvalidValue("unknown channel")
            kind = rt.config_of(view.release).sensor_type(channel.facts.sensor_type_id)
            if kind is None:
                raise InvalidValue("unknown sensor type")
            newest = rt.store.newest_readings(
                view,
                channel_id,
                until=until,
                statuses=("accepted",),
                time_states=("known", "replay"),
                limit=limit + 1,
            )  # the newest window, so trends never read a frozen old slice; one more row tells truncation
        chosen = list(reversed(newest[:limit]))  # ascending for consumers
        records = newest
        points = tuple(
            SeriesPoint(
                r.observed_at, Quantity(r.canonical, Unit(kind.unit)), r.quality_status, r.origin_mode
            )
            for r in chosen
        )
        return SeriesSlice(points, len(records) > limit)

    def habitat_streams(self, snapshot: ReadSnapshot) -> Sequence[HabitatStream]:
        return tuple(
            HabitatStream(
                c.facts.channel_id,
                c.facts.sensor_type_id,
                c.facts.origin_mode,
                c.facts.source_label,
                c.facts.dataset,
            )
            for c in self._rt.store.channels(snapshot)
        )
