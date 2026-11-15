"""E23's provided ports over the live repository: MissionStatePort, OverlayPort, LocationPort, DayLabelPort and
EnvironmentFacts (E23 4.2). Each call reads through the caller's view; only a call given none opens a snapshot.

Everything release dependent (return option, day label, the unknown word) is read from the release of that same
view, so a configuration change never reaches a read that started before it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MissionClock
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, UnitOfWork
from crew_health.mission.application.ports import (
    MissionFacts,
    OriginMode,
    OverlayFact,
    PlaceFact,
    ReturnOption,
    Snap,
)
from crew_health.mission.application.queries import MissionReader, MissionState
from crew_health.mission.domain.config import MissionConfig
from crew_health.mission.domain.day import DayLabelFacts


@dataclass(frozen=True, slots=True)
class LiveMissionState:
    reader: MissionReader
    snapshots: ReadSnapshotFactory
    clock: MissionClock
    config_of: Callable[[ReleaseHandle], MissionConfig]

    def _state(self, release: ReleaseHandle) -> MissionState:
        config = self.config_of(release)
        options = {
            p.template.environment_id: None
            if p.return_option is None
            else ReturnOption(
                p.return_option.trip_text, p.return_option.source_ids, p.return_option.to_be_set_by
            )
            for p in config.profiles
        }

        def label(record: object) -> DayLabelFacts:
            environment_id = getattr(record, "environment_id", "")
            profile = config.profile(environment_id)
            if profile is None:
                raise ProblemError(ErrorCode.CONFIG_INVALID, "no day label for " + str(environment_id))
            return profile.day_label

        return MissionState(self.reader, self.snapshots, options, self.clock, label)

    def _any(self) -> MissionState:
        with self.snapshots.snapshot() as snapshot:
            return self._state(snapshot.release)

    def _bare(self) -> MissionState:
        """A state for answers that read only stored records (identity, overlays, locations, day end). They need no
        configuration, so they answer before the first release is active; labels and return options do not."""

        def unavailable(record: object) -> DayLabelFacts:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "a day label needs the active release")

        return MissionState(self.reader, self.snapshots, {}, self.clock, unavailable)

    # --- MissionStatePort ---------------------------------------------------------------------------------

    def current(self, uow: Snap | None = None) -> MissionFacts | None:
        return self._bare().current(uow)

    def mission_mode(self, uow: Snap | None = None) -> OriginMode | None:
        return self._bare().mission_mode(uow)

    def active_environment_id(self, uow: Snap | None = None) -> str | None:
        return self._bare().active_environment_id(uow)

    def overlays(self, uow: UnitOfWork, person_id: str, since: datetime) -> Sequence[OverlayFact]:
        return self._state(uow.release).overlays(uow, person_id, since)

    def return_option(self, uow: Snap | None, environment_id: str) -> ReturnOption | None:
        if uow is not None:
            return self._state(uow.release).return_option(uow, environment_id)
        return self._any().return_option(None, environment_id)

    def day_end(self, instant: datetime) -> datetime:
        return self._bare().day_end(instant)

    # --- OverlayPort, LocationPort, DayLabelPort ----------------------------------------------------------

    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]:
        return self._bare().active(person_ids, at)

    def intervals(
        self, person_id: str, kinds: Collection[str], since: datetime, until: datetime, limit: int
    ) -> Sequence[OverlayFact]:
        return self._bare().intervals(person_id, kinds, since, until, limit)

    def location_of(self, person_id: str, at: datetime) -> str | None:
        return self._bare().location_of(person_id, at)

    def inline_label_at(self, instant: datetime) -> str:
        with self.snapshots.snapshot() as snapshot:
            state = self._state(snapshot.release)
            unknown = _unknown_word(snapshot.release)
        label = state.inline_label_at(instant, unknown=unknown)
        if label is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission_not_initialized", retryable=True)
        return label

    # --- EnvironmentFacts ---------------------------------------------------------------------------------

    def place_facts(self, uow: Snap, start: datetime, end: datetime) -> Sequence[PlaceFact]:
        """Habitat facts for the assistant. Nothing is composed from mission state here: an empty answer is
        honest, and the sensor readings reach the assistant only through their own provenance port (S6.4)."""
        return ()


def _unknown_word(release: ReleaseHandle) -> str:
    try:
        document = json.loads(release.document(ConfigName.UI_COPY).raw)
        word = document["designed_states"]["unknown"]
    except (ValueError, KeyError, TypeError) as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "ui-copy has no designed_states.unknown") from error
    if not isinstance(word, str) or not word:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "ui-copy designed_states.unknown is empty")
    return word


def snapshot_release(view: ReadSnapshot | UnitOfWork) -> ReleaseHandle:
    return view.release
