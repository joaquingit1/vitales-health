"""The ports sensors provides to other modules (E23 4.2): SensorStateQuery, SensorSeriesReader and
ProvenanceStreamsPort. Reads only; each is bound to the caller's view, and a call with none opens a snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, UnitOfWork
from crew_health.sensors.application.evaluate import SensorEvaluator
from crew_health.sensors.application.exports import sensor_evaluated
from crew_health.sensors.application.ports import (
    Freshness,
    HabitatStream,
    SensorEvaluated,
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

    def last_results(self, uow: UnitOfWork, channel_id: str) -> SensorEvaluated | None:
        """The channel's newest evaluation, computed from its stored readings under the release now active, so a
        consumer can re-evaluate after a configuration activation without a new reading. None when the channel
        has no evaluated reading in the active environment. Freshness is the decision at this unit of work's now."""
        rt = self._rt
        channel = rt.store.channel(uow, channel_id)
        if channel is None:
            raise InvalidValue("unknown channel")
        environment = rt.store.environment(uow)
        current = SensorEvaluator(rt).current(uow, channel)
        if environment is None or current is None:
            return None
        evaluation, kind = current
        now = decide_now(uow, channel, environment).state
        fresh = replace(channel, freshness=now if now != "never_reported" else channel.freshness)
        return sensor_evaluated(
            rt, uow, rt.config_of(uow.release), kind, fresh, evaluation, uow.release.release_id
        )

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
            records = [
                r
                for r in rt.store.readings_since(
                    view, channel_id, since=None, up_to_rev=None, limit=rt.limits.window_scan_max_readings
                )
                if r.quality_status == "accepted"
                and r.time_state in ("known", "replay")
                and r.observed_at <= until
            ]
        records.sort(key=lambda r: (r.observed_at, r.rev or 0))
        chosen = records[-limit:]
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
