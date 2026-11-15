"""Validate contracted producer objects before crossing into the routine engine."""

from datetime import datetime

from crew_health.contracts_gen.enums import EventType
from crew_health.contracts_gen.schemas.activity_overlay_schema import ActivityOverlay
from crew_health.contracts_gen.schemas.environment_profile_schema import EnvironmentProfile
from crew_health.contracts_gen.schemas.mission_event_schema import MissionEvent
from crew_health.contracts_gen.schemas.warning_schema import Warning
from crew_health.kernel.uow import StoredEvent
from crew_health.routine.domain.bindings import (
    EnvironmentInput,
    MissionEventInput,
    OverlayInput,
    TriggerInput,
    WarningClearedInput,
    WarningRaisedInput,
)


def warning_input(raw: bytes, *, cleared: bool) -> WarningRaisedInput | WarningClearedInput:
    warning = Warning.model_validate_json(raw)
    if cleared:
        if warning.cleared_at is None or warning.clear_reason is None:
            raise ValueError("cleared warning requires a clear instant and reason")
        return WarningClearedInput(warning.warning_id.root, warning.clear_reason, warning.cleared_at.root)
    return WarningRaisedInput(
        warning.warning_id.root,
        warning.definition_id.root,
        warning.raised_by.root,
        warning.scope,
        None if warning.person_id is None else warning.person_id.root,
        None if warning.location is None else warning.location.root,
        warning.origin_mode.root,
        warning.raised_at.root,
        None if warning.insight_id is None else warning.insight_id.root,
        None if warning.previous_warning_id is None else warning.previous_warning_id.root,
        warning.environment_id.root,
    )


def overlay_input(raw: bytes) -> OverlayInput:
    overlay = ActivityOverlay.model_validate_json(raw)
    return OverlayInput(
        overlay.overlay_id.root,
        overlay.revision,
        overlay.person_id.root,
        overlay.overlay.root,
        overlay.state,
        None if overlay.planned_start is None else overlay.planned_start.root,
        None if overlay.started_at is None else overlay.started_at.root,
        None if overlay.ended_at is None else overlay.ended_at.root,
        None if overlay.location is None else overlay.location.root,
    )


def mission_event_input(raw: bytes) -> MissionEventInput:
    event = MissionEvent.model_validate_json(raw)
    return MissionEventInput(
        event.event_id.root,
        event.event_type.root,
        event.occurred_at.root,
        event.origin_mode.root,
        event.environment_id.root,
        tuple(person.root for person in event.person_ids),
        None if event.location is None else event.location.root,
    )


def environment_input(raw: bytes, at: datetime) -> EnvironmentInput:
    profile = EnvironmentProfile.model_validate_json(raw)
    activated_at = at if profile.activated_at is None else profile.activated_at.root
    return EnvironmentInput(profile.environment_id.root, activated_at)


def parse_input(event: StoredEvent) -> TriggerInput | None:
    if event.type in (EventType.WARNING_RAISED, EventType.WARNING_CLEARED):
        return warning_input(event.data_json, cleared=event.type is EventType.WARNING_CLEARED)
    if event.type is EventType.OVERLAY_CHANGED:
        return overlay_input(event.data_json)
    if event.type is EventType.MISSION_EVENT_RECORDED:
        return mission_event_input(event.data_json)
    if event.type is EventType.ENVIRONMENT_ACTIVATED:
        return environment_input(event.data_json, event.occurred_at)
    return None
