"""Validated mission configuration (E23 8). The only place generated models parse mission documents.

Documents are validated against their generated schema, then mapped to the pure facts of the domain. An invalid
candidate is rejected whole and the last good one stays (AGENTS 3).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_environment_hazards_schema import EnvironmentHazardsJson
from crew_health.contracts_gen.schemas.config_environment_profiles_schema import EnvironmentProfilesJson
from crew_health.contracts_gen.schemas.config_routine_schedule_schema import RoutineScheduleJson
from crew_health.contracts_gen.schemas.config_sensor_catalog_schema import SensorCatalogJson
from crew_health.contracts_gen.schemas.config_sensor_limits_schema import SensorLimitsJson
from crew_health.contracts_gen.schemas.config_warning_rules_schema import WarningRulesJson
from crew_health.kernel.config import CandidateRelease, Finding
from crew_health.kernel.errors import ProblemError
from crew_health.mission.domain.day import DayLabelFacts
from crew_health.mission.domain.events import EventTypeFacts
from crew_health.mission.domain.loader_checks import (
    CHECK_7,
    CHECK_12,
    FilterFacts,
    MissionBounds,
    MissionFinding,
    check_effect_filters,
    check_events_and_facts,
    check_profiles,
)
from crew_health.mission.domain.profile import ProfileLists, ProfileTemplate
from crew_health.mission.domain.state_facts import StateFactConfig

Json = dict[str, Any]
ReadDocument = Callable[[ConfigName], bytes]
_ID = r"(?:EV|RT|RS|SK|S|C|N|L)\d{2}"
_GROUP = re.compile(r"\s*\(\s*" + _ID + r"(?:\s*,\s*" + _ID + r")*\s*\)")
_SPACE = re.compile(r"\s+")
_OVERLAYS = ("eva", "post_eva", "exercise", "sleep")  # CONTRACTS 11 fixed vocabulary
_ENVIRONMENTS = ("station_leo", "transit", "lunar_surface", "mars_surface")  # CONTRACTS 11 fixed vocabulary
_DOCUMENTS: dict[ConfigName, type[BaseModel]] = {
    ConfigName.ENVIRONMENT_PROFILES: EnvironmentProfilesJson,
    ConfigName.ENVIRONMENT_HAZARDS: EnvironmentHazardsJson,
    ConfigName.SENSOR_CATALOG: SensorCatalogJson,
    ConfigName.SENSOR_LIMITS: SensorLimitsJson,
    ConfigName.WARNING_RULES: WarningRulesJson,
    ConfigName.ROUTINE_SCHEDULE: RoutineScheduleJson,
}


def _bad(message: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFIG_INVALID, message)


def _reject_constant(name: str) -> object:
    raise ValueError("non finite JSON constant " + name)


def _document(name: ConfigName, raw: bytes) -> Json:
    """Validate against the generated schema, then decode. NaN and Infinity are refused."""
    try:
        _DOCUMENTS[name].model_validate_json(raw)
        value = json.loads(raw, parse_constant=_reject_constant)
    except (ValidationError, ValueError, UnicodeDecodeError) as error:
        raise _bad(name.value + " is corrupt or fails its schema") from error
    if not isinstance(value, dict):
        raise _bad(name.value + " is not an object")
    return cast(Json, value)


def strip_source_ids(text: str) -> str:
    return _SPACE.sub(" ", _GROUP.sub("", text)).strip()


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


def _profile(item: Json, delays: dict[str, str]) -> ProfileConfig:
    label = item["day_label"]
    option = item["return_option"]
    environment = item["environment_id"]
    if environment not in delays:
        raise _bad("no earth_delay for " + environment)
    template = ProfileTemplate(
        environment,
        item["label"],
        item["place"],
        item["eva_word"],
        strip_source_ids(delays[environment]),
        item["comms_queue"]["simulated_one_way_delay_seconds"],
    )
    return ProfileConfig(
        template,
        DayLabelFacts(
            label["counter"],
            label["template"],
            label["short_template"],
            label["inline_template"],
            label["day_length_seconds"],
        ),
        tuple(item["sensor_type_ids"]),
        tuple(item["overlays"]),
        None
        if option is None
        else ReturnOptionFacts(option["trip_text"], tuple(option["source_ids"]), option["to_be_set_by"]),
    )


def load_mission_config(read: ReadDocument) -> MissionConfig:
    document = _document(ConfigName.ENVIRONMENT_PROFILES, read(ConfigName.ENVIRONMENT_PROFILES))
    hazards = _document(ConfigName.ENVIRONMENT_HAZARDS, read(ConfigName.ENVIRONMENT_HAZARDS))
    delays = {e["id"]: e["earth_delay"] for e in hazards["environments"]}
    return MissionConfig(
        notice=document["notice"],
        confirmations_required=document["change_confirmation"]["confirmations_required"],
        event_types=tuple(
            EventTypeFacts(
                t["id"], t["label"], t["starts_overlay"], t["ends_overlay"], tuple(t["who_may_record"])
            )
            for t in document["mission_event_types"]
        ),
        state_facts=tuple(
            StateFactConfig(
                f["id"],
                f["label"],
                tuple(f["set_by_event_types"]),
                tuple(f["cleared_by_event_types"]),
                tuple(f["environments"]),
            )
            for f in document["state_facts"]
        ),
        effect_filters=tuple(
            FilterFacts(f["hazard_id"], f["effect_index"], f["effect_starts_with"], tuple(f["only_in"]))
            for f in document["effect_filters"]
        ),
        profiles=tuple(_profile(p, delays) for p in document["profiles"]),
    )


def lists_for(
    read: ReadDocument, config: MissionConfig, environment_id: str, channel_ids: Sequence[str]
) -> ProfileLists:
    """The six lists of a record, recomputed from the release (CONTRACTS 12 activation step 2)."""
    profile = config.profile(environment_id)
    if profile is None:
        raise _bad("no profile for " + environment_id)
    hazards = _document(ConfigName.ENVIRONMENT_HAZARDS, read(ConfigName.ENVIRONMENT_HAZARDS))
    limits = _document(ConfigName.SENSOR_LIMITS, read(ConfigName.SENSOR_LIMITS))
    rules = _document(ConfigName.WARNING_RULES, read(ConfigName.WARNING_RULES))
    routine = _document(ConfigName.ROUTINE_SCHEDULE, read(ConfigName.ROUTINE_SCHEDULE))
    hazard_ids = next(
        (tuple(e["hazard_ids"]) for e in hazards["environments"] if e["id"] == environment_id), ()
    )
    return ProfileLists(
        hazard_ids=hazard_ids,
        limit_ids=tuple(x["limit_id"] for x in limits["limits"] if environment_id in x["environments"]),
        warning_definition_ids=tuple(
            d["id"] for d in rules["definitions"] if environment_id in d["environments"]
        ),
        sensor_type_ids=profile.sensor_type_ids,
        sensor_channel_ids=tuple(channel_ids),
        routine_item_ids=tuple(i["id"] for i in routine["items"]),
    )


def _finding(found: Sequence[MissionFinding]) -> tuple[Finding, ...]:
    return tuple(Finding(f.check_id, ConfigName(f.document), f.pointer, f.message) for f in found)


def _raw(candidate: CandidateRelease, name: ConfigName) -> bytes | None:
    view = candidate.documents.get(name)
    return None if view is None else view.raw


@dataclass(frozen=True, slots=True)
class Check7Profiles:
    """CONTRACTS 11 check 7 plus the mission-document rules (event types, overlays, facts, bounds)."""

    bounds: MissionBounds
    check_id: str = CHECK_7

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        profiles_raw = _raw(candidate, ConfigName.ENVIRONMENT_PROFILES)
        catalog_raw = _raw(candidate, ConfigName.SENSOR_CATALOG)
        if profiles_raw is None or catalog_raw is None:
            return (Finding(self.check_id, ConfigName.ENVIRONMENT_PROFILES, "", "document missing"),)
        try:
            document = _document(ConfigName.ENVIRONMENT_PROFILES, profiles_raw)
            catalog = _document(ConfigName.SENSOR_CATALOG, catalog_raw)
        except ProblemError:
            return (
                Finding(self.check_id, ConfigName.ENVIRONMENT_PROFILES, "", "corrupt or fails its schema"),
            )
        profiles = document["profiles"]
        types = document["mission_event_types"]
        found = [
            *check_profiles(
                [p["environment_id"] for p in profiles],
                [p["sensor_type_ids"] for p in profiles],
                _ENVIRONMENTS,
                {s["id"] for s in catalog["sensor_types"]},
            ),
            *check_events_and_facts(
                [t["id"] for t in types],
                [(t["starts_overlay"], t["ends_overlay"]) for t in types],
                _OVERLAYS,
                [f["set_by_event_types"] + f["cleared_by_event_types"] for f in document["state_facts"]],
                self.bounds,
                len(document["state_facts"]),
            ),
        ]
        return _finding(found)


@dataclass(frozen=True, slots=True)
class Check12EffectFilters:
    """CONTRACTS 11 check 12, effect filter half."""

    check_id: str = CHECK_12 + ".effect"  # the contributor id E01 requires (config loader)

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        profiles_raw = _raw(candidate, ConfigName.ENVIRONMENT_PROFILES)
        hazards_raw = _raw(candidate, ConfigName.ENVIRONMENT_HAZARDS)
        if profiles_raw is None or hazards_raw is None:
            return (Finding(self.check_id, ConfigName.ENVIRONMENT_PROFILES, "", "document missing"),)
        try:
            document = _document(ConfigName.ENVIRONMENT_PROFILES, profiles_raw)
            hazards = _document(ConfigName.ENVIRONMENT_HAZARDS, hazards_raw)
        except ProblemError:
            return (
                Finding(self.check_id, ConfigName.ENVIRONMENT_PROFILES, "", "corrupt or fails its schema"),
            )
        effects = {h["id"]: [e["effect"] for e in h["effects"]] for h in hazards["hazards"]}
        filters = [
            FilterFacts(f["hazard_id"], f["effect_index"], f["effect_starts_with"], tuple(f["only_in"]))
            for f in document["effect_filters"]
        ]
        return _finding(check_effect_filters(filters, effects))
