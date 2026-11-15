# Generated from verified package schemas.
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class _Table[K, V](Mapping[K, V]):
    pairs: tuple[tuple[K, V], ...]

    def __getitem__(self, key: K) -> V:
        for candidate, value in self.pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        return (key for key, _ in self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)


class ErrorCode(StrEnum):
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    UNAUTHORIZED_SCOPE = "UNAUTHORIZED_SCOPE"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    ADAPTER_NOT_TRUSTED = "ADAPTER_NOT_TRUSTED"
    AGGREGATE_ONLY = "AGGREGATE_ONLY"
    NOT_FOUND = "NOT_FOUND"
    STALE_VERSION = "STALE_VERSION"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    SESSION_CLOSED = "SESSION_CLOSED"
    CONFIG_VERSION_MISMATCH = "CONFIG_VERSION_MISMATCH"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    INVALID_UNIT = "INVALID_UNIT"
    INVALID_VALUE = "INVALID_VALUE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ORIGIN_MISMATCH = "ORIGIN_MISMATCH"
    KIT_CANNOT_PERFORM = "KIT_CANNOT_PERFORM"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    CONTEXT_INAPPLICABLE = "CONTEXT_INAPPLICABLE"
    SOURCE_NOT_APPROVED = "SOURCE_NOT_APPROVED"
    REPORT_UNCONFIRMED = "REPORT_UNCONFIRMED"
    LEVEL_NOT_ALLOWED = "LEVEL_NOT_ALLOWED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CONFIG_INVALID = "CONFIG_INVALID"
    STEP_NOT_AVAILABLE = "STEP_NOT_AVAILABLE"
    SUPPLY_BELOW_ZERO = "SUPPLY_BELOW_ZERO"
    ROLE_NOT_ELIGIBLE = "ROLE_NOT_ELIGIBLE"
    VERSION_REQUIRED = "VERSION_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    MALFORMED_MODEL_OUTPUT = "MALFORMED_MODEL_OUTPUT"
    MODEL_OUTPUT_REJECTED = "MODEL_OUTPUT_REJECTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"


class EventType(StrEnum):
    CONFIG_RELEASE_ACTIVATED = "config.release_activated"
    ENVIRONMENT_CHANGE_PROPOSED = "environment.change_proposed"
    ENVIRONMENT_ACTIVATED = "environment.activated"
    ENVIRONMENT_CHANGE_REJECTED = "environment.change_rejected"
    OVERLAY_CHANGED = "overlay.changed"
    MISSION_EVENT_RECORDED = "mission.event_recorded"
    GROUND_LINK_CHANGED = "ground_link.changed"
    SENSOR_STATUS_CHANGED = "sensor.status_changed"
    SENSOR_QUIET = "sensor.quiet"
    SENSOR_RESUMED = "sensor.resumed"
    WARNING_RAISED = "warning.raised"
    WARNING_UPDATED = "warning.updated"
    WARNING_CLEARED = "warning.cleared"
    ROUTINE_OCCURRENCE_CHANGED = "routine.occurrence_changed"
    ROUTINE_OVERRIDE_CHANGED = "routine.override_changed"
    HELP_SESSION_CHANGED = "help.session_changed"
    HELP_BROADCAST_CHANGED = "help.broadcast_changed"
    GROUND_MESSAGE_CHANGED = "ground_message.changed"
    ASSISTANT_STATUS_CHANGED = "assistant.status_changed"
    INSIGHT_CHANGED = "insight.changed"
    JOB_PROGRESS = "job.progress"
    MEASUREMENT_REQUEST_CHANGED = "measurement_request.changed"
    HANDOFF_CHANGED = "handoff.changed"
    PARTICIPATION_STREAK_CHANGED = "participation.streak_changed"
    PARTICIPATION_CREW_GOAL_CHANGED = "participation.crew_goal_changed"
    PLAN_RUN_CHANGED = "plan_run.changed"
    PLAN_RUN_STEP_DONE = "plan_run.step_done"
    PLAN_RUN_ESCALATED = "plan_run.escalated"
    GUIDED_CHECK_BEAT = "guided_check.beat"
    GUIDED_CHECK_CHECKED = "guided_check.checked"
    COACHING_SAMPLE = "coaching.sample"
    ROLE_PROPOSED = "role.proposed"
    ROLE_CHANGED = "role.changed"
    SKILL_PRACTICED = "skill.practiced"
    SUPPLY_USED = "supply.used"
    DRILL_CHANGED = "drill.changed"


class ConfigName(StrEnum):
    ENVIRONMENT_HAZARDS = "environment-hazards"
    ROUTINE_SCHEDULE = "routine-schedule"
    RESPONDER_FLOWS = "responder-flows"
    SENSOR_CATALOG = "sensor-catalog"
    SENSOR_LIMITS = "sensor-limits"
    WARNING_RULES = "warning-rules"
    ROUTINE_RULES = "routine-rules"
    ENVIRONMENT_PROFILES = "environment-profiles"
    QUESTIONNAIRES = "questionnaires"
    INDICATOR_CATALOG = "indicator-catalog"
    HELP_PRESENTATION = "help-presentation"
    ASSISTANT_POLICY = "assistant-policy"
    UI_COPY = "ui-copy"
    PARTICIPATION_RULES = "participation-rules"
    KIT_MAP = "kit-map"
    ONBOARD_PLANS = "onboard-plans"
    SKILLS_CATALOG = "skills-catalog"
    ROLE_RULES = "role-rules"
    UNIT_MAPPING = "unit-mapping"


