"""Validated mission configuration as pure facts (E23 8). The adapter parses and validates the documents; these
shapes carry what the rules read, so the application layer never imports an adapter."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.mission.domain.day import DayLabelFacts
from crew_health.mission.domain.events import EventTypeFacts
from crew_health.mission.domain.loader_checks import FilterFacts
from crew_health.mission.domain.profile import ProfileTemplate
from crew_health.mission.domain.state_facts import StateFactConfig


@dataclass(frozen=True, slots=True)
class ReturnOptionFacts:
    trip_text: str
    source_ids: tuple[str, ...]
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    template: ProfileTemplate
    day_label: DayLabelFacts
    sensor_type_ids: tuple[str, ...]
    overlays: tuple[str, ...]
    return_option: ReturnOptionFacts | None


@dataclass(frozen=True, slots=True)
class MissionConfig:
    notice: str
    confirmations_required: int
    event_types: tuple[EventTypeFacts, ...]
    state_facts: tuple[StateFactConfig, ...]
    effect_filters: tuple[FilterFacts, ...]
    profiles: tuple[ProfileConfig, ...]

    def profile(self, environment_id: str) -> ProfileConfig | None:
        return next((p for p in self.profiles if p.template.environment_id == environment_id), None)

    def event_type(self, event_type_id: str) -> EventTypeFacts | None:
        return next((t for t in self.event_types if t.id == event_type_id), None)
