"""Loader checks and activation guards of the mission documents as pure rules (E23 8, 8.1).

Check 7 and the effect filter half of check 12 reject a release; the guards keep an open environment from losing
its profile. A finding is the only output; nothing here repairs or defaults content.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

CHECK_7 = "7"
CHECK_12 = "12"
PROFILES = "environment-profiles"


@dataclass(frozen=True, slots=True)
class MissionFinding:
    check_id: str
    document: str
    pointer: str
    message: str


@dataclass(frozen=True, slots=True)
class FilterFacts:
    hazard_id: str
    effect_index: int
    effect_starts_with: str
    only_in: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MissionBounds:
    max_event_types: int
    max_state_facts: int


def _find(check: str, pointer: str, message: str) -> MissionFinding:
    return MissionFinding(check, PROFILES, pointer, message)


def check_profiles(
    profile_environments: Sequence[str],
    profile_sensor_types: Sequence[Sequence[str]],
    environment_ids: Collection[str],
    catalog_ids: Collection[str],
) -> tuple[MissionFinding, ...]:
    """CONTRACTS 11 check 7: one profile per environment, every sensor type exists in the catalog."""
    found: list[MissionFinding] = []
    for environment in sorted(environment_ids):
        count = profile_environments.count(environment)
        if count != 1:
            found.append(_find(CHECK_7, "/profiles", f"{count} profiles for {environment}"))
    for position, sensor_types in enumerate(profile_sensor_types):
        for sensor_type in sensor_types:
            if sensor_type not in catalog_ids:
                found.append(_find(CHECK_7, f"/profiles/{position}/sensor_type_ids", "not in the catalog"))
    return tuple(found)


def check_effect_filters(
    filters: Sequence[FilterFacts], hazard_effects: Mapping[str, Sequence[str]]
) -> tuple[MissionFinding, ...]:
    """CONTRACTS 11 check 12, filter half: effect_starts_with matches the effect at effect_index."""
    found: list[MissionFinding] = []
    for position, rule in enumerate(filters):
        effects = hazard_effects.get(rule.hazard_id)
        pointer = f"/effect_filters/{position}"
        if effects is None or not 0 <= rule.effect_index < len(effects):
            found.append(_find(CHECK_12, pointer, "no such hazard effect"))
        elif not effects[rule.effect_index].startswith(rule.effect_starts_with):
            found.append(_find(CHECK_12, pointer + "/effect_starts_with", "does not match the effect"))
    return tuple(found)


def check_events_and_facts(
    event_type_ids: Sequence[str],
    overlay_links: Sequence[tuple[str | None, str | None]],
    overlay_kinds: Collection[str],
    fact_event_refs: Sequence[Sequence[str]],
    bounds: MissionBounds,
    fact_count: int,
) -> tuple[MissionFinding, ...]:
    """Event types unique and bounded, overlays they start or end exist, facts name existing event types."""
    found: list[MissionFinding] = []
    if len(event_type_ids) > bounds.max_event_types:
        found.append(_find(CHECK_7, "/mission_event_types", "more than mission.max_event_types"))
    if fact_count > bounds.max_state_facts:
        found.append(_find(CHECK_7, "/state_facts", "more than mission.max_state_facts"))
    if len(set(event_type_ids)) != len(event_type_ids):
        found.append(_find(CHECK_7, "/mission_event_types", "duplicate event type id"))
    for position, (starts, ends) in enumerate(overlay_links):
        for kind in (starts, ends):
            if kind is not None and kind not in overlay_kinds:
                found.append(
                    _find(CHECK_7, f"/mission_event_types/{position}", "overlay not in the vocabulary")
                )
    for position, refs in enumerate(fact_event_refs):
        if any(ref not in event_type_ids for ref in refs):
            found.append(_find(CHECK_7, f"/state_facts/{position}", "names an unknown event type"))
    return tuple(found)


def guard_environments_kept(
    candidate_profile_environments: Collection[str],
    active_environment: str | None,
    proposed_environment: str | None,
) -> tuple[MissionFinding, ...]:
    """E23 8.1: a release may not drop the profile of the active environment or of an open proposal."""
    found: list[MissionFinding] = []
    if active_environment is not None and active_environment not in candidate_profile_environments:
        found.append(_find(CHECK_7, "/profiles", "no profile for the active environment"))
    if proposed_environment is not None and proposed_environment not in candidate_profile_environments:
        found.append(_find(CHECK_7, "/profiles", "no profile for the proposed environment"))
    return tuple(found)