class Role(StrEnum):
    CREW = "crew"
    CREW_MEDICAL_OFFICER = "crew_medical_officer"
    GROUND = "ground"
    OPERATOR = "operator"
    STATION = "station"
    ADAPTER = "adapter"
    EDITOR = "editor"


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


ERROR_STATUS: Mapping[ErrorCode, int] = _Table(
    (
        (ErrorCode.MALFORMED_REQUEST, 400),
        (ErrorCode.UNAUTHENTICATED, 401),
        (ErrorCode.UNAUTHORIZED_SCOPE, 403),
        (ErrorCode.CONSENT_REQUIRED, 403),
        (ErrorCode.ACTION_NOT_ALLOWED, 403),
        (ErrorCode.ADAPTER_NOT_TRUSTED, 403),
        (ErrorCode.AGGREGATE_ONLY, 403),
        (ErrorCode.NOT_FOUND, 404),
        (ErrorCode.STALE_VERSION, 409),
        (ErrorCode.INVALID_TRANSITION, 409),
        (ErrorCode.DUPLICATE_COMMAND, 409),
        (ErrorCode.SESSION_CLOSED, 409),
        (ErrorCode.CONFIG_VERSION_MISMATCH, 409),
        (ErrorCode.CONFIRMATION_REQUIRED, 409),
        (ErrorCode.CURSOR_EXPIRED, 410),
        (ErrorCode.PAYLOAD_TOO_LARGE, 413),
        (ErrorCode.UNSUPPORTED_MEDIA_TYPE, 415),
        (ErrorCode.INVALID_UNIT, 422),
        (ErrorCode.INVALID_VALUE, 422),
        (ErrorCode.IDENTITY_MISMATCH, 422),
        (ErrorCode.ORIGIN_MISMATCH, 422),
        (ErrorCode.KIT_CANNOT_PERFORM, 422),
        (ErrorCode.CAPABILITY_UNAVAILABLE, 422),
        (ErrorCode.CONTEXT_INAPPLICABLE, 422),
        (ErrorCode.SOURCE_NOT_APPROVED, 422),
        (ErrorCode.REPORT_UNCONFIRMED, 422),
        (ErrorCode.LEVEL_NOT_ALLOWED, 422),
        (ErrorCode.APPROVAL_REQUIRED, 422),
        (ErrorCode.CONFIG_INVALID, 422),
        (ErrorCode.STEP_NOT_AVAILABLE, 422),
        (ErrorCode.SUPPLY_BELOW_ZERO, 422),
        (ErrorCode.ROLE_NOT_ELIGIBLE, 422),
        (ErrorCode.VERSION_REQUIRED, 428),
        (ErrorCode.INTERNAL_ERROR, 500),
        (ErrorCode.MALFORMED_MODEL_OUTPUT, 502),
        (ErrorCode.MODEL_OUTPUT_REJECTED, 502),
        (ErrorCode.MODEL_UNAVAILABLE, 503),
        (ErrorCode.MODEL_TIMEOUT, 504),
    )
)

ERROR_RETRYABLE: Mapping[ErrorCode, bool] = _Table(
    (
        (ErrorCode.MALFORMED_REQUEST, False),
        (ErrorCode.UNAUTHENTICATED, False),
        (ErrorCode.UNAUTHORIZED_SCOPE, False),
        (ErrorCode.CONSENT_REQUIRED, False),
        (ErrorCode.ACTION_NOT_ALLOWED, False),
        (ErrorCode.ADAPTER_NOT_TRUSTED, False),
        (ErrorCode.AGGREGATE_ONLY, False),
        (ErrorCode.NOT_FOUND, False),
        (ErrorCode.STALE_VERSION, False),
        (ErrorCode.INVALID_TRANSITION, False),
        (ErrorCode.DUPLICATE_COMMAND, False),
        (ErrorCode.SESSION_CLOSED, False),
        (ErrorCode.CONFIG_VERSION_MISMATCH, False),
        (ErrorCode.CONFIRMATION_REQUIRED, False),
        (ErrorCode.CURSOR_EXPIRED, False),
        (ErrorCode.PAYLOAD_TOO_LARGE, False),
        (ErrorCode.UNSUPPORTED_MEDIA_TYPE, False),
        (ErrorCode.INVALID_UNIT, False),
        (ErrorCode.INVALID_VALUE, False),
        (ErrorCode.IDENTITY_MISMATCH, False),
        (ErrorCode.ORIGIN_MISMATCH, False),
        (ErrorCode.KIT_CANNOT_PERFORM, False),
        (ErrorCode.CAPABILITY_UNAVAILABLE, False),
        (ErrorCode.CONTEXT_INAPPLICABLE, False),
        (ErrorCode.SOURCE_NOT_APPROVED, False),
        (ErrorCode.REPORT_UNCONFIRMED, False),
        (ErrorCode.LEVEL_NOT_ALLOWED, False),
        (ErrorCode.APPROVAL_REQUIRED, False),
        (ErrorCode.CONFIG_INVALID, False),
        (ErrorCode.STEP_NOT_AVAILABLE, False),
        (ErrorCode.SUPPLY_BELOW_ZERO, False),
        (ErrorCode.ROLE_NOT_ELIGIBLE, False),
        (ErrorCode.VERSION_REQUIRED, False),
        (ErrorCode.INTERNAL_ERROR, True),
        (ErrorCode.MALFORMED_MODEL_OUTPUT, False),
        (ErrorCode.MODEL_OUTPUT_REJECTED, False),
        (ErrorCode.MODEL_UNAVAILABLE, True),
        (ErrorCode.MODEL_TIMEOUT, True),
    )
)
