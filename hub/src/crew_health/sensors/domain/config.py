"""Validated sensor configuration as pure facts (E23 8). The adapter parses the documents; the rules and the
application read this shape, so no layer above the adapter imports it."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.sensors.domain.card import EffectFilter, HazardFacts
from crew_health.sensors.domain.channel import SensorTypeFacts
from crew_health.sensors.domain.evaluation import LimitFacts
from crew_health.sensors.domain.loader_checks import HazardLevels, LimitEntry


@dataclass(frozen=True, slots=True)
class SensorConfig:
    catalog_notice: str
    limits_notice: str
    hazards_notice: str
    sensor_types: tuple[SensorTypeFacts, ...]
    limit_entries: tuple[LimitEntry, ...]
    hazards: tuple[HazardFacts, ...]
    hazard_levels: tuple[HazardLevels, ...]
    earth_delay: tuple[tuple[str, str], ...]
    environment_hazard_ids: tuple[tuple[str, tuple[str, ...]], ...]
    effect_filters: tuple[EffectFilter, ...]
    canonical_ucum: tuple[tuple[str, str], ...] = ()

    @property
    def limits(self) -> tuple[LimitFacts, ...]:
        return tuple(entry.limit for entry in self.limit_entries)

    def sensor_type(self, sensor_type_id: str) -> SensorTypeFacts | None:
        return next((s for s in self.sensor_types if s.id == sensor_type_id), None)

    def canonical_unit(self, sensor_type_id: str) -> str | None:
        """The UCUM code of a type's canonical unit from the release's unit mapping; None when it names none."""
        return dict(self.canonical_ucum).get(sensor_type_id)

    def hazard(self, hazard_id: str) -> HazardFacts | None:
        return next((h for h in self.hazards if h.id == hazard_id), None)

    @property
    def notices(self) -> tuple[str, ...]:
        """Required notices, retained and shown wherever this content is rendered (CONTRACTS 11)."""
        return tuple(dict.fromkeys((self.catalog_notice, self.limits_notice, self.hazards_notice)))
