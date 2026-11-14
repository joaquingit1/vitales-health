"""Fixed vocabulary of the warnings engine (E24 3.1).

The enums mirror `warning.schema.json`, `common.schema.json` and `config-warning-rules.schema.json`.
A test compares each of them with the contract, so a contract change cannot go unnoticed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Level(StrEnum):
    INFO = "info"
    WATCH = "watch"
    ACT = "act"
    EMERGENCY = "emergency"

    @property
    def rank(self) -> int:
        return LEVEL_ORDER.index(self)

    # Levels order by rank, never as strings: "watch" is not above "emergency".
    def __lt__(self, other: object) -> bool:
        return self.rank < other.rank if isinstance(other, Level) else NotImplemented

    def __le__(self, other: object) -> bool:
        return self.rank <= other.rank if isinstance(other, Level) else NotImplemented

    def __gt__(self, other: object) -> bool:
        return self.rank > other.rank if isinstance(other, Level) else NotImplemented

    def __ge__(self, other: object) -> bool:
        return self.rank >= other.rank if isinstance(other, Level) else NotImplemented


LEVEL_ORDER: Final[tuple[Level, ...]] = (Level.INFO, Level.WATCH, Level.ACT, Level.EMERGENCY)
ASSISTANT_LEVELS: Final[frozenset[Level]] = frozenset({Level.INFO, Level.WATCH})


class RaisedBy(StrEnum):
    RULE = "rule"
    SENSOR_LIMIT = "sensor_limit"
    ASSISTANT = "assistant"


class Scope(StrEnum):
    HABITAT = "habitat"
    PERSON = "person"
    CREW = "crew"


class WarningState(StrEnum):
    RAISED = "raised"
    ACKNOWLEDGED = "acknowledged"
    CLEARED = "cleared"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


ACTIVE: Final[frozenset[WarningState]] = frozenset({WarningState.RAISED, WarningState.ACKNOWLEDGED})
FINAL: Final[frozenset[WarningState]] = frozenset(
    {WarningState.CLEARED, WarningState.RESOLVED, WarningState.DISMISSED}
)


class ClearReason(StrEnum):
    CONDITION_FALSE = "condition_false"
    CLEAR_EVENT = "clear_event"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    ENVIRONMENT_CHANGED = "environment_changed"
    CONFIG_CHANGED = "config_changed"


# A warning that ended this way spends its trigger (E24 3.9). The other reasons leave it reusable.
SPENT_CLEAR_REASONS: Final[frozenset[ClearReason]] = frozenset(
    {ClearReason.CONDITION_FALSE, ClearReason.CLEAR_EVENT, ClearReason.EXPIRED}
)


class ConditionKind(StrEnum):
    SENSOR_LIMIT = "sensor_limit"
    SENSOR_QUIET = "sensor_quiet"
    MISSION_EVENT = "mission_event"
    ANSWER = "answer"
    STATE_FACT = "state_fact"
    ASSISTANT_INSIGHT = "assistant_insight"
    GROUND_SILENCE = "ground_silence"
    SUPPLY_COVER_LOW = "supply_cover_low"


SENSOR_KINDS: Final[frozenset[ConditionKind]] = frozenset(
    {ConditionKind.SENSOR_LIMIT, ConditionKind.SENSOR_QUIET}
)


class ClearBy(StrEnum):
    CONDITION_FALSE = "condition_false"
    CLEAR_EVENT = "clear_event"
    NEVER = "never"


class WarningAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    RESOLVE = "resolve"
    DISMISS = "dismiss"
    ADD_NOTE = "add_note"


class OriginMode(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    SIM = "SIM"


class EnvironmentId(StrEnum):
    STATION_LEO = "station_leo"
    TRANSIT = "transit"
    LUNAR_SURFACE = "lunar_surface"
    MARS_SURFACE = "mars_surface"


class Basis(StrEnum):
    SOURCED = "sourced"
    TEAM_DEFAULT = "team default"


class EvidenceKind(StrEnum):
    READING = "reading"
    OBSERVATION = "observation"
    ANSWER = "answer"
    DAILY_CHECK = "daily_check"
    MISSION_EVENT = "mission_event"
    STATE_FACT = "state_fact"
    INSIGHT = "insight"
    WARNING = "warning"
    ROUTINE_OCCURRENCE = "routine_occurrence"
    HELP_SESSION = "help_session"
    PLAN_RUN = "plan_run"
    GUIDED_CHECK = "guided_check"
    SUPPLY_USE = "supply_use"


class Presence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    DECLINED = "declined"


class StepsFrom(StrEnum):
    HAZARD_WHAT_TO_DO = "hazard_what_to_do"
    LISTED = "listed"
    ASSISTANT = "assistant"


BODY_REGIONS: Final[frozenset[str]] = frozenset(
    {
        "head", "eyes", "ear", "neck", "shoulder", "heart", "lungs", "abdomen", "flank", "back",
        "wrist", "hand", "legs", "knee", "ankle",
    }
)  # fmt: skip

# Overlay vocabulary (common.schema.json `overlay`). Sleep is the one overlay Q9 reasons about.
OVERLAY_SLEEP: Final[str] = "sleep"
OVERLAY_EVA: Final[str] = "eva"
OVERLAYS: Final[frozenset[str]] = frozenset({"eva", "post_eva", "exercise", "sleep"})

# Ground link `source` value that marks a demonstration switch (E24 10, origin rules).
LINK_SOURCE_DEMO_SWITCH: Final[str] = "demo_switch"

ACTOR_KINDS: Final[frozenset[str]] = frozenset(
    {"person", "station", "ground", "operator", "adapter", "engine", "assistant"}
)
