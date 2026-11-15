"""SYSTEM 5.6: parse only the view's pinned documents."""

import json
import threading
from dataclasses import dataclass

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_environment_hazards_schema import EnvironmentHazardsJson
from crew_health.contracts_gen.schemas.config_environment_profiles_schema import (
    EnvironmentProfilesJson,
    Profile,
)
from crew_health.groundlink.domain.link import ProfileLink, ScheduleWindow, split_delay_text, window_findings
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot


def profile_link(profile: Profile) -> ProfileLink:
    return ProfileLink(
        profile.environment_id.root,
        profile.comms_queue.simulated_one_way_delay_seconds,
        profile.comms_queue.scheduled_status,
        profile.link.default_gap_kind.root,
        tuple(
            ScheduleWindow(
                w.starts_at.root,
                w.ends_at.root,
                w.status,
                None if w.gap_kind is None else w.gap_kind.root,
                w.planned,
            )
            for w in profile.link.sim_schedule
        ),
    )


@dataclass(frozen=True, slots=True)
class LinkConfig:
    profile: ProfileLink
    delay_text: str
    delay_source_ids: tuple[str, ...]


class LinkConfigCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[EnvironmentProfilesJson, EnvironmentHazardsJson]] = {}
        self._lock = threading.Lock()

    def get(self, view: ReadSnapshot, environment_id: str) -> LinkConfig:
        try:
            profiles_doc = view.release.document(ConfigName.ENVIRONMENT_PROFILES)
            hazards_doc = view.release.document(ConfigName.ENVIRONMENT_HAZARDS)
            key = profiles_doc.version, hazards_doc.version
            with self._lock:
                parsed = self._cache.get(key)
                if parsed is None:
                    profiles = EnvironmentProfilesJson.model_validate(json.loads(profiles_doc.raw))
                    hazards = EnvironmentHazardsJson.model_validate(json.loads(hazards_doc.raw))
                    if any(window_findings(profile_link(p).windows) for p in profiles.profiles):
                        raise ValueError("invalid schedule")
                    parsed = profiles, hazards
                    if len(self._cache) >= 8:
                        del self._cache[next(iter(self._cache))]
                    self._cache[key] = parsed
            profile = next(p for p in parsed[0].profiles if p.environment_id.root == environment_id)
            hazard = next(e for e in parsed[1].environments if e.id.root == environment_id)
            text, ids = split_delay_text(hazard.earth_delay, {source.id.root for source in parsed[1].sources})
            return LinkConfig(profile_link(profile), text, ids)
        except (KeyError, ValueError, ValidationError, StopIteration, ProblemError) as error:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value) from error
