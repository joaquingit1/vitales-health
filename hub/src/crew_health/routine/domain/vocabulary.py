from typing import Literal

CadenceWord = Literal[
    "continuous",
    "daily",
    "every_n_days",
    "weekly",
    "monthly",
    "per_eva",
    "after_event",
    "mission_phase",
    "as_needed",
]
OccurrenceState = Literal["not_due", "due", "later", "done", "skipped", "missed"]
CauseKind = Literal["cadence", "override", "per_eva", "cadence_event", "assistant", "person"]
SkipReason = Literal["not_available", "declined", "no_time", "not_needed_now", "other"]
Overlay = Literal["eva", "post_eva", "exercise", "sleep"]
OriginMode = Literal["LIVE", "REPLAY", "SIM"]
Audience = Literal["person", "everyone_in_location", "all_crew"]
StartedBy = Literal["rule", "sensor_limit", "person"]
OccurrenceStartedBy = Literal["rule", "sensor_limit", "person", "assistant"]
SourceKind = Literal["warning", "mission_event", "overlay", "observation", "answer", "person"]
EndsKind = Literal["warning_cleared", "effect_complete", "days", "person_stops", "mission_event"]
EndReason = Literal[
    "warning_cleared",
    "effect_complete",
    "days_elapsed",
    "stopped_by_person",
    "mission_event",
    "environment_changed",
]
DueKind = Literal["specific", "all_day"]  # internal enum
MemberBasis = Literal["location", "location_unknown", "site", "subject"]  # internal enum
ActorKind = Literal[
    "person", "station", "ground", "operator", "adapter", "engine", "assistant"
]  # internal enum

PERIODIC_WORDS: frozenset[CadenceWord] = frozenset(
    ("continuous", "daily", "every_n_days", "weekly", "monthly")
)
OPEN_STATES: frozenset[OccurrenceState] = frozenset(("due", "later", "not_due"))
FINAL_STATES: frozenset[OccurrenceState] = frozenset(("done", "skipped", "missed"))
